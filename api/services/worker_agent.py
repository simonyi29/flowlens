"""Outbound, authenticated command processor for a FlowLens worker."""
from __future__ import annotations

import asyncio
import json
import base64
import os
import hashlib
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from ..schemas.worker import WorkerCommand
from .task_store import TaskStore, task_store
from .worker_security import sanitize_worker_payload
from .worker_identity import WorkerIdentityManager

CommandHandler = Callable[[WorkerCommand], Awaitable[dict[str, Any]]]


class WorkerAgent:
    def __init__(self, worker_id: str, *, store: TaskStore = task_store,
                 identity: WorkerIdentityManager | None = None):
        self.worker_id = worker_id
        self.store = store
        self.handlers: dict[str, CommandHandler] = {}
        self._stop = asyncio.Event()
        self.identity = identity or WorkerIdentityManager()
        self.send_immediate: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.control_ws_url: str | None = None

    def register_handler(self, command_type: str, handler: CommandHandler) -> None:
        self.handlers[command_type] = handler

    async def process_command(self, raw: dict[str, Any]) -> dict[str, Any]:
        command = WorkerCommand.model_validate(raw)
        claimed = await self.store.claim_worker_command(command.command_id, command.type)
        if not claimed:
            return {"command_id": command.command_id, "status": "duplicate"}
        handler = self.handlers.get(command.type)
        if handler is None:
            result = {"command_id": command.command_id, "status": "failed", "error_type": "unsupported_command"}
        else:
            try:
                payload = sanitize_worker_payload(await handler(command))
                result = {"command_id": command.command_id, "status": "completed", "result": payload}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = {
                    "command_id": command.command_id, "status": "failed",
                    "error_type": getattr(exc, "error_type", "unknown"),
                    "error_message": str(exc)[:500],
                }
        if command.payload.get("run_id"):
            result["run_id"] = command.payload["run_id"]
        await self.store.enqueue_outbox("command.result", result)
        return result

    async def heartbeat(self) -> str:
        event = {"worker_id": self.worker_id, "status": "online", "browser_slots": 1}
        return await self.store.enqueue_outbox("worker.heartbeat", event)

    async def pending_messages(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = await self.store.pending_outbox(limit)
        return [{
            "sequence": row["sequence"], "event_id": row["event_id"],
            "event_type": row["event_type"], "payload": json.loads(row["payload_json"]),
        } for row in rows]

    def stop(self) -> None:
        self._stop.set()

    async def health_summary(self) -> dict[str, Any]:
        from .douyin_session_manager import douyin_browser_slot, profile_directory
        usage = shutil.disk_usage(profile_directory.root.parent if profile_directory.root.parent.exists() else self.store.path.parent)
        try:
            import faster_whisper  # noqa: F401
            asr_available = True
        except ImportError:
            asr_available = False
        return {
            "browser_slot_busy":douyin_browser_slot.lock.locked(),
            "free_disk_bytes":usage.free,
            "ffprobe_available":shutil.which("ffprobe") is not None,
            "asr_available":asr_available,
        }

    def configure_default_handlers(self) -> None:
        from .douyin_session_manager import (
            PlaywrightDouyinLoginBrowser, douyin_browser_slot, managed_profile_runtime,
            profile_directory, session_manager,
        )
        from .crawler_manager import crawler_manager
        from ..schemas.crawler import CrawlerStartRequest

        async def login_start(command: WorkerCommand):
            payload = command.payload
            required = ("login_session_id","connection_id","profile_id","tenant_hash")
            if any(not payload.get(key) for key in required): raise ValueError("incomplete login command")
            path = profile_directory.path_for(payload["tenant_hash"],payload["profile_id"])
            await self.store.save_browser_profile({
                "profile_id":payload["profile_id"],"connection_id":payload["connection_id"],
                "tenant_hash":payload["tenant_hash"],"status":"creating","profile_path":str(path),
            })
            await self.store.save_connection({
                "connection_id":payload["connection_id"],
                "user_id":payload["tenant_hash"],
                "worker_id":self.worker_id,
                "profile_id":payload["profile_id"],
                "status":"creating",
            })
            expires = payload.get("expires_at")
            if not expires:
                from datetime import datetime, timedelta, timezone
                expires = (datetime.now(timezone.utc)+timedelta(seconds=180)).isoformat()
            await self.store.save_login_session({
                "login_session_id":payload["login_session_id"],"connection_id":payload["connection_id"],
                "profile_id":payload["profile_id"],"status":"queued","expires_at":expires,
                "worker_id":self.worker_id,
            })
            task = session_manager.start_login(payload["login_session_id"])
            last_status = None; qr_digest = None
            while not task.done():
                item = await self.store.get_login_session(payload["login_session_id"])
                png = session_manager.qr.get(payload["login_session_id"])
                current_digest = hashlib.sha256(png).hexdigest() if png else None
                if png and current_digest != qr_digest and self.send_immediate:
                    await self.send_immediate({
                        "type":"qr.image","login_session_id":payload["login_session_id"],
                        "image_base64":base64.b64encode(png).decode(),
                    }); qr_digest = current_digest
                if item and item["status"] != last_status and self.send_immediate:
                    last_status = item["status"]
                    await self.send_immediate({"type":"login.status","login_session_id":payload["login_session_id"],"status":last_status})
                await asyncio.sleep(.5)
            await task
            item = await self.store.get_login_session(payload["login_session_id"])
            connection = await self.store.get_connection(payload["connection_id"])
            if self.send_immediate and item:
                await self.send_immediate({
                    "type":"login.status","login_session_id":payload["login_session_id"],"status":item["status"],
                    "creator_hash":connection.get("creator_hash") if connection else None,
                    "masked_nickname":connection.get("masked_nickname") if connection else None,
                    "error_type":item.get("error_type"),"message":item.get("error_message"),
                })
            return {"status":item["status"] if item else "failed"}

        async def login_cancel(command: WorkerCommand):
            return {"cancelled":await session_manager.cancel_login(str(command.payload.get("login_session_id") or ""))}

        async def session_check(command: WorkerCommand):
            profile_id = str(command.payload.get("profile_id") or "")
            connection_id = str(command.payload.get("connection_id") or "")
            profile = await self.store.get_browser_profile(profile_id)
            if not profile or profile.get("connection_id") != connection_id:
                raise ValueError("managed profile does not match connection")
            browser = PlaywrightDouyinLoginBrowser()
            try:
                async with douyin_browser_slot.lock:
                    await browser.start(profile_directory.ensure(profile["tenant_hash"], profile_id))
                    state = await browser.check_state()
                    status = {
                        "logged_in":"connected", "captcha_required":"verification_required",
                        "risk_controlled":"risk_controlled",
                    }.get(state.status, "session_expired")
                    await self.store.update_connection(
                        connection_id, status, creator_hash=state.creator_hash,
                        masked_nickname=state.masked_nickname,
                    )
                    return {
                        "connection_id":connection_id, "connection_status":status,
                        "creator_hash":state.creator_hash,
                        "masked_nickname":state.masked_nickname,
                    }
            finally:
                await browser.close()

        async def crawl_start(command: WorkerCommand):
            payload = command.payload
            config = dict(payload.get("config") or {})
            config.update({
                "platform":"dy", "connection_id":payload.get("connection_id"),
                "browser_profile_id":payload.get("browser_profile_id"),
                "worker_run_id":payload.get("run_id"), "browser_mode":"managed_profile",
            })
            local_run_id = await crawler_manager.enqueue(CrawlerStartRequest.model_validate(config))
            asyncio.create_task(self._monitor_crawl(payload.get("run_id") or local_run_id, local_run_id))
            return {"worker_run_id":local_run_id}

        async def crawl_pause(command: WorkerCommand): return {"ok":await crawler_manager.pause(str(command.payload.get("worker_run_id") or command.payload.get("run_id") or ""))}
        async def crawl_resume(command: WorkerCommand): return {"ok":await crawler_manager.resume(str(command.payload.get("worker_run_id") or command.payload.get("run_id") or ""))}
        async def crawl_cancel(command: WorkerCommand): return {"ok":await crawler_manager.cancel(str(command.payload.get("worker_run_id") or command.payload.get("run_id") or ""))}
        async def crawl_retry(command: WorkerCommand): return {"run_id":await crawler_manager.retry_failed(str(command.payload.get("worker_run_id") or command.payload.get("run_id") or ""))}

        async def profile_delete(command: WorkerCommand):
            profile_id = str(command.payload.get("profile_id") or "")
            profile = await self.store.get_browser_profile(profile_id)
            if not profile:
                return {"deleted":False}
            await managed_profile_runtime.stop(profile_id)
            deleted = await asyncio.to_thread(
                profile_directory.delete, profile["tenant_hash"], profile_id
            )
            await self.store.save_browser_profile({**profile, "status":"deleted", "pid":None, "cdp_port":None})
            return {"deleted":deleted}

        async def profile_close(command: WorkerCommand):
            profile_id = str(command.payload.get("profile_id") or "")
            await session_manager.close_idle_profile(profile_id)
            await managed_profile_runtime.stop(profile_id)
            return {"closed":True}

        async def media_open(command: WorkerCommand):
            if not self.control_ws_url:
                raise RuntimeError("media relay is not connected")
            from .media_relay import safe_media_path, parse_range
            item = await self.store.get_media(str(command.payload.get("asset_id") or ""))
            try:
                if not item: raise FileNotFoundError
                path = safe_media_path(item.get("path"))
                parse_range(command.payload.get("range"), path.stat().st_size)
            except (FileNotFoundError, PermissionError, ValueError) as exc:
                if self.send_immediate:
                    status = "forbidden" if isinstance(exc, PermissionError) else "invalid_range" if isinstance(exc, ValueError) else "not_found"
                    await self.send_immediate({"type":"media.status", "stream_id":command.payload.get("stream_id"), "status":status})
                return {"accepted":False}
            asyncio.create_task(self._stream_media(command.payload))
            return {"accepted":True, "stream_id":command.payload.get("stream_id")}

        async def media_delete(command: WorkerCommand):
            asset_id = str(command.payload.get("asset_id") or "")
            item = await self.store.get_media(asset_id)
            if not item:
                return {"asset_id":asset_id, "deleted":False, "status":"not_found"}
            from .media_relay import safe_media_path
            deleted_file = False
            if item.get("path"):
                path = safe_media_path(item["path"])
                if path.is_file():
                    await asyncio.to_thread(path.unlink)
                    deleted_file = True
            item.update({"asset_id":asset_id,"status":"deleted","path":None,"size_bytes":0})
            await self.store.upsert_media(item)
            return {"asset_id":asset_id,"deleted":True,"deleted_file":deleted_file,"status":"deleted"}

        self.register_handler("douyin.login.start",login_start)
        self.register_handler("douyin.login.refresh",login_start)
        self.register_handler("douyin.login.cancel",login_cancel)
        self.register_handler("douyin.session.check",session_check)
        self.register_handler("crawl.start",crawl_start)
        self.register_handler("crawl.pause",crawl_pause)
        self.register_handler("crawl.resume",crawl_resume)
        self.register_handler("crawl.cancel",crawl_cancel)
        self.register_handler("crawl.retry_failed",crawl_retry)
        self.register_handler("profile.delete",profile_delete)
        self.register_handler("profile.close",profile_close)
        self.register_handler("media.open",media_open)
        self.register_handler("media.delete",media_delete)

    async def _stream_media(self, payload: dict[str, Any]) -> None:
        import websockets
        from .media_relay import safe_media_path, parse_range
        item = await self.store.get_media(str(payload.get("asset_id") or ""))
        if not item:
            return
        try:
            path = safe_media_path(item.get("path"))
            size = path.stat().st_size
            start, end = parse_range(payload.get("range"), size)
        except (FileNotFoundError, PermissionError, ValueError):
            return
        base = str(self.control_ws_url).split("/internal/flowlens/workers/connect", 1)[0]
        url = f"{base}/internal/flowlens/workers/media/{payload['stream_id']}?worker_id={self.worker_id}"
        async with websockets.connect(url, max_size=2 * 1024 * 1024) as socket:
            challenge = json.loads(await socket.recv())
            nonce = base64.urlsafe_b64decode(challenge["nonce"].encode())
            await socket.send(json.dumps({"type":"authenticate", "signature":self.identity.sign(nonce)}))
            await socket.send(json.dumps({
                "type":"media.metadata", "size":size, "start":start, "end":end,
                "mime_type":item.get("mime_type") or "application/octet-stream",
            }))
            with path.open("rb") as handle:
                handle.seek(start); remaining = end - start + 1
                while remaining:
                    chunk = await asyncio.to_thread(handle.read, min(1024 * 1024, remaining))
                    if not chunk: break
                    remaining -= len(chunk)
                    await socket.send(chunk)
            await socket.send(json.dumps({"type":"media.complete"}))

    async def _monitor_crawl(self, remote_run_id: str, local_run_id: str) -> None:
        last = None
        sent_log_ids: set[int] = set()
        while True:
            item = await self.store.get_run(local_run_id)
            if not item: return
            for log in reversed(await self.store.list_logs(local_run_id, 500)):
                log_id = int(log["id"])
                if log_id in sent_log_ids: continue
                sent_log_ids.add(log_id)
                await self.store.enqueue_outbox("result.log", {
                    "worker_id":self.worker_id, "run_id":remote_run_id,
                    "entity_type":"log", "entity_id":str(log_id),
                    "payload":{
                        "level":log.get("level"), "message":log.get("message"),
                        "created_at":log.get("created_at"),
                    },
                    "observed_at":log.get("created_at"),
                })
            state = (item["status"],item["stage"],item.get("error_type"),item.get("error_message"))
            if state != last and self.send_immediate:
                await self.send_immediate({
                    "type":"crawl.status","run_id":remote_run_id,"worker_run_id":local_run_id,
                    "status":state[0],"stage":state[1],"error_type":state[2],"error_message":state[3],
                }); last = state
            if item["status"] in {"completed","failed","partial","cancelled","waiting_for_login","waiting_for_space"}: return
            await asyncio.sleep(1)

    async def run_once(self, control_ws_url: str) -> None:
        import websockets
        self.identity.load_or_create()
        self.control_ws_url = control_ws_url
        url = f"{control_ws_url}?worker_id={self.worker_id}"
        async with websockets.connect(url, max_size=2 * 1024 * 1024) as socket:
            challenge = json.loads(await socket.recv())
            nonce = base64.urlsafe_b64decode(challenge["nonce"].encode())
            await socket.send(json.dumps({"type":"authenticate","signature":self.identity.sign(nonce)}))
            authenticated = json.loads(await socket.recv())
            if authenticated.get("type") != "authenticated": raise RuntimeError("worker authentication failed")
            send_lock = asyncio.Lock()
            async def send(message):
                async with send_lock: await socket.send(json.dumps(sanitize_worker_payload(message),ensure_ascii=False))
            self.send_immediate = send
            async def heartbeat_loop():
                while True:
                    await send({"type":"heartbeat","worker_id":self.worker_id,
                                "capabilities":await self.health_summary()})
                    await asyncio.sleep(15)
            heartbeat = asyncio.create_task(heartbeat_loop())
            sent_results: set[str] = set()
            async def outbox_loop():
                while True:
                    for message in await self.pending_messages():
                        if message["event_id"] in sent_results:
                            continue
                        payload = message["payload"]
                        if message["event_type"].startswith("result."):
                            await send({
                                "type":"result.event","event_id":message["event_id"],
                                "run_id":payload.get("run_id"),"entity_type":payload.get("entity_type"),
                                "entity_id":payload.get("entity_id"),"payload":payload.get("payload") or {},
                                "observed_at":payload.get("observed_at"),
                            })
                        elif message["event_type"] == "command.result":
                            await send({"type":"command.result", "outbox_event_id":message["event_id"], **payload})
                        else:
                            continue
                        sent_results.add(message["event_id"])
                    await asyncio.sleep(1)
            outbox = asyncio.create_task(outbox_loop())
            try:
                async for raw in socket:
                    message = json.loads(raw)
                    if message.get("type") in {"result.ack", "outbox.ack"}:
                        await self.store.ack_outbox_event(str(message.get("event_id") or "")); continue
                    if message.get("type") != "command": continue
                    payload = dict(message.get("payload") or {})
                    payload.setdefault("command_id",message["event_id"])
                    result = await self.process_command(payload)
                    await send({"type":"ack","event_id":message["event_id"]})
                    await send({
                        "type":"command.result",
                        "run_id":payload.get("run_id"),
                        **result,
                    })
            finally:
                heartbeat.cancel(); outbox.cancel(); self.send_immediate = None

    async def run_forever(self, control_ws_url: str) -> None:
        delay = 1
        while not self._stop.is_set():
            try:
                await self.run_once(control_ws_url); delay = 1
            except asyncio.CancelledError: raise
            except Exception:
                await asyncio.sleep(delay); delay = min(delay * 2, 30)
