"""Browser authentication API for FlowLens remote website mode."""
from __future__ import annotations

import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..services.auth import (
    Identity, clear_session_cookie, create_session, csrf_token_for_session,
    hash_password, iso, normalize_username, opaque_hash, parse_datetime,
    remote_mode, require_authenticated_user, require_csrf, set_session_cookie,
    temporary_password_seconds, utc_now, validate_origin, validate_password,
    verify_password,
)
from ..services.task_store import task_store

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)


def _identity_payload(identity: Identity) -> dict:
    return {
        "user": {
            "user_id": identity.user_id,
            "username": identity.username,
            "display_name": identity.display_name,
            "role": identity.role,
            "status": identity.status,
            "must_change_password": identity.must_change_password,
            "max_douyin_connections": identity.max_douyin_connections,
            "max_queued_tasks": identity.max_queued_tasks,
            "media_quota_bytes": identity.media_quota_bytes,
        },
        "csrf_token": identity.csrf_token,
        "capabilities": {
            "admin_console": identity.role == "admin" and not identity.must_change_password,
            "multiple_douyin_connections": True,
        },
    }


def _source_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For here unless an explicit proxy layer validates it.
    return request.client.host if request.client else "unknown"


async def _locked(username_hash: str, source_ip_hash: str) -> bool:
    window = int(os.getenv("FLOWLENS_LOGIN_LOCK_SECONDS", "900"))
    maximum = int(os.getenv("FLOWLENS_LOGIN_MAX_FAILURES", "5"))
    attempts = await task_store.recent_login_attempts(
        username_hash, source_ip_hash, iso(utc_now() - timedelta(seconds=window))
    )
    username_failures = 0
    source_failures = 0
    count_username = True
    count_source = True
    for attempt in attempts:
        if count_username and attempt["username_hash"] == username_hash:
            if attempt["success"]:
                count_username = False
            else:
                username_failures += 1
        if count_source and attempt["source_ip_hash"] == source_ip_hash:
            if attempt["success"]:
                count_source = False
            else:
                source_failures += 1
        if username_failures >= maximum or source_failures >= maximum:
            return True
        if not count_username and not count_source:
            break
    return False


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    if not remote_mode():
        raise HTTPException(404, "website login is disabled in local mode")
    validate_origin(request)
    try:
        normalized = normalize_username(payload.username)
    except ValueError:
        normalized = payload.username.strip().lower()[:64]
    username_hash = opaque_hash(normalized, "login-username")
    source_ip_hash = opaque_hash(_source_ip(request), "login-ip")
    if await _locked(username_hash, source_ip_hash):
        raise HTTPException(429, detail={
            "error_type": "login_locked", "user_message": "登录失败次数过多，请 15 分钟后重试。",
            "recoverable": True,
        })
    user = await task_store.get_user_by_username(normalized)
    valid = bool(user and verify_password(user["password_hash"], payload.password))
    temp_expired = bool(
        user and user.get("must_change_password") and
        (parse_datetime(user.get("temporary_password_expires_at")) or utc_now()) <= utc_now()
    )
    if not valid or temp_expired:
        await task_store.record_login_attempt(username_hash, source_ip_hash, False)
        await task_store.add_audit_event(
            actor_user_id=user["user_id"] if user else None, action="user.login_failed",
            target_type="user", target_id=user["user_id"] if user else None, result="failed",
            context={"username_hash": username_hash[:16]}, request_id=request.headers.get("x-request-id"),
        )
        raise HTTPException(401, detail={
            "error_type": "invalid_credentials", "user_message": "用户名或密码错误。",
            "recoverable": True,
        })
    if user["status"] == "suspended":
        raise HTTPException(423, detail={
            "error_type": "account_suspended", "user_message": "账号已暂停，请联系管理员。",
            "recoverable": True,
        })
    await task_store.record_login_attempt(username_hash, source_ip_hash, True)
    await task_store.record_login(user["user_id"])
    if user.get("must_change_password"):
        await task_store.consume_temporary_password(user["user_id"])
    identity, token = await create_session(user)
    set_session_cookie(response, token)
    await task_store.add_audit_event(
        actor_user_id=user["user_id"], action="user.login", target_type="user",
        target_id=user["user_id"], request_id=request.headers.get("x-request-id"),
    )
    return _identity_payload(identity)


@router.get("/me")
async def me(identity: Identity = Depends(require_authenticated_user)):
    return _identity_payload(identity)


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest, request: Request, response: Response,
    identity: Identity = Depends(require_csrf),
):
    if payload.new_password != payload.confirm_password:
        raise HTTPException(422, detail={
            "error_type": "password_mismatch", "user_message": "两次输入的密码不一致。",
        })
    user = await task_store.get_user(identity.user_id)
    if not user:
        raise HTTPException(401, "user no longer exists")
    try:
        validate_password(payload.new_password, identity.username)
    except ValueError as exc:
        raise HTTPException(422, detail={"error_type": "password_policy", "user_message": str(exc)})
    if verify_password(user["password_hash"], payload.new_password):
        raise HTTPException(422, detail={
            "error_type": "password_reused", "user_message": "新密码不能与当前密码相同。",
        })
    await task_store.set_user_password(
        identity.user_id, hash_password(payload.new_password), temporary_expires_at=None,
        must_change_password=False, activate=True,
    )
    await task_store.revoke_user_sessions(identity.user_id, "password_changed")
    updated = await task_store.get_user(identity.user_id)
    normal_identity, token = await create_session(updated)
    set_session_cookie(response, token)
    await task_store.add_audit_event(
        actor_user_id=identity.user_id, action="user.password_changed", target_type="user",
        target_id=identity.user_id, request_id=request.headers.get("x-request-id"),
    )
    return _identity_payload(normal_identity)


@router.post("/logout")
async def logout(
    response: Response, identity: Identity = Depends(require_csrf),
):
    if identity.session_id:
        await task_store.revoke_session(identity.session_id, "logout")
    clear_session_cookie(response)
    return {"status": "ok"}


@router.post("/logout-all")
async def logout_all(
    response: Response, identity: Identity = Depends(require_csrf),
):
    await task_store.revoke_user_sessions(identity.user_id, "logout_all")
    clear_session_cookie(response)
    await task_store.add_audit_event(
        actor_user_id=identity.user_id, action="user.sessions_revoked",
        target_type="user", target_id=identity.user_id,
    )
    return {"status": "ok"}
