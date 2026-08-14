"""Trusted-proxy API surface used to integrate FlowLens with an existing site."""
from __future__ import annotations

import hashlib
import json
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..services.task_store import task_store
from ..services.product_views import present_run, safe_error
from ..services import douyin_session_manager
from ..services.media_relay import MediaRelayOpenError, media_relay_broker
from ..services.remote_events import remote_event_hub
from ..services.auth import (
    Identity, identity_from_token, optional_current_identity, require_password_changed,
    require_csrf, SESSION_COOKIE,
)

router = APIRouter(prefix="/flowlens", tags=["remote-flowlens"])


class LoginSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=200)


class RemoteCrawlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    asr_model: str = Field(default="small", min_length=1, max_length=100)
    asr_language: str = Field(default="zh", min_length=1, max_length=20)
    download_media: bool = False
    download_video: bool = True
    download_images: bool = True
    download_cover: bool = True
    download_music: bool = False
    media_quality: Literal["best_h264"] = "best_h264"
    max_media_downloads: int = Field(default=15, ge=0, le=10000)
    max_media_total_bytes: int = Field(default=5 * 1024**3, ge=1, le=1024**4)
    media_library_max_bytes: int = Field(default=20 * 1024**3, ge=1, le=1024**4)
    min_free_disk_bytes: int = Field(default=10 * 1024**3, ge=0, le=1024**4)
    skip_existing_media: bool = True
    verify_media: bool = True
    keep_asr_source_media: bool = False
    incremental: bool = False
    stop_after_existing: int = Field(default=5, ge=1, le=100)
    refresh_existing_metrics: bool = True
    refresh_existing_comments: bool = False


def _enabled() -> bool:
    return os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1", "true", "yes"}


async def current_user(
    request: Request,
    x_flowlens_proxy_token: str | None = Header(None),
    x_flowlens_user_id: str | None = Header(None),
) -> str:
    if not _enabled():
        raise HTTPException(404, "remote worker mode is disabled")
    identity = await optional_current_identity(request)
    if identity is not None:
        await require_password_changed(identity)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            await require_csrf(request, identity)
        return identity.user_id
    # Migration-only compatibility for a private trusted backend. It is
    # disabled by default and must never be used by browser JavaScript.
    compat = os.getenv("FLOWLENS_TRUSTED_HEADER_COMPAT", "false").lower() in {"1", "true", "yes"}
    expected = os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    if not compat or not expected or not x_flowlens_proxy_token:
        raise HTTPException(401, "server-side session authentication required")
    import hmac
    if not hmac.compare_digest(expected, x_flowlens_proxy_token):
        raise HTTPException(401, "server-side session authentication required")
    if not x_flowlens_user_id or len(x_flowlens_user_id) > 128:
        raise HTTPException(401, "authenticated user context required")
    return x_flowlens_user_id


async def current_admin(
    request: Request,
    x_flowlens_proxy_token: str | None = Header(None),
    x_flowlens_user_id: str | None = Header(None),
    x_flowlens_role: str | None = Header(None),
) -> str:
    identity = await optional_current_identity(request)
    if identity is not None:
        await require_password_changed(identity)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            await require_csrf(request, identity)
        if identity.role != "admin":
            raise HTTPException(403, "administrator role required")
        return identity.user_id
    if os.getenv("FLOWLENS_TRUSTED_HEADER_COMPAT", "false").lower() in {"1", "true", "yes"} and x_flowlens_role == "admin":
        return await current_user(request, x_flowlens_proxy_token, x_flowlens_user_id)
    raise HTTPException(403, "administrator role required")


