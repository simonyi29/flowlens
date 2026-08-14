"""Reset an existing administrator password from the server console."""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from api.services.auth import (
    generate_temporary_password, hash_password, iso, normalize_username,
    temporary_password_seconds, utc_now,
)
from api.services.task_store import task_store


async def reset_admin(username: str) -> tuple[str, str]:
    await task_store.initialize()
    user = await task_store.get_user_by_username(normalize_username(username))
    if not user or user["role"] != "admin":
        raise RuntimeError("未找到该管理员账号")
    password = generate_temporary_password()
    expires_at = iso(utc_now() + timedelta(seconds=temporary_password_seconds()))
    await task_store.set_user_password(
        user["user_id"], hash_password(password), temporary_expires_at=expires_at,
        must_change_password=True,
    )
    await task_store.revoke_user_sessions(user["user_id"], "admin_password_reset")
    await task_store.add_audit_event(
        actor_user_id=user["user_id"], action="user.temporary_password_reset",
        target_type="user", target_id=user["user_id"], context={"source": "local_cli"},
    )
    return password, expires_at


def main() -> None:
    parser = argparse.ArgumentParser(description="重置 FlowLens 管理员临时密码")
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    try:
        password, expires_at = asyncio.run(reset_admin(args.username))
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"重置失败：{exc}") from exc
    print("新的临时密码仅显示一次：")
    print(f"一次性临时密码: {password}")
    print(f"有效期至: {expires_at}")


if __name__ == "__main__":
    main()
