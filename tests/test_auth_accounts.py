import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from api.main import app
from api.services.auth import (
    generate_temporary_password,
    hash_password,
    iso,
    utc_now,
    verify_password,
)
from api.services.task_store import TaskStore


def _patch_store(monkeypatch, store):
    from api.services import auth as auth_service
    from api.routers import auth as auth_router
    from api.routers import admin_users, remote, worker_gateway
    monkeypatch.setattr(auth_service, "task_store", store)
    monkeypatch.setattr(auth_router, "task_store", store)
    monkeypatch.setattr(admin_users, "task_store", store)
    monkeypatch.setattr(remote, "task_store", store)
    monkeypatch.setattr(worker_gateway, "task_store", store)


def _seed_user(store, *, username, role="user", status="pending_activation", password=None):
    password = password or generate_temporary_password()
    user = asyncio.run(store.create_user({
        "username": username, "normalized_username": username.lower(),
        "display_name": f"{username} 显示名", "password_hash": hash_password(password),
        "role": role, "status": status, "must_change_password": status == "pending_activation",
        "temporary_password_expires_at": iso(utc_now() + timedelta(hours=24)) if status == "pending_activation" else None,
    }))
    return user, password


def _login(client, username, password):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password},
        headers={"Origin": "http://testserver"},
    )


def _csrf_headers(csrf):
    return {"Origin": "http://testserver", "X-CSRF-Token": csrf}


