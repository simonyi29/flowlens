"""Administrator-managed ordinary user lifecycle."""
from __future__ import annotations

import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..services.auth import (
    Identity, generate_temporary_password, hash_password, iso, normalize_username,
    require_admin, require_csrf, temporary_password_seconds, utc_now, validate_display_name,
)
from ..services.task_store import task_store

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    max_douyin_connections: int = Field(default=3, ge=1, le=50)
    max_queued_tasks: int = Field(default=10, ge=1, le=1000)
    media_quota_bytes: int = Field(default=20 * 1024**3, ge=1024**2, le=10 * 1024**4)


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str | None = Field(default=None, min_length=3, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    max_douyin_connections: int | None = Field(default=None, ge=1, le=50)
    max_queued_tasks: int | None = Field(default=None, ge=1, le=1000)
    media_quota_bytes: int | None = Field(default=None, ge=1024**2, le=10 * 1024**4)


def _public_user(user: dict) -> dict:
    return {key: user.get(key) for key in (
        "user_id", "username", "display_name", "role", "status", "must_change_password",
        "max_douyin_connections", "max_queued_tasks", "media_quota_bytes", "created_at",
        "activated_at", "last_login_at", "suspended_at", "updated_at",
        "douyin_connection_count", "active_task_count", "media_usage_bytes",
    )}


async def _admin_with_csrf(
    identity: Identity = Depends(require_csrf),
) -> Identity:
    if identity.must_change_password or identity.status != "active" or identity.role != "admin":
        raise HTTPException(403, detail={
            "error_type": "admin_required", "user_message": "需要管理员权限。",
        })
    return identity


@router.get("")
async def list_users(
    search: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern="^(pending_activation|active|suspended)$"),
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
    _: Identity = Depends(require_admin),
):
    items = await task_store.list_users(search=search, status=status, limit=limit, offset=offset)
    total = await task_store.count_users(search=search, status=status)
    return {"items": [_public_user(item) for item in items], "total": total, "limit": limit, "offset": offset}


@router.post("")
async def create_user(
    payload: CreateUserRequest, request: Request, response: Response,
    admin: Identity = Depends(_admin_with_csrf),
):
    try:
        normalized = normalize_username(payload.username)
        display_name = validate_display_name(payload.display_name)
    except ValueError as exc:
        raise HTTPException(422, detail={"error_type": "validation_error", "user_message": str(exc)})
    temporary_password = generate_temporary_password()
    expires_at = iso(utc_now() + timedelta(seconds=temporary_password_seconds()))
    try:
        user = await task_store.create_user({
            "username": normalized, "normalized_username": normalized, "display_name": display_name,
            "password_hash": hash_password(temporary_password), "role": "user",
            "status": "pending_activation", "must_change_password": True,
            "temporary_password_expires_at": expires_at,
            "max_douyin_connections": payload.max_douyin_connections,
            "max_queued_tasks": payload.max_queued_tasks,
            "media_quota_bytes": payload.media_quota_bytes,
            "created_by_user_id": admin.user_id,
        })
    except sqlite3.IntegrityError:
        raise HTTPException(409, detail={
            "error_type": "username_exists", "user_message": "该用户名已存在。",
        })
    await task_store.add_audit_event(
        actor_user_id=admin.user_id, action="user.created", target_type="user",
        target_id=user["user_id"], context={"username": normalized},
        request_id=request.headers.get("x-request-id"),
    )
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return {
        "user": _public_user(user), "temporary_password": temporary_password,
        "temporary_password_expires_at": expires_at,
    }


@router.get("/{user_id}")
async def get_user(user_id: str, _: Identity = Depends(require_admin)):
    user = await task_store.get_user_resource_summary(user_id)
    if not user:
        raise HTTPException(404, "user not found")
    # Resource summaries do not expose content, comments, subtitles, media paths or IDs.
    return _public_user(user)


