"""Managed, tenant-isolated Douyin browser sessions for remote workers."""
from __future__ import annotations

import asyncio
import re
import shutil
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from tools.browser_launcher import BrowserLauncher
from tools.user_hash import mask_nickname
from .task_store import TaskStore, task_store

ROOT = Path(__file__).resolve().parents[2]
PROFILE_ROOT = ROOT / "data" / "flowlens" / "browser" / "dy"
TENANT_RE = re.compile(r"^[a-f0-9]{16}$")
PROFILE_RE = re.compile(r"^[a-f0-9]{32}$")


class ProfileDirectory:
    def __init__(self, root: Path = PROFILE_ROOT):
        self.root = root.resolve()

    def path_for(self, tenant_hash: str, profile_id: str) -> Path:
        if not TENANT_RE.fullmatch(tenant_hash) or not PROFILE_RE.fullmatch(profile_id):
            raise ValueError("invalid tenant or profile identifier")
        path = (self.root / tenant_hash / profile_id / "profile").resolve()
        if self.root not in path.parents:
            raise ValueError("profile path escapes managed root")
        return path

    def ensure(self, tenant_hash: str, profile_id: str) -> Path:
        path = self.path_for(tenant_hash, profile_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def profile_root(self, tenant_hash: str, profile_id: str) -> Path:
        return self.path_for(tenant_hash, profile_id).parent

    def delete(self, tenant_hash: str, profile_id: str) -> bool:
        target = self.profile_root(tenant_hash, profile_id).resolve()
        if self.root not in target.parents or target == self.root:
            raise ValueError("profile path escapes managed root")
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True


class EphemeralQrStore:
    """In-memory QR bytes; intentionally has no filesystem persistence path."""
    def __init__(self, ttl_seconds: int = 180, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._items: dict[str, tuple[float, bytes]] = {}

    def put(self, login_session_id: str, png: bytes) -> None:
        self._items[login_session_id] = (self.clock() + self.ttl_seconds, bytes(png))

    def get(self, login_session_id: str) -> bytes | None:
        item = self._items.get(login_session_id)
        if not item:
            return None
        expires_at, png = item
        if self.clock() >= expires_at:
            self.delete(login_session_id)
            return None
        return png

    def delete(self, login_session_id: str) -> None:
        self._items.pop(login_session_id, None)


@dataclass
class BrowserSlot:
    """One fair process-local slot shared by login and crawl browser operations."""
    lock: asyncio.Lock

    @classmethod
    def create(cls) -> "BrowserSlot":
        return cls(asyncio.Lock())


@dataclass
class BrowserSessionState:
    status: str
    creator_hash: str | None = None
    masked_nickname: str | None = None
    error_type: str | None = None
    message: str | None = None


class LoginBrowser(Protocol):
    async def start(self, profile_path: Path) -> dict: ...
    async def open_login_qr(self) -> bytes: ...
    async def check_state(self) -> BrowserSessionState: ...
    async def close(self) -> None: ...


class PlaywrightDouyinLoginBrowser:
    """A headed Chrome session that never exports cookies or full-page screenshots."""
    QR_SELECTORS = (
        "[class*='login'] canvas", "[class*='login'] img[src^='data:image']",
        "[class*='qrcode'] canvas", "[class*='qrcode'] img",
    )

    def __init__(self):
        self.launcher = BrowserLauncher()
        self.playwright = self.browser = self.context = self.page = None
        self.port: int | None = None

    async def start(self, profile_path: Path) -> dict:
        from playwright.async_api import async_playwright
        paths = self.launcher.detect_browser_paths()
        if not paths:
            raise RuntimeError("chrome_start_failed: Chrome or Edge not found")
        profile_path.mkdir(parents=True, exist_ok=True)
        self.port = self.launcher.find_available_port(9222)
        process = self.launcher.launch_browser(paths[0], self.port, False, str(profile_path), "https://www.douyin.com/")
        ready = await asyncio.to_thread(self.launcher.wait_for_browser_ready, self.port, 60)
        if not ready:
            raise RuntimeError("chrome_start_failed: CDP did not become ready")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()
        if "douyin.com" not in str(self.page.url):
            await self.page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=45_000)
        return {"pid": process.pid, "cdp_port": self.port}

    async def open_login_qr(self) -> bytes:
        assert self.page is not None
        for text in ("登录", "扫码登录"):
            locator = self.page.get_by_text(text, exact=True)
            if await locator.count():
                try:
                    await locator.first.click(timeout=3_000)
                    break
                except Exception:
                    pass
        for _ in range(20):
            for selector in self.QR_SELECTORS:
                locator = self.page.locator(selector)
                if await locator.count() and await locator.first.is_visible():
                    png = await locator.first.screenshot(type="png")
                    if len(png) > 100:
                        return png
            await asyncio.sleep(.5)
        raise RuntimeError("qr_not_found: Douyin login QR was not found")

    async def check_state(self) -> BrowserSessionState:
        assert self.page is not None and self.context is not None
        url = str(self.page.url).lower()
        text = (await self.page.locator("body").inner_text(timeout=5_000)).lower()
        if any(token in url or token in text for token in ("captcha", "验证码", "安全验证")):
            return BrowserSessionState("captcha_required", error_type="captcha_required", message="管理员需要在抓取机完成验证")
        if any(token in text for token in ("访问频繁", "风险", "异常请求")):
            return BrowserSessionState("risk_controlled", error_type="risk_controlled", message="抖音触发风险控制")
        cookies = await self.context.cookies(["https://www.douyin.com/"])
        has_session = any(cookie.get("name") in {"sessionid", "sessionid_ss", "sid_guard", "passport_csrf_token"} for cookie in cookies)
        login_buttons = await self.page.get_by_text("登录", exact=True).count()
        if has_session and login_buttons == 0:
            nickname = ""
            for selector in ("[class*='avatar'] img[alt]", "[data-e2e='user-avatar'] img[alt]"):
                locator = self.page.locator(selector)
                if await locator.count():
                    nickname = (await locator.first.get_attribute("alt")) or ""
                    if nickname: break
            return BrowserSessionState("logged_in", masked_nickname=mask_nickname(nickname) if nickname else "已连接账号")
        qr_visible = False
        for selector in self.QR_SELECTORS:
            locator = self.page.locator(selector)
            if await locator.count() and await locator.first.is_visible():
                qr_visible = True; break
        return BrowserSessionState("qr_ready" if qr_visible else "qr_scanned")

    async def close(self) -> None:
        try:
            if self.browser and self.browser.is_connected():
                await self.browser.close()
        finally:
            if self.playwright:
                await self.playwright.stop()
            self.launcher.cleanup()


class ManagedDouyinSessionManager:
    TERMINAL = {"logged_in", "captcha_required", "risk_controlled", "expired", "cancelled", "failed"}

    def __init__(self, *, store: TaskStore = task_store, profiles: ProfileDirectory | None = None,
                 qr: EphemeralQrStore | None = None, browser_factory: Callable[[], LoginBrowser] = PlaywrightDouyinLoginBrowser,
                 poll_interval: float = 1.0):
        self.store = store
        self.profiles = profiles or profile_directory
        self.qr = qr or qr_store
        self.browser_factory = browser_factory
        self.poll_interval = poll_interval
        self.tasks: dict[str, asyncio.Task] = {}

    def start_login(self, login_session_id: str) -> asyncio.Task:
        existing = self.tasks.get(login_session_id)
        if existing and not existing.done():
            return existing
        task = asyncio.create_task(self.run_login(login_session_id))
        self.tasks[login_session_id] = task
        return task

    async def run_login(self, login_session_id: str) -> None:
        item = await self.store.get_login_session(login_session_id)
        if not item:
            raise ValueError("login session not found")
        profile = await self.store.get_browser_profile(item["profile_id"])
        if not profile:
            raise ValueError("browser profile not found")
        browser = self.browser_factory()
        try:
            async with douyin_browser_slot.lock:
                await self.store.update_login_session(login_session_id, "starting_browser")
                path = self.profiles.ensure(profile["tenant_hash"], profile["profile_id"])
                info = await browser.start(path)
                await self.store.save_browser_profile({**profile, "status":"running", **info})
                await self.store.update_login_session(login_session_id, "opening_login_page")
                png = await browser.open_login_qr()
                self.qr.put(login_session_id, png)
                await self.store.update_login_session(login_session_id, "qr_ready")
                while True:
                    latest = await self.store.get_login_session(login_session_id)
                    if not latest or latest["status"] == "cancelled":
                        self.qr.delete(login_session_id); return
                    expires = datetime.fromisoformat(latest["expires_at"])
                    if expires.tzinfo is None: expires = expires.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) >= expires:
                        await self.store.update_login_session(login_session_id, "expired")
                        self.qr.delete(login_session_id); return
                    state = await browser.check_state()
                    if state.status != latest["status"]:
                        await self.store.update_login_session(login_session_id, state.status, error_type=state.error_type, error_message=state.message)
                    if state.status == "logged_in":
                        await self.store.update_connection(item["connection_id"], "connected", creator_hash=state.creator_hash, masked_nickname=state.masked_nickname)
                        self.qr.delete(login_session_id); return
                    if state.status in {"captcha_required", "risk_controlled"}:
                        await self.store.update_connection(item["connection_id"], "verification_required" if state.status == "captcha_required" else "risk_controlled")
                        self.qr.delete(login_session_id); return
                    await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            await self.store.update_login_session(login_session_id, "cancelled")
            self.qr.delete(login_session_id)
            raise
        except Exception as exc:
            await self.store.update_login_session(login_session_id, "failed", error_type=getattr(exc, "error_type", "unknown"), error_message=str(exc)[:500])
            self.qr.delete(login_session_id)
        finally:
            await browser.close()

    async def cancel_login(self, login_session_id: str) -> bool:
        item = await self.store.get_login_session(login_session_id)
        if not item or item["status"] in self.TERMINAL:
            return False
        await self.store.update_login_session(login_session_id, "cancelled")
        task = self.tasks.get(login_session_id)
        if task and not task.done(): task.cancel()
        self.qr.delete(login_session_id)
        return True