@router.websocket("/events")
async def remote_events(websocket: WebSocket):
    if not _enabled():
        await websocket.close(code=1008); return
    session_token = websocket.cookies.get(SESSION_COOKIE)
    identity = await identity_from_token(session_token)
    if not identity or identity.status != "active" or identity.must_change_password:
        await websocket.close(code=1008); return
    user_id = identity.user_id
    await websocket.accept()
    queue = remote_event_hub.subscribe(user_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                current = await identity_from_token(session_token, touch=False)
                if not current or current.status != "active" or current.must_change_password:
                    await websocket.close(code=1008)
                    break
                await websocket.send_json({"event":"session.heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        remote_event_hub.unsubscribe(user_id, queue)


def _tenant_hash(user_id: str) -> str:
    key = os.getenv("FLOWLENS_TENANT_HASH_KEY") or os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    return hashlib.sha256(f"{key}:{user_id}".encode()).hexdigest()[:16]


async def _enqueue_worker_command(worker_id: str, command_type: str, payload: dict,
                                  ttl_seconds: int = 300) -> str:
    issued = datetime.now(timezone.utc)
    command_id = f"cmd_{uuid.uuid4().hex}"
    return await task_store.enqueue_outbox("worker.command", {
        "worker_id":worker_id,
        "command_id":command_id,
        "protocol_version":"1.0",
        "type":command_type,
        "issued_at":issued.isoformat(),
        "expires_at":(issued + timedelta(seconds=ttl_seconds)).isoformat(),
        "payload":payload,
    })


@router.get("/workers")
async def workers(_: str = Depends(current_user)):
    items = await task_store.list_workers()
    return {"items":[{key:item.get(key) for key in ("worker_id","name","status","version")}
                     for item in items]}


@router.get("/admin/workers")
async def admin_workers(_: str = Depends(current_admin)):
    items = await task_store.list_workers()
    for item in items:
        item["capabilities"] = json.loads(item.pop("capabilities_json") or "{}")
    return {"items":items}


@router.get("/admin/queue")
async def admin_global_queue(
    limit: int = 100, offset: int = 0, _: str = Depends(current_admin),
):
    rows = await task_store.list_remote_runs_global(min(max(limit, 1), 200), max(offset, 0))
    return {"items": [{
        "run_id": row["run_id"], "user_display_name": row.get("user_display_name") or "未知用户",
        "account_label": row.get("display_name") or row.get("remark") or row.get("masked_nickname") or "抖音账号",
        "crawler_type": row.get("crawler_type") or "unknown", "stage": row["stage"],
        "status": row["status"], "worker_name": row.get("worker_name") or "未分配",
        "created_at": row["created_at"],
    } for row in rows], "total": await task_store.count_remote_runs_global()}


@router.post("/admin/queue/{run_id}/pause")
async def admin_pause_queue_item(run_id: str, admin_user_id: str = Depends(current_admin)):
    run = await task_store.get_remote_run(run_id)
    if not run:
        raise HTTPException(404, "crawl run not found")
    if run["status"] not in {"queued", "running", "pausing", "waiting_for_login", "waiting_for_space"}:
        raise HTTPException(409, "crawl run cannot be paused in its current state")
    await _enqueue_worker_command(run["worker_id"], "crawl.pause", {
        "run_id":run_id, "worker_run_id":run.get("worker_run_id"),
        "reason":"admin_emergency_pause",
    }, 120)
    await task_store.update_remote_run(run_id, "pausing")
    await task_store.add_audit_event(
        actor_user_id=admin_user_id, action="admin.task_emergency_paused",
        target_type="remote_crawl_run", target_id=run_id,
    )
    return {"run_id":run_id,"status":"pausing"}


@router.get("/admin/verifications")
async def admin_verifications(_: str = Depends(current_admin)):
    rows = await task_store.list_connections_requiring_verification()
    return {"items":[{
        "connection_id":row["connection_id"],
        "user_display_name":row.get("user_display_name") or "未知用户",
        "account_label":row.get("display_name") or row.get("remark") or row.get("masked_nickname") or "抖音账号",
        "status":row["status"], "worker_name":row.get("worker_name") or "未分配",
        "worker_status":row.get("worker_status") or "offline",
        "last_verified_at":row.get("last_verified_at"), "updated_at":row.get("updated_at"),
    } for row in rows]}


@router.post("/admin/verifications/{connection_id}/recheck")
async def admin_recheck_verification(
    connection_id: str, admin_user_id: str = Depends(current_admin),
):
    connection = await task_store.get_connection(connection_id)
    if not connection:
        raise HTTPException(404, "Douyin connection not found")
    worker = await task_store.get_worker(connection["worker_id"])
    if not worker or worker["status"] != "online":
        raise HTTPException(503, "worker is offline")
    await _enqueue_worker_command(connection["worker_id"], "douyin.session.check", {
        "connection_id":connection_id, "profile_id":connection["profile_id"],
    }, 180)
    await task_store.add_audit_event(
        actor_user_id=admin_user_id, action="admin.verification_recheck",
        target_type="douyin_connection", target_id=connection_id,
    )
    return {"connection_id":connection_id,"status":"checking"}


@router.post("/admin/worker-enrollments")
async def create_worker_enrollment(admin_user_id: str = Depends(current_admin)):
    code = await task_store.create_worker_enrollment(600)
    await task_store.add_audit_event(
        actor_user_id=admin_user_id, action="admin.worker_enrollment_created",
        target_type="worker_enrollment", target_id=None,
    )
    return {"enrollment_code":code, "expires_in_seconds":600}


@router.delete("/admin/workers/{worker_id}")
async def revoke_worker(worker_id: str, confirm: bool = False, admin_user_id: str = Depends(current_admin)):
    if not confirm:
        raise HTTPException(409, "explicit confirmation is required")
    if not await task_store.revoke_worker(worker_id):
        raise HTTPException(404, "worker not found")
    await task_store.add_audit_event(
        actor_user_id=admin_user_id, action="admin.worker_revoked",
        target_type="worker", target_id=worker_id,
    )
    return {"worker_id":worker_id, "status":"revoked"}


@router.get("/admin/browser/status")
async def admin_browser_status(_: str = Depends(current_admin)):
    profiles = await task_store.list_browser_profile_status()
    # CDP ports are intentionally reduced to a boolean in the central/admin API.
    return {"slot_busy":douyin_session_manager.douyin_browser_slot.lock.locked(), "profiles":[{
        "profile_id":item["profile_id"], "connection_id":item["connection_id"],
        "status":item["status"], "process_running":item.get("pid") is not None,
        "last_checked_at":item.get("last_checked_at"), "updated_at":item.get("updated_at"),
    } for item in profiles]}


@router.post("/admin/browser/{connection_id}/close")
async def admin_close_browser(connection_id: str, admin_user_id: str = Depends(current_admin)):
    connection = await task_store.get_connection(connection_id)
    if not connection: raise HTTPException(404, "Douyin connection not found")
    await _enqueue_worker_command(connection["worker_id"], "profile.close", {
        "profile_id":connection["profile_id"], "connection_id":connection_id,
    }, 120)
    await task_store.add_audit_event(
        actor_user_id=admin_user_id, action="admin.browser_closed",
        target_type="douyin_connection", target_id=connection_id,
    )
    return {"connection_id":connection_id, "status":"closing"}


@router.post("/douyin/login-sessions")
async def create_login_session(request: LoginSessionRequest, user_id: str = Depends(current_user)):
    await task_store.initialize()
    user = await task_store.get_user(user_id)
    if not user:
        raise HTTPException(401, "user not found")
    if await task_store.count_active_connections(user_id) >= int(user["max_douyin_connections"]):
        raise HTTPException(409, detail={
            "error_type":"connection_quota_reached",
            "user_message":f"已达到 {user['max_douyin_connections']} 个抖音账号上限。",
        })
    workers = [item for item in await task_store.list_workers() if item["status"] == "online"]
    worker = workers[0] if workers else None
    if not worker:
        raise HTTPException(503, "worker is offline")
    worker_id = worker["worker_id"]
    login_session_id, connection_id, profile_id = f"ls_{uuid.uuid4().hex}", f"conn_{uuid.uuid4().hex}", uuid.uuid4().hex
    tenant_hash = _tenant_hash(user_id)
    profile_path = douyin_session_manager.profile_directory.path_for(tenant_hash, profile_id)
    await task_store.save_browser_profile({
        "profile_id": profile_id, "connection_id": connection_id, "tenant_hash": tenant_hash,
        "status": "creating", "profile_path": str(profile_path),
    })
    await task_store.save_connection({
        "connection_id": connection_id, "user_id": user_id, "worker_id": worker_id,
        "profile_id": profile_id, "status": "creating",
    })
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=180)
    await task_store.save_login_session({
        "login_session_id": login_session_id, "connection_id": connection_id,
        "profile_id": profile_id, "status": "queued", "expires_at": expires_at.isoformat(),
        "user_id": user_id, "worker_id": worker_id,
    })
    await _enqueue_worker_command(worker_id, "douyin.login.start", {
        "login_session_id": login_session_id,
        "connection_id": connection_id, "profile_id": profile_id, "tenant_hash": tenant_hash,
        "expires_at":expires_at.isoformat(),
    }, 180)
    await task_store.add_audit_event(
        actor_user_id=user_id, action="connection.created", target_type="douyin_connection",
        target_id=connection_id, context={"worker_id":worker_id},
    )
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
    await _enqueue_worker_command(item["worker_id"], "douyin.login.refresh", {
        "login_session_id":login_session_id, "profile_id":item["profile_id"],
        "connection_id":item["connection_id"], "tenant_hash":profile["tenant_hash"],
        "expires_at":expires_at.isoformat(),
    }, 180)
    return {"status":"queued", "expires_at":expires_at.isoformat()}


@router.post("/douyin/login-sessions/{login_session_id}/cancel")
async def cancel_login(login_session_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_login_session(login_session_id, user_id)
    if not item:
        raise HTTPException(404, "login session not found")
    await task_store.update_login_session(login_session_id, "cancelled")
    douyin_session_manager.qr_store.delete(login_session_id)
    await _enqueue_worker_command(item["worker_id"], "douyin.login.cancel", {
        "login_session_id":login_session_id, "profile_id":item["profile_id"],
    }, 120)
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
        "masked_nickname", "display_name", "remark", "last_verified_at", "created_at", "updated_at",
    )}


@router.patch("/douyin/connections/{connection_id}")
async def update_connection_label(
    connection_id: str, payload: ConnectionUpdateRequest,
    user_id: str = Depends(current_user),
):
    item = await task_store.get_user_connection(connection_id, user_id)
    if not item:
        raise HTTPException(404, "Douyin connection not found")
    display_name = payload.display_name.strip() if payload.display_name else None
    remark = payload.remark.strip() if payload.remark is not None else None
    await task_store.update_connection_labels(connection_id, display_name=display_name, remark=remark)
    return await connection(connection_id, user_id)


@router.post("/douyin/connections/{connection_id}/login-session")
async def reconnect_connection(connection_id: str, user_id: str = Depends(current_user)):
    connection = await task_store.get_user_connection(connection_id, user_id)
    if not connection:
        raise HTTPException(404, "Douyin connection not found")
    worker = await task_store.get_worker(connection["worker_id"])
    profile = await task_store.get_browser_profile(connection["profile_id"])
    if not worker or worker["status"] != "online": raise HTTPException(503, "worker is offline")
    if not profile: raise HTTPException(409, "browser profile is unavailable")
    login_session_id = f"ls_{uuid.uuid4().hex}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=180)
    await task_store.save_login_session({
        "login_session_id":login_session_id, "connection_id":connection_id,
        "profile_id":connection["profile_id"], "status":"queued", "expires_at":expires_at.isoformat(),
        "user_id":user_id, "worker_id":connection["worker_id"],
    })
    await task_store.update_connection(connection_id, "creating")
    await _enqueue_worker_command(connection["worker_id"], "douyin.login.start", {
        "login_session_id":login_session_id, "connection_id":connection_id,
        "profile_id":connection["profile_id"], "tenant_hash":profile["tenant_hash"],
        "expires_at":expires_at.isoformat(),
    }, 180)
    return {"login_session_id":login_session_id, "status":"queued", "expires_at":expires_at.isoformat()}


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
    await _enqueue_worker_command(item["worker_id"], "profile.delete", {
        "profile_id":item["profile_id"], "connection_id":connection_id,
    }, 120)
    await task_store.update_connection(connection_id, "disconnected")
    await task_store.add_audit_event(
        actor_user_id=user_id, action="connection.disconnected", target_type="douyin_connection",
        target_id=connection_id,
    )
    return {"connection_id":connection_id, "status":"disconnected"}


@router.post("/crawl-runs")
async def create_crawl_run(request: RemoteCrawlRequest, user_id: str = Depends(current_user)):
    user = await task_store.get_user(user_id)
    if not user or user["status"] != "active":
        raise HTTPException(423, "account is not active")
    if await task_store.count_active_remote_runs(user_id) >= int(user["max_queued_tasks"]):
        raise HTTPException(409, detail={
            "error_type":"task_quota_reached",
            "user_message":f"排队或运行中的任务已达到 {user['max_queued_tasks']} 个上限。",
        })
    connection = await task_store.get_user_connection(request.connection_id, user_id)
    if not connection:
        raise HTTPException(404, "Douyin connection not found")
    if connection["status"] != "connected":
        raise HTTPException(409, "Douyin connection is not ready")
    worker = await task_store.get_worker(connection["worker_id"])
    if not worker or worker["status"] != "online":
        raise HTTPException(503, "worker is offline")
    config = request.model_dump(exclude={"connection_id"})
    if request.download_media:
        resources = await task_store.get_user_resource_summary(user_id)
        used_bytes = int((resources or {}).get("media_usage_bytes") or 0)
        reserved_bytes = await task_store.reserved_user_media_bytes(user_id)
        available_bytes = int(user["media_quota_bytes"]) - used_bytes - reserved_bytes
        if available_bytes <= 0:
            raise HTTPException(409, detail={
                "error_type":"media_quota_reached",
                "user_message":"媒体空间配额已用完，请联系管理员调整配额。",
            })
        # The server, not the browser, owns the effective per-task reservation.
        config["max_media_total_bytes"] = min(request.max_media_total_bytes, available_bytes)
        config["media_library_max_bytes"] = min(
            request.media_library_max_bytes,
            int(user["media_quota_bytes"]),
        )
    if request.crawler_type == "topic" and not request.topics.strip():
        raise HTTPException(422, "topics is required in topic mode")
    run_id = await task_store.create_remote_run({
        "user_id":user_id, "connection_id":request.connection_id,
        "worker_id":connection["worker_id"], "config":config,
    })
    await _enqueue_worker_command(connection["worker_id"], "crawl.start", {
        "run_id":run_id,
        "connection_id":request.connection_id, "browser_profile_id":connection["profile_id"],
        "config":config,
    }, 86_400)
    await task_store.add_audit_event(
        actor_user_id=user_id, action="task.created", target_type="remote_crawl_run",
        target_id=run_id, context={"crawler_type":request.crawler_type, "connection_id":request.connection_id},
    )
    return {"run_id":run_id, "status":"queued"}


@router.get("/crawl-runs")
async def crawl_runs(
    limit: int = 10,
    offset: int = 0,
    status: str | None = None,
    connection_id: str | None = None,
    user_id: str = Depends(current_user),
):
    valid_statuses = {
        "queued", "running", "pausing", "paused", "waiting_for_login",
        "waiting_for_space", "partial", "completed", "failed", "cancelled",
    }
    if status and status not in valid_statuses:
        raise HTTPException(422, "Unsupported task status")
    safe_limit = min(max(limit, 1), 100)
    safe_offset = max(offset, 0)
    if connection_id and not await task_store.get_user_connection(connection_id, user_id):
        raise HTTPException(404, "Douyin connection not found")
    rows = await task_store.list_user_remote_runs(user_id, safe_limit, safe_offset, status, connection_id)
    connections = {item["connection_id"]: item for item in await task_store.list_user_connections(user_id)}
    return {"items":[present_run(
        item,
        account_label=(connections.get(item["connection_id"], {}).get("display_name")
                       or connections.get(item["connection_id"], {}).get("remark")
                       or connections.get(item["connection_id"], {}).get("masked_nickname")
                       or "抖音账号"),
        remote=True,
    ) for item in rows],
        "total": await task_store.count_user_remote_runs(user_id, status, connection_id),
        "status_counts": await task_store.user_remote_run_status_counts(user_id),
        "limit": safe_limit,
        "offset": safe_offset,
    }


@router.get("/crawl-runs/{run_id}")
async def crawl_run(run_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_remote_run(run_id, user_id)
    if not item: raise HTTPException(404, "crawl run not found")
    connection = await task_store.get_user_connection(item["connection_id"], user_id)
    return {
        **present_run(
            item,
            account_label=((connection or {}).get("display_name") or (connection or {}).get("remark")
                           or (connection or {}).get("masked_nickname") or "抖音账号"),
            remote=True,
        ),
        "error": safe_error(item.get("error_type"), item.get("error_message")),
    }


@router.get("/crawl-runs/{run_id}/items")
async def crawl_run_items(run_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_remote_run(run_id, user_id)
    if not item: raise HTTPException(404, "crawl run not found")
    rows = []
    for entity_type in ("aweme", "creator", "topic", "comment", "transcript", "media"):
        results = await task_store.list_user_remote_results(user_id, entity_type, 500, 0)
        rows.extend({"entity_type":entity_type, "entity_id":row["entity_id"], "synced_at":row["synced_at"]}
                    for row in results if row["run_id"] == run_id)
    return {"items":rows}


@router.get("/crawl-runs/{run_id}/logs")
async def crawl_run_logs(run_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_remote_run(run_id, user_id)
    if not item: raise HTTPException(404, "crawl run not found")
    results = await task_store.list_user_remote_results(user_id, "log", 500, 0)
    return {"logs":[row for row in results if row["run_id"] == run_id]}


async def _control_run(run_id: str, action: str, user_id: str):
    item = await task_store.get_user_remote_run(run_id, user_id)
    if not item: raise HTTPException(404, "crawl run not found")
    await _enqueue_worker_command(item["worker_id"], f"crawl.{action}", {
        "run_id":run_id,
        "worker_run_id":item.get("worker_run_id"),
    }, 120)
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


@router.post("/crawl-runs/{run_id}/rerun")
async def rerun_remote_crawl(run_id: str, user_id: str = Depends(current_user)):
    item = await task_store.get_user_remote_run(run_id, user_id)
    if not item:
        raise HTTPException(404, "crawl run not found")
    if item["status"] not in {"completed", "cancelled", "partial"}:
        raise HTTPException(409, "crawl run cannot be run again")
    connection = await task_store.get_user_connection(item["connection_id"], user_id)
    if not connection or connection["status"] != "connected":
        raise HTTPException(409, "Douyin connection is not ready")
    worker = await task_store.get_worker(connection["worker_id"])
    if not worker or worker["status"] != "online":
        raise HTTPException(503, "worker is offline")
    config = json.loads(item["sanitized_config_json"])
    new_run_id = await task_store.create_remote_run({
        "user_id": user_id,
        "connection_id": item["connection_id"],
        "worker_id": connection["worker_id"],
        "config": config,
    })
    await _enqueue_worker_command(connection["worker_id"], "crawl.start", {
        "run_id": new_run_id,
        "connection_id": item["connection_id"],
        "browser_profile_id": connection["profile_id"],
        "config": config,
    }, 86_400)
    return {"status": "queued", "run_id": new_run_id, "source_run_id": run_id}


@router.delete("/crawl-runs/{run_id}")
async def delete_remote_crawl_history(
    run_id: str,
    confirm: bool = False,
    user_id: str = Depends(current_user),
):
    item = await task_store.get_user_remote_run(run_id, user_id)
    if not item:
        raise HTTPException(404, "crawl run not found")
    if not confirm:
        raise HTTPException(409, "explicit confirmation is required")
    if not await task_store.delete_user_remote_run_history(run_id, user_id):
        raise HTTPException(409, "only finished crawl history can be deleted")
    return {"status": "deleted", "run_id": run_id, "results_preserved": True}


@router.post("/crawl-runs/{run_id}/continue-after-verification")
async def continue_after_verification(run_id: str, user_id: str = Depends(current_user)):
    return await _control_run(run_id, "resume", user_id)


@router.get("/results/{entity_type}")
async def remote_results(entity_type: str, limit: int = 50, offset: int = 0,
                         connection_id: str | None = None,
                         user_id: str = Depends(current_user)):
    allowed = {"aweme", "creator", "topic", "comment", "transcript", "aweme_metric", "creator_metric", "media", "log"}
    if entity_type not in allowed:
        raise HTTPException(422, "unsupported result entity type")
    safe_limit = min(max(limit, 1), 500)
    safe_offset = max(offset, 0)
    rows, total = await asyncio.gather(
        task_store.list_user_remote_results(user_id, entity_type, safe_limit, safe_offset, connection_id),
        task_store.count_user_remote_results(user_id, entity_type, connection_id),
    )
    connections = {item["connection_id"]: item for item in await task_store.list_user_connections(user_id)}
    for row in rows:
        row["payload"] = json.loads(row.pop("payload_json"))
        connection = connections.get(row.get("connection_id"), {})
        row["account_label"] = (connection.get("display_name") or connection.get("remark")
                                or connection.get("masked_nickname") or "抖音账号")
    return {"items": rows, "total": total, "limit": safe_limit, "offset": safe_offset}


@router.get("/results/aweme/{aweme_id}/detail")
async def remote_aweme_detail(aweme_id: str, user_id: str = Depends(current_user)):
    aweme = await task_store.get_user_remote_result(user_id, "aweme", aweme_id)
    if not aweme:
        raise HTTPException(404, "aweme result not found")

    def payload(row: dict) -> dict:
        value = json.loads(row.get("payload_json") or "{}")
        return value if isinstance(value, dict) else {}

    aweme_payload = payload(aweme)
    comments, transcripts, metrics, media = await asyncio.gather(
        task_store.list_user_remote_results(user_id, "comment", 500, 0),
        task_store.list_user_remote_results(user_id, "transcript", 100, 0),
        task_store.list_user_remote_results(user_id, "aweme_metric", 500, 0),
        task_store.list_user_remote_results(user_id, "media", 500, 0),
    )
    comment_payloads = []
    for row in comments:
        item = payload(row)
        if str(item.get("aweme_id") or "") == aweme_id:
            comment_payloads.append(item)
    roots, children = [], {}
    for item in comment_payloads:
        if int(item.get("level") or 1) == 1:
            roots.append(item)
        else:
            children.setdefault(str(item.get("root_comment_id") or item.get("parent_comment_id") or ""), []).append(item)
    for root in roots:
        root["replies"] = children.get(str(root.get("comment_id") or ""), [])
    transcript = None
    for row in transcripts:
        item = payload(row)
        if str(row.get("entity_id") or item.get("aweme_id") or "") == aweme_id:
            transcript = item
            break
    metric_payloads = []
    for row in metrics:
        item = payload(row)
        if str(item.get("aweme_id") or "") == aweme_id:
            metric_payloads.append(item)
    media_payloads = []
    for row in media:
        item = payload(row)
        if str(item.get("aweme_id") or "") == aweme_id:
            item.setdefault("asset_id", row.get("entity_id"))
            media_payloads.append(item)
    return {
        "aweme": aweme_payload,
        "transcript": transcript,
        "metrics": metric_payloads,
        "comments": roots,
        "media": media_payloads,
    }


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
    await _enqueue_worker_command(worker["worker_id"], "media.open", {
        "stream_id":session.stream_id,
        "asset_id":asset_id, "range":range_header,
    }, 30)
    try:
        metadata = await asyncio.wait_for(session.ready, timeout=30)
    except MediaRelayOpenError as exc:
        media_relay_broker.close(session.stream_id)
        if exc.status == "not_found": raise HTTPException(404, "media file not found")
        if exc.status == "forbidden": raise HTTPException(403, "media file is not authorized")
        if exc.status == "invalid_range": raise HTTPException(416, "invalid byte range")
        raise HTTPException(503, "worker could not open media stream")
    except Exception:
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


@router.delete("/media/{asset_id}")
async def delete_remote_media(
    asset_id: str, confirm: bool = False, user_id: str = Depends(current_user),
):
    if not confirm:
        raise HTTPException(409, "explicit confirmation is required")
    result = await task_store.get_user_remote_result(user_id, "media", asset_id)
    if not result:
        raise HTTPException(404, "media asset not found")
    payload = json.loads(result.get("payload_json") or "{}")
    if payload.get("status") == "deleted":
        return {"asset_id":asset_id,"status":"deleted"}
    worker = await task_store.get_worker(result["worker_id"])
    if not worker or worker["status"] != "online":
        raise HTTPException(503, "worker is offline")
    await task_store.update_remote_media_status(
        asset_id, result["worker_id"], "deleting", user_id=user_id,
    )
    await _enqueue_worker_command(result["worker_id"], "media.delete", {
        "asset_id":asset_id, "run_id":result["run_id"],
    }, 120)
    return {"asset_id":asset_id,"status":"deleting"}
