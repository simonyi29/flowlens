"""Create the first FlowLens administrator from the server console."""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import timedelta

from api.services.auth import (
    generate_temporary_password, hash_password, iso, normalize_username,
    temporary_password_seconds, utc_now, validate_display_name,
)
from api.services.task_store import task_store


async def create_admin(username: str, display_name: str) -> tuple[str, str]:
    await task_store.initialize()
    if await task_store.admin_exists():
        raise RuntimeError("数据库中已经存在管理员，不能再次初始化")
    normalized = normalize_username(username)
    display_name = validate_display_name(display_name)
    temporary_password = generate_temporary_password()
    expires_at = iso(utc_now() + timedelta(seconds=temporary_password_seconds()))
    user = await task_store.create_user({
        "username": normalized, "normalized_username": normalized,
        "display_name": display_name, "password_hash": hash_password(temporary_password),
        "role": "admin", "status": "pending_activation", "must_change_password": True,
        "temporary_password_expires_at": expires_at,
    })
    await task_store.add_audit_event(
        actor_user_id=user["user_id"], action="admin.bootstrap_created",
        target_type="user", target_id=user["user_id"], context={"username": normalized},
    )
    return temporary_password, expires_at


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 FlowLens 第一位管理员")
    parser.add_argument("--username", help="管理员用户名")
    parser.add_argument("--display-name", help="显示名称")
    args = parser.parse_args()
    username = args.username or input("管理员用户名: ").strip()
    display_name = args.display_name or input("显示名称: ").strip()
    try:
        password, expires_at = asyncio.run(create_admin(username, display_name))
    except (RuntimeError, ValueError, sqlite3.IntegrityError) as exc:
        raise SystemExit(f"初始化失败：{exc}") from exc
    print("\n管理员已创建。以下临时密码仅显示一次：")
    print(f"用户名: {normalize_username(username)}")
    print(f"一次性临时密码: {password}")
    print(f"有效期至: {expires_at}")
    print("登录地址: /#/login")


if __name__ == "__main__":
    main()
