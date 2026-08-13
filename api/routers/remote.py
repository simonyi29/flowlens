"""Trusted-proxy API surface used to integrate FlowLens with an existing site."""
from __future__ import annotations

import hashlib
import hmac
import json
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..services.task_store import task_store
from ..services import douyin_session_manager
from ..services.media_relay import media_relay_broker
from ..services.remote_events import remote_event_hub

router = APIRouter(prefix="/flowlens", tags=["remote-flowlens"])


class LoginSessionRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)


class RemoteCrawlRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=128)
    crawler_type: Literal["search", "detail", "creator", "topic"] = "search"
    keywords: str = ""
    specified_ids: str = ""
    creator_ids: str = ""
    topics: str = ""
    max_notes_count: int = Field(default=20, ge=1, le=10000)
    enable_comments: bool = True
    enable_sub_comments: bool = True
    max_comments_count: int = Field(default=0, ge=0, le=10000)
    enable_creator_profile: bool = True
    enable_native_subtitle: bool = True
    enable_asr: bool = True
    download_media: bool = False
    max_media_downloads: int = Field(default=15, ge=0, le=10000)
    max_media_total_bytes: int = Field(default=5 * 1024**3, ge=1)
    incremental: bool = False


def _enabled() -> bool:
    return os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1", "true", "yes"}


async def current_user(
    x_flowlens_proxy_token: str | None = Header(None),
    x_flowlens_user_id: str | None = Header(None),
) -> str:
    if not _enabled():
        raise HTTPException(404, "remote worker mode is disabled")
    expected = os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    if not expected or not x_flowlens_proxy_token or not hmac.compare_digest(expected, x_flowlens_proxy_token):
        raise HTTPException(401, "trusted proxy authentication required")
    if not x_flowlens_user_id or len(x_flowlens_user_id) > 128:
        raise HTTPException(401, "authenticated user context required")
    return x_flowlens_user_id


async def current_admin(
    user_id: str = Depends(current_user),
    x_flowlens_role: str | None = Header(None),
) -> str:
    if x_flowlens_role != "admin":
        raise HTTPException(403, "administrator role required")
    return user_id


@router.websocket("/events")
async def remote_events(websocket: WebSocket):
    if not _enabled():
        await websocket.close(code=1008); return
    expected = os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    token = websocket.headers.get("x-flowlens-proxy-token", "")
    user_id = websocket.headers.get("x-flowlens-user-id", "")
    if not expected or not user_id or not hmac.compare_digest(expected, token):
        await websocket.close(code=1008); return
    await websocket.accept()
    queue = remote_event_hub.subscribe(user_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        remote_event_hub.unsubscribe(user_id, queue)


def _tenant_hash(user_id: str) -> str:
    key = os.getenv("FLOWLENS_TENANT_HASH_KEY") or os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    return hashlib.sha256(f"{key}:{user_id}".encode()).hexdigest()[:16]


@router.get("/workers")
async def workers(_: str = Depends(current_user)):
    return {"items": await task_store.list_workers()}


@router.post("/admin/worker-enrollments")
async def create_worker_enrollment(_: str = Depends(current_admin)):
    code = await task_store.create_worker_enrollment(600)
    return {"enrollment_code":code, "expires_in_seconds":600}


@router.post("/douyin/login-sessions")
async def create_login_session(request: LoginSessionRequest, user_id: str = Depends(current_user)):
    await task_store.initialize()
    worker = await task_store.get_worker(request.worker_id)
    if not worker or worker["status"] != "online":
        raise HTTPException(503, "worker is offline")
    login_session_id, connection_id, profile_id = f"ls_{uuid.uuid4().hex}", f"conn_{uuid.uuid4().hex}", uuid.uuid4().hex
    tenant_hash = _tenant_hash(user_id)
    profile_path = douyin_session_manager.profile_directory.path_for(tenant_hash, profile_id)
    await task_store.save_browser_profile({
        "profile_id": profile_id, "connection_id": connection_id, "tenant_hash": tenant_hash,
        "status": "creating", "profile_path": str(profile_path),
    })
    await task_store.save_connection({
        "connection_id": connection_id, "user_id": user_id, "worker_id": request.worker_id,
        "profile_id": profile_id, "status": "creating",
    })
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=180)
    await task_store.save_login_session({
        "login_session_id": login_session_id, "connection_id": connection_id,
        "profile_id": profile_id, "status": "queued", "expires_at": expires_at.isoformat(),
        "user_id": user_id, "worker_id": request.worker_id,
    })
    await task_store.enqueue_outbox("worker.command", {
        "type": "douyin.login.start", "login_session_id": login_session_id,
        "connection_id": connection_id, "profile_id": profile_id, "tenant_hash": tenant_hash,
        "worker_id": request.worker_id,
    })
    return {"login_session_id": login_session_id, "status": "queued", "expires_at": expires_at.isoformat()}


