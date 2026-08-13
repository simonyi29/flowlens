"""Managed, tenant-isolated Douyin browser sessions for remote workers."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


profile_directory = ProfileDirectory()
qr_store = EphemeralQrStore()
douyin_browser_slot = BrowserSlot.create()