def test_remote_auth_first_login_admin_creates_user_and_suspends(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    asyncio.run(store.initialize())
    _patch_store(monkeypatch, store)
    monkeypatch.setenv("FLOWLENS_REMOTE_WORKER", "true")
    monkeypatch.setenv("FLOWLENS_PUBLIC_ORIGIN", "http://testserver")
    monkeypatch.setenv("FLOWLENS_AUTH_HASH_KEY", "test-key-at-least-32-random-characters")
    monkeypatch.delenv("FLOWLENS_TRUSTED_HEADER_COMPAT", raising=False)
    admin, temporary_password = _seed_user(store, username="root.admin", role="admin")

    client = TestClient(app)
    assert client.get("/api/admin/users").status_code == 401
    assert client.post("/api/auth/register", json={}).status_code == 404

    login = _login(client, "ROOT.ADMIN", temporary_password)
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    csrf = login.json()["csrf_token"]
    assert login.headers["cache-control"] == "no-store, private"
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/flowlens/douyin/connections").status_code == 403
    second_browser = TestClient(app)
    assert _login(second_browser, "root.admin", temporary_password).status_code == 401
    assert client.post(
        "/api/auth/change-password",
        json={"new_password": "a-valid-permanent-password-2026", "confirm_password": "a-valid-permanent-password-2026"},
        headers={"Origin": "http://testserver"},
    ).status_code == 403
    changed = client.post(
        "/api/auth/change-password",
        json={"new_password": "a-valid-permanent-password-2026", "confirm_password": "a-valid-permanent-password-2026"},
        headers=_csrf_headers(csrf),
    )
    assert changed.status_code == 200
    csrf = changed.json()["csrf_token"]
    assert changed.json()["capabilities"]["admin_console"] is True

    role_injection = client.post(
        "/api/admin/users",
        json={"username": "researcher", "display_name": "研究员", "role": "admin"},
        headers=_csrf_headers(csrf),
    )
    assert role_injection.status_code == 422
    created = client.post(
        "/api/admin/users",
        json={"username": "researcher", "display_name": "研究员", "max_douyin_connections": 2,
              "max_queued_tasks": 4, "media_quota_bytes": 1024**3},
        headers=_csrf_headers(csrf),
    )
    assert created.status_code == 200
    assert created.json()["user"]["role"] == "user"
    assert created.json()["temporary_password"]
    assert "password_hash" not in created.text
    user_id = created.json()["user"]["user_id"]

    users = client.get("/api/admin/users")
    assert users.status_code == 200
    assert any(item["user_id"] == user_id for item in users.json()["items"])
    updated = client.patch(
        f"/api/admin/users/{user_id}",
        json={"display_name": "研究员一", "max_douyin_connections": 5,
              "max_queued_tasks": 8, "media_quota_bytes": 2 * 1024**3},
        headers=_csrf_headers(csrf),
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "研究员一"
    assert updated.json()["max_douyin_connections"] == 5
    revoked = client.post(
        f"/api/admin/users/{user_id}/revoke-sessions", headers=_csrf_headers(csrf),
    )
    assert revoked.status_code == 200
    suspended = client.post(f"/api/admin/users/{user_id}/suspend", headers=_csrf_headers(csrf))
    assert suspended.status_code == 200
    assert _login(client, "researcher", created.json()["temporary_password"]).status_code == 423
    restored = client.post(f"/api/admin/users/{user_id}/restore", headers=_csrf_headers(csrf))
    assert restored.status_code == 200
    assert restored.json()["status"] == "pending_activation"
    self_suspend = client.post(f"/api/admin/users/{admin['user_id']}/suspend", headers=_csrf_headers(csrf))
    assert self_suspend.status_code == 409


def test_wrong_username_and_password_are_indistinguishable_and_lock(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    asyncio.run(store.initialize())
    _patch_store(monkeypatch, store)
    monkeypatch.setenv("FLOWLENS_REMOTE_WORKER", "true")
    monkeypatch.setenv("FLOWLENS_PUBLIC_ORIGIN", "http://testserver")
    monkeypatch.setenv("FLOWLENS_AUTH_HASH_KEY", "another-test-key-at-least-32-characters")
    monkeypatch.setenv("FLOWLENS_LOGIN_MAX_FAILURES", "5")
    _seed_user(store, username="known.user", status="active", password="known-good-password-2026")
    client = TestClient(app)
    unknown = _login(client, "unknown.user", "wrong-password-value")
    wrong = _login(client, "known.user", "wrong-password-value")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"]["user_message"] == wrong.json()["detail"]["user_message"]
    # Four more failures for the same source reach the shared source-IP limit.
    for _ in range(3):
        assert _login(client, "known.user", "wrong-password-value").status_code == 401
    locked = _login(client, "known.user", "wrong-password-value")
    assert locked.status_code == 429


def test_local_mode_remains_auth_free(monkeypatch):
    monkeypatch.setenv("FLOWLENS_REMOTE_WORKER", "false")
    client = TestClient(app)
    capabilities = client.get("/api/system/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["mode"] == "local"
    assert capabilities.json()["current_role"] == "admin"
    assert client.get("/api/auth/me").status_code == 200


def test_schema_migration_adds_auth_tables_and_connection_labels(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    asyncio.run(store.initialize())
    asyncio.run(store.initialize())
    import sqlite3
    with sqlite3.connect(store.path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in db.execute("PRAGMA table_info(douyin_connection)")}
    assert {"flowlens_user", "user_session", "auth_login_attempt", "audit_event"} <= tables
    assert {"display_name", "remark", "updated_at"} <= columns


def test_local_admin_bootstrap_is_single_use_and_reset_revokes_sessions(monkeypatch, tmp_path):
    from tools import create_admin as create_admin_tool
    from tools import reset_admin_password as reset_admin_tool
    from api.services import auth as auth_service

    store = TaskStore(tmp_path / "tasks.sqlite")
    asyncio.run(store.initialize())
    monkeypatch.setattr(create_admin_tool, "task_store", store)
    monkeypatch.setattr(reset_admin_tool, "task_store", store)
    monkeypatch.setattr(auth_service, "task_store", store)
    password, expires_at = asyncio.run(
        create_admin_tool.create_admin("First.Admin", "首位管理员")
    )
    user = asyncio.run(store.get_user_by_username("first.admin"))
    assert user["role"] == "admin" and user["status"] == "pending_activation"
    assert verify_password(user["password_hash"], password)
    assert expires_at == user["temporary_password_expires_at"]
    try:
        asyncio.run(create_admin_tool.create_admin("second.admin", "第二管理员"))
    except RuntimeError as exc:
        assert "已经存在管理员" in str(exc)
    else:
        raise AssertionError("second administrator bootstrap should be rejected")

    from api.services.auth import create_session

    _, token = asyncio.run(create_session(user))
    new_password, reset_expires_at = asyncio.run(
        reset_admin_tool.reset_admin("FIRST.ADMIN")
    )
    updated = asyncio.run(store.get_user(user["user_id"]))
    assert new_password != password
    assert verify_password(updated["password_hash"], new_password)
    assert updated["temporary_password_expires_at"] == reset_expires_at
    session = asyncio.run(store.get_user_session(__import__("hashlib").sha256(token.encode()).hexdigest()))
    assert session["revoked_reason"] == "admin_password_reset"


def test_ordinary_user_cannot_use_admin_api_and_multiple_connections_are_isolated(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    asyncio.run(store.initialize())
    _patch_store(monkeypatch, store)
    monkeypatch.setenv("FLOWLENS_REMOTE_WORKER", "true")
    monkeypatch.setenv("FLOWLENS_PUBLIC_ORIGIN", "http://testserver")
    monkeypatch.setenv("FLOWLENS_AUTH_HASH_KEY", "multi-account-test-key-with-enough-entropy")
    user_a, password_a = _seed_user(
        store, username="user.a", status="active", password="user-a-password-2026",
    )
    user_b, password_b = _seed_user(
        store, username="user.b", status="active", password="user-b-password-2026",
    )

    async def seed_connections():
        await store.upsert_worker({
            "worker_id":"worker-1", "name":"worker-1", "public_key":"x" * 64,
            "status":"online", "version":"1.3.0",
        })
        for index, owner in enumerate((user_a, user_a, user_b), start=1):
            await store.save_connection({
                "connection_id": f"conn-{index}", "user_id": owner["user_id"],
                "worker_id": "worker-1", "profile_id": f"{index:032x}",
                "status": "connected", "masked_nickname": f"账号**{index}",
            })
        await store.update_connection_labels(
            "conn-2", display_name="常用研究号", remark="新能源组",
        )

    asyncio.run(seed_connections())
    client_a, client_b = TestClient(app), TestClient(app)
    login_a = _login(client_a, "USER.A", password_a)
    login_b = _login(client_b, "user.b", password_b)
    assert login_a.status_code == login_b.status_code == 200
    assert client_a.get("/api/admin/users").status_code == 403
    items_a = client_a.get("/api/flowlens/douyin/connections").json()["items"]
    items_b = client_b.get("/api/flowlens/douyin/connections").json()["items"]
    assert {item["connection_id"] for item in items_a} == {"conn-1", "conn-2"}
    assert {item["connection_id"] for item in items_b} == {"conn-3"}
    assert next(item for item in items_a if item["connection_id"] == "conn-2")["display_name"] == "常用研究号"
    forged = client_b.post(
        "/api/flowlens/crawl-runs",
        json={"connection_id": "conn-2", "crawler_type": "search", "keywords": "越权"},
        headers=_csrf_headers(login_b.json()["csrf_token"]),
    )
    assert forged.status_code == 404

    async def seed_quota_usage():
        await store.update_user_profile(user_a["user_id"], media_quota_bytes=1000)
        await store.store_remote_result({
            "source_event_id":"quota-media", "user_id":user_a["user_id"],
            "connection_id":"conn-1", "run_id":"historical-run", "worker_id":"worker-1",
            "entity_type":"media", "entity_id":"quota-asset",
            "payload":{"asset_id":"quota-asset", "status":"completed", "size_bytes":900},
        })

    asyncio.run(seed_quota_usage())
    first_download = client_a.post(
        "/api/flowlens/crawl-runs",
        json={
            "connection_id":"conn-1", "crawler_type":"search", "keywords":"配额",
            "download_media":True, "max_media_total_bytes":500,
        },
        headers=_csrf_headers(login_a.json()["csrf_token"]),
    )
    assert first_download.status_code == 200
    run = asyncio.run(store.get_user_remote_run(first_download.json()["run_id"], user_a["user_id"]))
    config = __import__("json").loads(run["sanitized_config_json"])
    assert config["max_media_total_bytes"] == 100
    quota_reached = client_a.post(
        "/api/flowlens/crawl-runs",
        json={
            "connection_id":"conn-1", "crawler_type":"search", "keywords":"再次下载",
            "download_media":True, "max_media_total_bytes":1,
        },
        headers=_csrf_headers(login_a.json()["csrf_token"]),
    )
    assert quota_reached.status_code == 409
    assert quota_reached.json()["detail"]["error_type"] == "media_quota_reached"


def test_admin_emergency_pause_and_remote_media_delete_are_owner_scoped(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    asyncio.run(store.initialize())
    _patch_store(monkeypatch, store)
    monkeypatch.setenv("FLOWLENS_REMOTE_WORKER", "true")
    monkeypatch.setenv("FLOWLENS_PUBLIC_ORIGIN", "http://testserver")
    monkeypatch.setenv("FLOWLENS_AUTH_HASH_KEY", "admin-operation-key-with-enough-entropy")
    _, admin_password = _seed_user(
        store, username="admin.ops", role="admin", status="active",
        password="admin-ops-password-2026",
    )
    owner, owner_password = _seed_user(
        store, username="media.owner", status="active", password="media-owner-password-2026",
    )

    async def seed():
        await store.upsert_worker({
            "worker_id":"worker-1", "name":"worker-1", "public_key":"x" * 64,
            "status":"online", "version":"1.3.0",
        })
        await store.save_connection({
            "connection_id":"conn-media", "user_id":owner["user_id"],
            "worker_id":"worker-1", "profile_id":"a" * 32,
            "status":"connected", "masked_nickname":"媒体**号",
        })
        await store.create_remote_run({
            "run_id":"run-media", "user_id":owner["user_id"],
            "connection_id":"conn-media", "worker_id":"worker-1",
            "config":{"crawler_type":"search"}, "status":"running",
        })
        await store.store_remote_result({
            "source_event_id":"media-event", "user_id":owner["user_id"],
            "connection_id":"conn-media", "run_id":"run-media", "worker_id":"worker-1",
            "entity_type":"media", "entity_id":"asset-owner",
            "payload":{"asset_id":"asset-owner", "kind":"video", "status":"completed", "size_bytes":123},
        })

    asyncio.run(seed())
    admin_client, owner_client = TestClient(app), TestClient(app)
    admin_login = _login(admin_client, "admin.ops", admin_password)
    owner_login = _login(owner_client, "media.owner", owner_password)
    paused = admin_client.post(
        "/api/flowlens/admin/queue/run-media/pause",
        headers=_csrf_headers(admin_login.json()["csrf_token"]),
    )
    assert paused.status_code == 200 and paused.json()["status"] == "pausing"
    deleted = owner_client.delete(
        "/api/flowlens/media/asset-owner", params={"confirm": "true"},
        headers=_csrf_headers(owner_login.json()["csrf_token"]),
    )
    assert deleted.status_code == 200 and deleted.json()["status"] == "deleting"
    queued = asyncio.run(store.pending_outbox_for_worker("worker-1"))
    payloads = [__import__("json").loads(item["payload_json"]) for item in queued]
    media_command = next(item for item in payloads if item["type"] == "media.delete")
    assert media_command["payload"] == {"asset_id":"asset-owner", "run_id":"run-media"}
    result = asyncio.run(store.get_user_remote_result(owner["user_id"], "media", "asset-owner"))
    assert __import__("json").loads(result["payload_json"])["status"] == "deleting"
    assert asyncio.run(store.update_remote_media_status(
        "asset-owner", "worker-1", "deleted", user_id=owner["user_id"], deleted=True,
    ))
    resources = asyncio.run(store.get_user_resource_summary(owner["user_id"]))
    assert resources["media_usage_bytes"] == 0
