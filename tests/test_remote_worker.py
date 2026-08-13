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
from api.schemas.crawler import CrawlerStartRequest
from api.services.crawler_manager import CrawlerManager
from api.services.douyin_session_manager import (
    ProfileDirectory, EphemeralQrStore, ManagedDouyinSessionManager, BrowserSessionState,
)
from tools.browser_launcher import BrowserLauncher
from api.services.media_relay import MediaRelayBroker, parse_range, safe_media_path
from api.main import app
from api.worker import _control_websocket_url


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


def test_worker_control_url_requires_tls_except_loopback():
    assert _control_websocket_url("https://control.example.com") == "wss://control.example.com/internal/flowlens/workers/connect"
    assert _control_websocket_url("http://127.0.0.1:8080") == "ws://127.0.0.1:8080/internal/flowlens/workers/connect"
    with pytest.raises(ValueError):
        _control_websocket_url("http://control.example.com")


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
    message = sanitize_worker_payload("Cookie=session-secret ws://127.0.0.1:9222/devtools/browser/x")
    assert "session-secret" not in message and "9222" not in message


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


def test_managed_profile_is_passed_only_through_process_environment():
    manager = CrawlerManager()
    config = CrawlerStartRequest(
        platform="dy", crawler_type="search", keywords="x",
        browser_mode="managed_profile", browser_profile_id="c" * 32,
        connection_id="conn-1", worker_run_id="remote-run",
    )
    env = manager._build_process_env(config, "local-run", managed_cdp_port=9231)
    assert env["FLOWLENS_MANAGED_PROFILE"] == "1"
    assert env["FLOWLENS_BROWSER_PROFILE_ID"] == "c" * 32
    assert env["FLOWLENS_CDP_PORT"] == "9231"
    command = manager._build_command(config)
    assert "conn-1" not in " ".join(command) and "9231" not in " ".join(command)


def test_managed_login_session_publishes_qr_then_connects(tmp_path):
    class FakeBrowser:
        def __init__(self): self.checks = 0
        async def start(self, profile_path): return {"pid":123,"cdp_port":9222}
        async def open_login_qr(self): return b"qr-png"
        async def check_state(self):
            self.checks += 1
            return BrowserSessionState(
                "logged_in" if self.checks > 1 else "qr_scanned",
                creator_hash="creator-hash", masked_nickname="张**三",
            )
        async def close(self): return None

    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite"); await store.initialize()
        await store.upsert_worker({"worker_id":"w1","name":"w","public_key":"k","status":"online"})
        profile_id = "b" * 32
        directory = ProfileDirectory(tmp_path / "profiles")
        await store.save_browser_profile({"profile_id":profile_id,"connection_id":"c1","tenant_hash":"0123456789abcdef","status":"creating","profile_path":str(directory.path_for("0123456789abcdef",profile_id))})
        await store.save_connection({"connection_id":"c1","user_id":"u1","worker_id":"w1","profile_id":profile_id,"status":"creating"})
        await store.save_login_session({"login_session_id":"ls1","connection_id":"c1","profile_id":profile_id,"status":"queued","expires_at":"2099-01-01T00:00:00+00:00","user_id":"u1","worker_id":"w1"})
        qr = EphemeralQrStore()
        manager = ManagedDouyinSessionManager(store=store, profiles=directory, qr=qr, browser_factory=lambda:FakeBrowser(), poll_interval=0)
        await manager.run_login("ls1")
        assert qr.get("ls1") is None
        assert (await store.get_login_session("ls1"))["status"] == "logged_in"
        connection = await store.get_connection("c1")
        assert connection["status"] == "connected" and connection["masked_nickname"] == "张**三"

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
    command = asyncio.run(store.pending_outbox_for_worker("worker-1"))[0]
    command_body = json.loads(command["payload_json"])
    assert command_body["type"] == "douyin.login.start"
    assert command_body["payload"]["login_session_id"] == session_id
    assert "expires_at" in command_body and "command_id" in command_body
    command_body.pop("worker_id")
    assert WorkerCommand.model_validate(command_body).payload["login_session_id"] == session_id
    douyin_session_manager.qr_store.put(session_id, b"png-data")
    assert client.get(f"/api/flowlens/douyin/login-sessions/{session_id}", headers=headers_a).status_code == 200
    assert client.get(f"/api/flowlens/douyin/login-sessions/{session_id}", headers=headers_b).status_code == 404
    qr_response = client.get(f"/api/flowlens/douyin/login-sessions/{session_id}/qr", headers=headers_a)
    assert qr_response.status_code == 200 and qr_response.content == b"png-data"
    assert qr_response.headers["cache-control"] == "no-store, private"
    assert client.get(f"/api/flowlens/douyin/login-sessions/{session_id}/qr", headers=headers_b).status_code == 404
    assert client.post(f"/api/flowlens/douyin/login-sessions/{session_id}/cancel", headers=headers_a).status_code == 200
    session = asyncio.run(store.get_login_session(session_id))
    asyncio.run(store.update_connection(session["connection_id"], "connected", masked_nickname="已**录"))
    run = client.post("/api/flowlens/crawl-runs", json={
        "connection_id":session["connection_id"], "crawler_type":"search",
        "keywords":"测试", "max_notes_count":2,
    }, headers=headers_a)
    assert run.status_code == 200 and run.json()["status"] == "queued"
    run_id = run.json()["run_id"]
    assert client.get(f"/api/flowlens/crawl-runs/{run_id}", headers=headers_b).status_code == 404
    assert client.post(f"/api/flowlens/crawl-runs/{run_id}/pause", headers=headers_a).status_code == 200
    rejected = client.post("/api/flowlens/crawl-runs", json={
        "connection_id":session["connection_id"], "crawler_type":"search",
        "keywords":"测试", "chrome_args":["--remote-debugging-address=0.0.0.0"],
    }, headers=headers_a)
    assert rejected.status_code == 422


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


