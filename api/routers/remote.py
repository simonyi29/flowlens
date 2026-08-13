"""Trusted-proxy API surface used to integrate FlowLens with an existing site."""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..services.task_store import task_store
from ..services import douyin_session_manager

router = APIRouter(prefix="/flowlens", tags=["remote-flowlens"])


class LoginSessionRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)


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


def _tenant_hash(user_id: str) -> str:
    key = os.getenv("FLOWLENS_TENANT_HASH_KEY") or os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    return hashlib.sha256(f"{key}:{user_id}".encode()).hexdigest()[:16]


@router.get("/workers")
async def workers(_: str = Depends(current_user)):
    return {"items": await task_store.list_workers()}


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


@router.get("/douyin/connections")
async def connections(user_id: str = Depends(current_user)):
    return {"items": await task_store.list_user_connections(user_id)}
