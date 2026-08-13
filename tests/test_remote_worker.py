import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from api.schemas.worker import WorkerCommand, WorkerEvent
from api.services.task_store import TaskStore
from api.services.worker_security import sanitize_worker_payload
from api.services.worker_identity import WorkerIdentityManager, verify_worker_signature
from api.services.worker_agent import WorkerAgent
from api.services.douyin_session_manager import ProfileDirectory, EphemeralQrStore
from tools.browser_launcher import BrowserLauncher
from api.main import app


def test_browser_launcher_binds_cdp_to_loopback(monkeypatch, tmp_path):
    captured = {}

    class Process:
        pid = 123

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return Process()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    launcher = BrowserLauncher()
    launcher.launch_browser("chrome", 9222, user_data_dir=str(tmp_path))
    assert "--remote-debugging-address=127.0.0.1" in captured["args"]
    assert not any("0.0.0.0" in value for value in captured["args"])


def test_worker_protocol_rejects_unknown_commands_and_expired_commands():
    with pytest.raises(ValidationError):
        WorkerCommand(type="shell.exec", payload={})
    with pytest.raises(ValidationError):
        WorkerCommand(
            type="crawl.start",
            issued_at="2020-01-01T00:00:00Z",
            expires_at="2020-01-01T00:01:00Z",
            payload={},
        )


def test_worker_payload_sanitizer_removes_secrets_recursively():
    cleaned = sanitize_worker_payload({
        "cookies": "secret",
        "nested": {"authorization": "bearer", "title": "safe"},
        "items": [{"sec_uid": "uid", "content": "ok"}],
        "cdp_ws_url": "ws://secret",
    })
    rendered = json.dumps(cleaned)
    assert "secret" not in rendered and "bearer" not in rendered and "uid" not in rendered
    assert cleaned["nested"]["title"] == "safe"
    assert cleaned["items"][0]["content"] == "ok"


def test_worker_event_has_id_and_sequence():
    event = WorkerEvent(worker_id="worker-1", event_type="worker.heartbeat", sequence=1, payload={})
    assert event.event_id and event.protocol_version == "1.0"


def test_worker_tables_and_outbox_are_idempotent(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        await store.initialize()
        await store.initialize()
        event_id = await store.enqueue_outbox("crawl.status", {"status": "running"})
        rows = await store.pending_outbox()
        assert rows[0]["event_id"] == event_id
        assert rows[0]["status"] == "pending"
        await store.ack_outbox(rows[0]["sequence"])
        assert await store.pending_outbox() == []
        with sqlite3.connect(store.path) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"worker_identity", "browser_profile", "login_session", "worker_command", "sync_outbox", "media_stream_session"} <= tables

    asyncio.run(scenario())


def test_worker_identity_is_persistent_and_signatures_verify(tmp_path):
    manager = WorkerIdentityManager(tmp_path / "identity.pem")
    first = manager.load_or_create()
    second = manager.load_or_create()
    challenge = b"one-time-challenge"
    signature = manager.sign(challenge)
    assert first == second
    assert verify_worker_signature(first, challenge, signature)
    assert not verify_worker_signature(first, b"different", signature)


def test_remote_api_requires_trusted_proxy_and_isolates_users(monkeypatch, tmp_path):
    from api.routers import remote as remote_router
    from api.services import douyin_session_manager

    store = TaskStore(tmp_path / "tasks.sqlite")
    monkeypatch.setattr(remote_router, "task_store", store)
    monkeypatch.setattr(douyin_session_manager, "profile_directory", ProfileDirectory(tmp_path / "profiles"))
    monkeypatch.setenv("FLOWLENS_REMOTE_WORKER", "true")
    monkeypatch.setenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "test-proxy-token")
    asyncio.run(store.upsert_worker({"worker_id":"worker-1","name":"test","public_key":"key","status":"online"}))
    client = TestClient(app)
    assert client.get("/api/flowlens/douyin/connections").status_code == 401
    headers_a = {"X-FlowLens-Proxy-Token":"test-proxy-token", "X-FlowLens-User-ID":"user-a"}
    headers_b = {"X-FlowLens-Proxy-Token":"test-proxy-token", "X-FlowLens-User-ID":"user-b"}
    created = client.post("/api/flowlens/douyin/login-sessions", json={"worker_id":"worker-1"}, headers=headers_a)
    assert created.status_code == 200
    session_id = created.json()["login_session_id"]
    assert client.get(f"/api/flowlens/douyin/login-sessions/{session_id}", headers=headers_a).status_code == 200
    assert client.get(f"/api/flowlens/douyin/login-sessions/{session_id}", headers=headers_b).status_code == 404


def test_worker_agent_executes_each_command_once(tmp_path):
    async def scenario():
        calls = []
        store = TaskStore(tmp_path / "tasks.sqlite")
        await store.initialize()
        agent = WorkerAgent("worker-1", store=store)

        async def handler(command):
            calls.append(command.command_id)
            return {"ok": True}

        agent.register_handler("crawl.start", handler)
        command = WorkerCommand(type="crawl.start", payload={"keywords":"safe"})
        first = await agent.process_command(command.model_dump(mode="json"))
        second = await agent.process_command(command.model_dump(mode="json"))
        assert first["status"] == "completed"
        assert second["status"] == "duplicate"
        assert calls == [command.command_id]

    asyncio.run(scenario())


def test_profile_directory_never_allows_path_traversal(tmp_path):
    profiles = ProfileDirectory(tmp_path)
    path = profiles.path_for("0123456789abcdef", "a" * 32)
    assert path.parent.parent == tmp_path.resolve() / "0123456789abcdef"
    with pytest.raises(ValueError):
        profiles.path_for("../outside", "a" * 32)
    with pytest.raises(ValueError):
        profiles.path_for("0123456789abcdef", "../outside")


def test_ephemeral_qr_store_expires_and_never_persists(tmp_path):
    clock = [100.0]
    store = EphemeralQrStore(ttl_seconds=180, clock=lambda: clock[0])
    store.put("session", b"png")
    assert store.get("session") == b"png"
    clock[0] = 281.0
    assert store.get("session") is None
    assert list(tmp_path.iterdir()) == []


def test_remote_connection_ownership_and_command_idempotency(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        await store.initialize()
        await store.save_browser_profile({
            "profile_id": "a" * 32, "connection_id": "conn-1",
            "tenant_hash": "0123456789abcdef", "status": "creating",
            "profile_path": str(tmp_path / "profile"),
        })
        await store.save_login_session({
            "login_session_id": "ls-1", "connection_id": "conn-1",
            "profile_id": "a" * 32, "status": "queued",
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        assert (await store.get_login_session("ls-1"))["status"] == "queued"
        first = await store.claim_worker_command("cmd-1", "crawl.start")
        second = await store.claim_worker_command("cmd-1", "crawl.start")
        assert first is True and second is False

    asyncio.run(scenario())
