import asyncio

from fastapi.testclient import TestClient

from api.main import app
from api.routers import dashboard as dashboard_router
from api.services.product_views import allowed_actions, present_run, safe_error
from api.services.task_store import TaskStore


def test_task_view_has_readable_name_progress_and_state_actions():
    run = {
        "run_id": "run-1",
        "platform": "dy",
        "crawler_type": "search",
        "status": "running",
        "stage": "comments",
        "config_json": '{"keywords":"新能源汽车, 智能驾驶","max_notes_count":30}',
        "created_at": "2026-08-13T08:00:00+00:00",
    }
    view = present_run(run, stages=[{
        "stage": "detail", "status": "running", "total_count": 30,
        "completed_count": 12, "failed_count": 1,
    }])
    assert view["display_name"] == "关键词：新能源汽车"
    assert view["source_summary"] == "新能源汽车"
    assert view["progress"] == {"completed": 12, "total": 30, "percent": 40.0}
    assert view["status_label"] == "正在采集"
    assert view["allowed_actions"] == ["pause", "cancel"]


def test_allowed_actions_are_strictly_state_specific():
    assert allowed_actions("completed") == ["view_results", "rerun"]
    assert "pause" not in allowed_actions("completed")
    assert allowed_actions("partial") == ["view_failures", "retry_failed"]
    assert allowed_actions("waiting_for_login") == ["reconnect", "continue_after_login", "cancel"]


def test_safe_error_maps_internal_type_to_user_recovery_message():
    error = safe_error("login_required", "session rejected")
    assert error == {
        "error_type": "login_required",
        "user_message": "抖音登录已失效，请重新扫码后继续任务。",
        "technical_detail": "session rejected",
        "recoverable": True,
        "recommended_action": "reconnect",
    }


def test_capabilities_and_dashboard_overview(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")

    async def seed():
        await store.initialize()
        run_id = await store.create_run({
            "platform": "dy", "crawler_type": "topic", "topics": "人工智能",
            "max_notes_count": 20,
        })
        await store.upsert_task_item(run_id, "aweme-1", "detail", "completed", 1)

    asyncio.run(seed())
    monkeypatch.setattr(dashboard_router, "task_store", store)
    client = TestClient(app)

    capabilities = client.get("/api/system/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["mode"] == "local"
    assert capabilities.json()["features"]["local_crawl"] is True

    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["recent_runs"][0]["display_name"] == "话题：人工智能"
    assert body["recent_runs"][0]["progress"]["completed"] == 1
    assert body["task_counts"]["queued"] == 1
    assert {"health_summary", "storage_summary", "library_counts"} <= set(body)


def test_remote_capabilities_do_not_trust_role_header_without_proxy_token(monkeypatch):
    monkeypatch.setenv("FLOWLENS_REMOTE_WORKER", "true")
    monkeypatch.setenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "trusted-secret")
    client = TestClient(app)

    forged = client.get("/api/system/capabilities", headers={"X-FlowLens-Role": "admin"})
    assert forged.status_code == 200
    assert forged.json()["current_role"] == "user"
    assert forged.json()["features"]["admin"] is False

    trusted = client.get(
        "/api/system/capabilities",
        headers={"X-FlowLens-Proxy-Token": "trusted-secret", "X-FlowLens-Role": "admin"},
    )
    assert trusted.status_code == 200
    assert trusted.json()["current_role"] == "admin"
    assert trusted.json()["features"]["admin"] is True