@router.patch("/{user_id}")
async def update_user(
    user_id: str, payload: UpdateUserRequest,
    admin: Identity = Depends(_admin_with_csrf),
):
    user = await task_store.get_user(user_id)
    if not user:
        raise HTTPException(404, "user not found")
    username = normalized = None
    if payload.username is not None:
        try:
            username = normalized = normalize_username(payload.username)
        except ValueError as exc:
            raise HTTPException(422, detail={"error_type": "validation_error", "user_message": str(exc)})
    display_name = None
    if payload.display_name is not None:
        try:
            display_name = validate_display_name(payload.display_name)
        except ValueError as exc:
            raise HTTPException(422, detail={"error_type": "validation_error", "user_message": str(exc)})
    try:
        updated = await task_store.update_user_profile(
            user_id, username=username, normalized_username=normalized, display_name=display_name,
            max_douyin_connections=payload.max_douyin_connections,
            max_queued_tasks=payload.max_queued_tasks, media_quota_bytes=payload.media_quota_bytes,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, detail={"error_type": "username_exists", "user_message": "该用户名已存在。"})
    return _public_user(updated)


@router.post("/{user_id}/reset-temporary-password")
async def reset_temporary_password(
    user_id: str, response: Response, admin: Identity = Depends(_admin_with_csrf),
):
    user = await task_store.get_user(user_id)
    if not user:
        raise HTTPException(404, "user not found")
    if user["role"] == "admin" and user_id != admin.user_id:
        raise HTTPException(403, "administrator passwords must be reset locally")
    if user["role"] == "admin":
        raise HTTPException(403, "use the local reset_admin_password command")
    temporary_password = generate_temporary_password()
    expires_at = iso(utc_now() + timedelta(seconds=temporary_password_seconds()))
    await task_store.set_user_password(
        user_id, hash_password(temporary_password), temporary_expires_at=expires_at,
        must_change_password=True,
    )
    await task_store.revoke_user_sessions(user_id, "temporary_password_reset")
    await task_store.add_audit_event(
        actor_user_id=admin.user_id, action="user.temporary_password_reset",
        target_type="user", target_id=user_id,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return {"temporary_password": temporary_password, "temporary_password_expires_at": expires_at}


@router.post("/{user_id}/revoke-sessions")
async def revoke_sessions(user_id: str, admin: Identity = Depends(_admin_with_csrf)):
    if not await task_store.get_user(user_id):
        raise HTTPException(404, "user not found")
    count = await task_store.revoke_user_sessions(user_id, "admin_revoked")
    await task_store.add_audit_event(
        actor_user_id=admin.user_id, action="user.sessions_revoked", target_type="user", target_id=user_id,
    )
    return {"status": "ok", "revoked_sessions": count}


@router.post("/{user_id}/suspend")
async def suspend_user(user_id: str, admin: Identity = Depends(_admin_with_csrf)):
    if user_id == admin.user_id:
        raise HTTPException(409, detail={"error_type": "self_suspend_forbidden", "user_message": "管理员不能暂停自己的账号。"})
    user = await task_store.get_user(user_id)
    if not user:
        raise HTTPException(404, "user not found")
    if user["role"] == "admin":
        raise HTTPException(403, "administrator accounts cannot be managed here")
    active_runs = await task_store.list_user_remote_runs(user_id, 1000, 0)
    from .remote import _enqueue_worker_command
    for run in active_runs:
        if run["status"] in {"queued", "running", "pausing", "waiting_for_login", "waiting_for_space"}:
            await _enqueue_worker_command(run["worker_id"], "crawl.pause", {
                "run_id": run["run_id"], "worker_run_id": run.get("worker_run_id"),
                "reason": "account_suspended",
            }, 120)
    await task_store.set_user_status(user_id, "suspended")
    await task_store.revoke_user_sessions(user_id, "account_suspended")
    paused = await task_store.pause_user_remote_runs(user_id)
    await task_store.add_audit_event(
        actor_user_id=admin.user_id, action="user.suspended", target_type="user", target_id=user_id,
        context={"paused_tasks": paused},
    )
    return {"status": "suspended", "paused_tasks": paused}


@router.post("/{user_id}/restore")
async def restore_user(user_id: str, admin: Identity = Depends(_admin_with_csrf)):
    user = await task_store.get_user(user_id)
    if not user:
        raise HTTPException(404, "user not found")
    if user["role"] == "admin":
        raise HTTPException(403, "administrator accounts cannot be managed here")
    next_status = "pending_activation" if user["must_change_password"] else "active"
    await task_store.set_user_status(user_id, next_status)
    await task_store.add_audit_event(
        actor_user_id=admin.user_id, action="user.restored", target_type="user", target_id=user_id,
    )
    return {"status": next_status}