@router.get("/douyin/login-sessions/{login_session_id}")
async def login_session(login_session_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_login_session(login_session_id, user_id)
    if not item:
        raise HTTPException(404, "login session not found")
    return {
        "login_session_id": item["login_session_id"], "status": item["status"],
        "expires_at": item["expires_at"],
        "qr_available": douyin_session_manager.qr_store.get(login_session_id) is not None,
        "error_type": item["error_type"], "message": item["error_message"],
    }


@router.get("/douyin/login-sessions/{login_session_id}/qr")
async def login_qr(login_session_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_login_session(login_session_id, user_id)
    if not item:
        raise HTTPException(404, "login session not found")
    png = douyin_session_manager.qr_store.get(login_session_id)
    if png is None:
        raise HTTPException(404, "login QR is not available")
    return Response(
        png, media_type="image/png",
        headers={"Cache-Control":"no-store, private", "Pragma":"no-cache", "X-Content-Type-Options":"nosniff"},
    )


@router.post("/douyin/login-sessions/{login_session_id}/refresh")
async def refresh_login(login_session_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_login_session(login_session_id, user_id)
    if not item:
        raise HTTPException(404, "login session not found")
    if item["status"] == "logged_in":
        raise HTTPException(409, "connection is already logged in")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=180)
    await task_store.save_login_session({**item, "status":"queued", "expires_at":expires_at.isoformat()})
    douyin_session_manager.qr_store.delete(login_session_id)
    profile = await task_store.get_browser_profile(item["profile_id"])
    if not profile:
        raise HTTPException(409, "browser profile is unavailable")
    await task_store.enqueue_outbox("worker.command", {
        "type":"douyin.login.refresh", "login_session_id":login_session_id,
        "worker_id":item["worker_id"], "profile_id":item["profile_id"],
        "connection_id":item["connection_id"], "tenant_hash":profile["tenant_hash"],
        "expires_at":expires_at.isoformat(),
    })
    return {"status":"queued", "expires_at":expires_at.isoformat()}


@router.post("/douyin/login-sessions/{login_session_id}/cancel")
async def cancel_login(login_session_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_login_session(login_session_id, user_id)
    if not item:
        raise HTTPException(404, "login session not found")
    await task_store.update_login_session(login_session_id, "cancelled")
    douyin_session_manager.qr_store.delete(login_session_id)
    await task_store.enqueue_outbox("worker.command", {
        "type":"douyin.login.cancel", "login_session_id":login_session_id,
        "worker_id":item["worker_id"], "profile_id":item["profile_id"],
    })
    return {"status":"cancelled"}


@router.get("/douyin/connections")
async def connections(user_id: str = Depends(current_user)):
    return {"items": await task_store.list_user_connections(user_id)}


@router.get("/douyin/connections/{connection_id}")
async def connection(connection_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_connection(connection_id, user_id)
    if not item:
        raise HTTPException(404, "Douyin connection not found")
    return {key:item.get(key) for key in (
        "connection_id", "worker_id", "status", "creator_hash",
        "masked_nickname", "last_verified_at", "created_at",
    )}


@router.delete("/douyin/connections/{connection_id}")
async def disconnect_connection(connection_id: str, confirm: bool = False,
                                user_id: str = Depends(current_user)):
    if not confirm:
        raise HTTPException(409, "explicit confirmation is required")
    item = await task_store.get_user_connection(connection_id, user_id)
    if not item:
        raise HTTPException(404, "Douyin connection not found")
    runs = await task_store.list_user_remote_runs(user_id)
    active = {"queued", "running", "pausing", "paused", "waiting_for_login", "waiting_for_space"}
    if any(run["connection_id"] == connection_id and run["status"] in active for run in runs):
        raise HTTPException(409, "connection has an unfinished crawl run")
    await task_store.enqueue_outbox("worker.command", {
        "type":"profile.delete", "worker_id":item["worker_id"],
        "profile_id":item["profile_id"], "connection_id":connection_id,
    })
    await task_store.update_connection(connection_id, "disconnected")
    return {"connection_id":connection_id, "status":"disconnected"}


@router.post("/crawl-runs")
async def create_crawl_run(request: RemoteCrawlRequest, user_id: str = Depends(current_user)):
    connection = await task_store.get_user_connection(request.connection_id, user_id)
    if not connection:
        raise HTTPException(404, "Douyin connection not found")
    if connection["status"] != "connected":
        raise HTTPException(409, "Douyin connection is not ready")
    worker = await task_store.get_worker(connection["worker_id"])
    if not worker or worker["status"] != "online":
        raise HTTPException(503, "worker is offline")
    config = request.model_dump(exclude={"connection_id"})
    if request.crawler_type == "topic" and not request.topics.strip():
        raise HTTPException(422, "topics is required in topic mode")
    run_id = await task_store.create_remote_run({
        "user_id":user_id, "connection_id":request.connection_id,
        "worker_id":connection["worker_id"], "config":config,
    })
    await task_store.enqueue_outbox("worker.command", {
        "type":"crawl.start", "worker_id":connection["worker_id"], "run_id":run_id,
        "connection_id":request.connection_id, "browser_profile_id":connection["profile_id"],
        "config":config,
    })
    return {"run_id":run_id, "status":"queued"}


@router.get("/crawl-runs")
async def crawl_runs(user_id: str = Depends(current_user)):
    return {"items":await task_store.list_user_remote_runs(user_id)}


@router.get("/crawl-runs/{run_id}")
async def crawl_run(run_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_remote_run(run_id, user_id)
    if not item: raise HTTPException(404, "crawl run not found")
    return item


async def _control_run(run_id: str, action: str, user_id: str):
    item = await task_store.get_user_remote_run(run_id, user_id)
    if not item: raise HTTPException(404, "crawl run not found")
    await task_store.enqueue_outbox("worker.command", {
        "type":f"crawl.{action}", "worker_id":item["worker_id"], "run_id":run_id,
        "worker_run_id":item.get("worker_run_id"),
    })
    target = {"pause":"pausing", "resume":"queued", "cancel":"cancelled", "retry_failed":"queued"}[action]
    await task_store.update_remote_run(run_id, target)
    return {"run_id":run_id, "status":target}


@router.post("/crawl-runs/{run_id}/pause")
async def pause_run(run_id: str, user_id: str = Depends(current_user)): return await _control_run(run_id,"pause",user_id)

@router.post("/crawl-runs/{run_id}/resume")
async def resume_run(run_id: str, user_id: str = Depends(current_user)): return await _control_run(run_id,"resume",user_id)

@router.post("/crawl-runs/{run_id}/cancel")
async def cancel_run(run_id: str, user_id: str = Depends(current_user)): return await _control_run(run_id,"cancel",user_id)

@router.post("/crawl-runs/{run_id}/retry-failed")
async def retry_run(run_id: str, user_id: str = Depends(current_user)): return await _control_run(run_id,"retry_failed",user_id)


@router.get("/results/{entity_type}")
async def remote_results(entity_type: str, limit: int = 50, offset: int = 0,
                         user_id: str = Depends(current_user)):
    allowed = {"aweme", "creator", "topic", "comment", "transcript", "aweme_metric", "creator_metric", "media"}
    if entity_type not in allowed:
        raise HTTPException(422, "unsupported result entity type")
    rows = await task_store.list_user_remote_results(
        user_id, entity_type, min(max(limit, 1), 500), max(offset, 0)
    )
    for row in rows:
        row["payload"] = json.loads(row.pop("payload_json"))
    return {"items":rows}


@router.get("/media/{asset_id}/stream")
async def stream_remote_media(asset_id: str, range_header: str | None = Header(None, alias="Range"),
                              user_id: str = Depends(current_user)):
    result = await task_store.get_user_remote_result(user_id, "media", asset_id)
    if not result:
        raise HTTPException(404, "media asset not found")
    worker = await task_store.get_worker(result["worker_id"])
    if not worker or worker["status"] != "online":
        raise HTTPException(503, "worker is offline")
    try:
        session = media_relay_broker.create(worker["worker_id"], asset_id, range_header)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc))
    await task_store.enqueue_outbox("worker.command", {
        "type":"media.open", "worker_id":worker["worker_id"], "stream_id":session.stream_id,
        "asset_id":asset_id, "range":range_header,
    })
    try:
        metadata = await asyncio.wait_for(session.ready, timeout=30)
    except (asyncio.TimeoutError, Exception):
        media_relay_broker.close(session.stream_id)
        raise HTTPException(503, "worker did not open media stream")

    async def chunks():
        try:
            while True:
                chunk = await session.queue.get()
                if chunk is None: break
                yield chunk
        finally:
            media_relay_broker.close(session.stream_id)

    start, end, size = int(metadata["start"]), int(metadata["end"]), int(metadata["size"])
    headers = {"Accept-Ranges":"bytes", "Content-Length":str(end-start+1)}
    status = 200
    if range_header:
        status = 206; headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status,
        media_type=metadata.get("mime_type") or "application/octet-stream", headers=headers)
