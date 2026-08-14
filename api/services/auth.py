"""FlowLens website authentication, password and session primitives.

Browser users are authenticated exclusively by a server-side session cookie in
remote mode. Worker device authentication remains a separate Ed25519 protocol.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request

from .task_store import task_store

SESSION_COOKIE = "flowlens_session"
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def remote_mode() -> bool:
    return os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1", "true", "yes"}


def normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError("用户名须为 3～32 位小写字母、数字、点、下划线或连字符")
    return normalized


def validate_display_name(display_name: str) -> str:
    value = display_name.strip()
    if not 1 <= len(value) <= 64:
        raise ValueError("显示名称须为 1～64 个字符")
    return value


def validate_password(password: str, username: str, *, previous_password: str | None = None) -> None:
    if not 12 <= len(password) <= 128:
        raise ValueError("密码长度须为 12～128 个字符")
    if password.casefold() == username.casefold():
        raise ValueError("密码不能与用户名相同")
    if previous_password is not None and hmac.compare_digest(password, previous_password):
        raise ValueError("新密码不能与当前临时密码相同")


def generate_temporary_password() -> str:
    # 24 bytes provide more than 20 URL-safe printable characters.
    return secrets.token_urlsafe(24)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _hash_key() -> bytes:
    configured = os.getenv("FLOWLENS_AUTH_HASH_KEY", "")
    if configured:
        return configured.encode("utf-8")
    # Local development remains usable; remote health reports this as unsafe.
    return hashlib.sha256(b"flowlens-local-development-key").digest()


def opaque_hash(value: str, purpose: str) -> str:
    return hmac.new(_hash_key(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token_for_session(token: str) -> str:
    return hmac.new(_hash_key(), f"csrf:{token}".encode(), hashlib.sha256).hexdigest()


def csrf_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_idle_seconds() -> int:
    return max(300, int(os.getenv("FLOWLENS_SESSION_IDLE_SECONDS", "43200")))


def session_absolute_seconds() -> int:
    return max(session_idle_seconds(), int(os.getenv("FLOWLENS_SESSION_ABSOLUTE_SECONDS", "604800")))


def temporary_password_seconds() -> int:
    return max(300, int(os.getenv("FLOWLENS_TEMP_PASSWORD_SECONDS", "86400")))


def cookie_secure() -> bool:
    return os.getenv("FLOWLENS_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Identity:
    user_id: str
    username: str
    display_name: str
    role: str
    status: str
    must_change_password: bool
    session_id: str | None = None
    session_token: str | None = None
    max_douyin_connections: int = 3
    max_queued_tasks: int = 10
    media_quota_bytes: int = 20 * 1024**3

    @property
    def csrf_token(self) -> str | None:
        return csrf_token_for_session(self.session_token) if self.session_token else None


LOCAL_OWNER = Identity(
    user_id="local_owner", username="local_owner", display_name="本机管理员",
    role="admin", status="active", must_change_password=False,
)


async def create_session(user: dict[str, Any]) -> tuple[Identity, str]:
    token = secrets.token_urlsafe(48)
    now = utc_now()
    session_id = secrets.token_hex(24)
    csrf_token = csrf_token_for_session(token)
    await task_store.create_user_session({
        "session_id": session_id,
        "user_id": user["user_id"],
        "session_token_hash": session_token_hash(token),
        "csrf_token_hash": csrf_token_hash(csrf_token),
        "created_at": iso(now),
        "last_seen_at": iso(now),
        "idle_expires_at": iso(now + timedelta(seconds=session_idle_seconds())),
        "absolute_expires_at": iso(now + timedelta(seconds=session_absolute_seconds())),
    })
    identity = Identity(
        user_id=user["user_id"], username=user["username"], display_name=user["display_name"],
        role=user["role"], status=user["status"],
        must_change_password=bool(user["must_change_password"]), session_id=session_id,
        session_token=token, max_douyin_connections=int(user.get("max_douyin_connections") or 3),
        max_queued_tasks=int(user.get("max_queued_tasks") or 10),
        media_quota_bytes=int(user.get("media_quota_bytes") or 20 * 1024**3),
    )
    return identity, token


async def identity_from_token(token: str | None, *, touch: bool = True) -> Identity | None:
    if not token:
        return None
    row = await task_store.get_user_session(session_token_hash(token))
    if not row or row.get("revoked_at"):
        return None
    derived_csrf = csrf_token_for_session(token)
    if not hmac.compare_digest(str(row.get("csrf_token_hash") or ""), csrf_token_hash(derived_csrf)):
        await task_store.revoke_session(row["session_id"], "csrf_binding_invalid")
        return None
    now = utc_now()
    idle = parse_datetime(row.get("idle_expires_at"))
    absolute = parse_datetime(row.get("absolute_expires_at"))
    if not idle or not absolute or idle <= now or absolute <= now:
        await task_store.revoke_session(row["session_id"], "expired")
        return None
    if touch:
        next_idle = min(absolute, now + timedelta(seconds=session_idle_seconds()))
        await task_store.touch_user_session(row["session_id"], iso(now), iso(next_idle))
    return Identity(
        user_id=row["user_id"], username=row["username"], display_name=row["display_name"],
        role=row["role"], status=row["status"],
        must_change_password=bool(row["must_change_password"]), session_id=row["session_id"],
        session_token=token, max_douyin_connections=int(row.get("max_douyin_connections") or 3),
        max_queued_tasks=int(row.get("max_queued_tasks") or 10),
        media_quota_bytes=int(row.get("media_quota_bytes") or 20 * 1024**3),
    )


async def optional_current_identity(request: Request) -> Identity | None:
    if not remote_mode():
        return LOCAL_OWNER
    return await identity_from_token(request.cookies.get(SESSION_COOKIE))


async def require_authenticated_user(
    identity: Identity | None = Depends(optional_current_identity),
) -> Identity:
    if identity is None:
        raise HTTPException(401, detail={
            "error_type": "authentication_required", "user_message": "请先登录 FlowLens。",
            "recoverable": True, "recommended_action": "login",
        })
    if identity.status == "suspended":
        raise HTTPException(423, detail={
            "error_type": "account_suspended", "user_message": "账号已暂停，请联系管理员。",
            "recoverable": True, "recommended_action": "contact_admin",
        })
    return identity


async def require_password_changed(
    identity: Identity = Depends(require_authenticated_user),
) -> Identity:
    if identity.must_change_password or identity.status == "pending_activation":
        raise HTTPException(403, detail={
            "error_type": "password_change_required", "user_message": "首次登录需要先设置正式密码。",
            "recoverable": True, "recommended_action": "change_password",
        })
    if identity.status != "active":
        raise HTTPException(403, "account is not active")
    return identity


async def require_admin(identity: Identity = Depends(require_password_changed)) -> Identity:
    if identity.role != "admin":
        raise HTTPException(403, detail={
            "error_type": "admin_required", "user_message": "需要管理员权限。",
            "recoverable": False,
        })
    return identity


def validate_origin(request: Request) -> None:
    if not remote_mode():
        return
    allowed = os.getenv("FLOWLENS_PUBLIC_ORIGIN", "").rstrip("/")
    origin = (request.headers.get("origin") or "").rstrip("/")
    if allowed and (not origin or not hmac.compare_digest(allowed, origin)):
        raise HTTPException(403, detail={
            "error_type": "invalid_origin", "user_message": "请求来源未被允许。",
        })


async def require_csrf(
    request: Request, identity: Identity = Depends(require_authenticated_user),
) -> Identity:
    if not remote_mode():
        return identity
    validate_origin(request)
    provided = request.headers.get("x-csrf-token", "")
    expected = identity.csrf_token or ""
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(403, detail={
            "error_type": "csrf_failed", "user_message": "安全令牌已失效，请刷新页面后重试。",
            "recoverable": True, "recommended_action": "refresh",
        })
    return identity


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=cookie_secure(), samesite="lax",
        path="/", max_age=session_absolute_seconds(),
    )
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=cookie_secure(), samesite="lax")
    response.headers["Cache-Control"] = "no-store, private"