def test_worker_enrollment_is_one_time(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite"); await store.initialize()
        code = await store.create_worker_enrollment(600)
        assert await store.consume_worker_enrollment(code) is True
        assert await store.consume_worker_enrollment(code) is False
    asyncio.run(scenario())


def test_profile_directory_never_allows_path_traversal(tmp_path):
    profiles = ProfileDirectory(tmp_path)
    path = profiles.path_for("0123456789abcdef", "a" * 32)
    assert path.parent.parent == tmp_path.resolve() / "0123456789abcdef"
    with pytest.raises(ValueError):
        profiles.path_for("../outside", "a" * 32)
    with pytest.raises(ValueError):
        profiles.path_for("0123456789abcdef", "../outside")
    path.mkdir(parents=True)
    (path / "Preferences").write_text("{}", encoding="utf-8")
    assert profiles.delete("0123456789abcdef", "a" * 32) is True
    assert not path.parent.exists()


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


def test_remote_result_ingestion_is_tenant_scoped_sanitized_and_idempotent(tmp_path, monkeypatch):
    from api.routers import worker_gateway

    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite"); await store.initialize()
        monkeypatch.setattr(worker_gateway, "task_store", store)
        worker = {"worker_id":"worker-1"}
        await store.create_remote_run({
            "run_id":"remote-run", "user_id":"user-a", "connection_id":"conn-a",
            "worker_id":"worker-1", "config":{"crawler_type":"search"},
        })
        event = {
            "type":"result.event", "event_id":"event-1", "run_id":"remote-run",
            "entity_type":"aweme", "entity_id":"aweme-1",
            "payload":{"title":"safe", "cookies":"secret", "author":{"sec_uid":"private"}},
        }
        first = await worker_gateway._receive_worker_message(worker, event)
        second = await worker_gateway._receive_worker_message(worker, event)
        assert first == {"type":"result.ack", "event_id":"event-1", "stored":True}
        assert second["stored"] is False
        rows = await store.list_user_remote_results("user-a", "aweme")
        payload = json.loads(rows[0]["payload_json"])
        assert payload == {"title":"safe", "author":{}}
        assert await store.list_user_remote_results("user-b", "aweme") == []

    asyncio.run(scenario())


def test_worker_command_result_binds_local_run_id(tmp_path, monkeypatch):
    from api.routers import worker_gateway

    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite"); await store.initialize()
        monkeypatch.setattr(worker_gateway, "task_store", store)
        await store.create_remote_run({
            "run_id":"remote-run", "user_id":"user-a", "connection_id":"conn-a",
            "worker_id":"worker-1", "config":{},
        })
        await worker_gateway._receive_worker_message(
            {"worker_id":"worker-1"},
            {"type":"command.result", "run_id":"remote-run", "result":{"worker_run_id":"local-run"}},
        )
        run = await store.get_remote_run("remote-run")
        assert run["worker_run_id"] == "local-run" and run["status"] == "running"

    asyncio.run(scenario())


def test_remote_media_range_and_stream_limits(tmp_path, monkeypatch):
    from api.services import media_relay
    root = tmp_path / "media"
    target = root / "creator" / "aweme" / "video.mp4"
    target.parent.mkdir(parents=True); target.write_bytes(b"0123456789")
    monkeypatch.setattr(media_relay, "MEDIA_ROOT", root.resolve())
    assert safe_media_path(str(target)) == target.resolve()
    assert parse_range("bytes=2-5", 10) == (2, 5)
    assert parse_range("bytes=-3", 10) == (7, 9)
    with pytest.raises(ValueError): parse_range("bytes=20-30", 10)
    with pytest.raises(PermissionError): safe_media_path(str(tmp_path / "outside.mp4"))

    async def scenario():
        broker = MediaRelayBroker()
        first = broker.create("worker", "one", None)
        second = broker.create("worker", "two", None)
        with pytest.raises(RuntimeError): broker.create("worker", "three", None)
        broker.close(first.stream_id)
        third = broker.create("worker", "three", None)
        assert third and second

    asyncio.run(scenario())