class ManagedProfileRuntime:
    """Launches a stored profile for a crawler child process to attach over loopback CDP."""
    def __init__(self, *, store: TaskStore = task_store, profiles: ProfileDirectory | None = None):
        self.store = store
        self.profiles = profiles or profile_directory
        self.launchers: dict[str, BrowserLauncher] = {}

    async def start(self, profile_id: str) -> int:
        profile = await self.store.get_browser_profile(profile_id)
        if not profile:
            raise ValueError("managed browser profile not found")
        path = self.profiles.ensure(profile["tenant_hash"], profile_id)
        launcher = BrowserLauncher()
        paths = launcher.detect_browser_paths()
        if not paths:
            raise RuntimeError("chrome_start_failed: Chrome or Edge not found")
        port = launcher.find_available_port(9222)
        process = launcher.launch_browser(paths[0], port, False, str(path), "https://www.douyin.com/")
        if not await asyncio.to_thread(launcher.wait_for_browser_ready, port, 60):
            launcher.cleanup()
            raise RuntimeError("chrome_start_failed: managed profile CDP did not become ready")
        self.launchers[profile_id] = launcher
        await self.store.save_browser_profile({**profile, "status":"running", "pid":process.pid, "cdp_port":port})
        return port

    async def stop(self, profile_id: str) -> None:
        launcher = self.launchers.pop(profile_id, None)
        if launcher:
            await asyncio.to_thread(launcher.cleanup)
        profile = await self.store.get_browser_profile(profile_id)
        if profile:
            await self.store.save_browser_profile({**profile, "status":"idle", "pid":None, "cdp_port":None})


profile_directory = ProfileDirectory()
qr_store = EphemeralQrStore()
douyin_browser_slot = BrowserSlot.create()
session_manager = ManagedDouyinSessionManager()
managed_profile_runtime = ManagedProfileRuntime()
