import asyncio

from fastapi.testclient import TestClient

from api.main import app
from api.routers import tasks as tasks_router
from api.services.task_store import TaskStore


def test_task_list_is_paginated_and_reports_status_counts(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")

    async def seed():
        await store.initialize()
        first = await store.create_run({
            "platform": "dy", "crawler_type": "search",
            "keywords": "新能源汽车", "max_notes_count": 12,
        })
        second = await store.create_run({
            "platform": "dy", "crawler_type": "topic",
            "topics": "人工智能", "max_notes_count": 8,
        })
        await store.update_run(first, "completed", stage="finalize")
        await store.update_run(second, "cancelled", stage="finalize", error_type="cancelled")

    asyncio.run(seed())
    monkeypatch.setattr(tasks_router, "task_store", store)
    client = TestClient(app)

    response = client.get("/api/tasks", params={"limit": 1, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] == 2
    assert body["status_counts"] == {"cancelled": 1, "completed": 1}
    assert body["limit"] == 1
    assert body["offset"] == 0

    completed = client.get("/api/tasks", params={"status": "completed"})
    assert completed.status_code == 200
    assert completed.json()["total"] == 1
    assert completed.json()["items"][0]["display_name"] == "关键词：新能源汽车"


def test_task_list_rejects_unknown_status(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    asyncio.run(store.initialize())
    monkeypatch.setattr(tasks_router, "task_store", store)
    client = TestClient(app)
    response = client.get("/api/tasks", params={"status": "not-a-status"})
    assert response.status_code == 422


def test_cancelled_partial_runs_are_repaired_without_deleting_history(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")

    async def exercise():
        await store.initialize()
        run_id = await store.create_run({
            "platform": "dy", "crawler_type": "search", "keywords": "测试",
        })
        await store.update_run(
            run_id, "partial", stage="finalize",
            error_type="cancelled", error_message="crawler cancelled",
        )
        # initialize() is deliberately idempotent and also runs safe repairs.
        store._initialized = False
        await store.initialize()
        return await store.get_run(run_id)

    row = asyncio.run(exercise())
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["error_type"] == "cancelled"


def test_rerun_endpoint_clones_sanitized_config_into_new_run(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")

    class Manager:
        async def rerun(self, run_id: str):
            source = await store.get_run(run_id)
            if not source or source["status"] not in {"completed", "cancelled", "partial"}:
                return None
            return await store.create_run(__import__("json").loads(source["config_json"]))

    async def seed():
        await store.initialize()
        run_id = await store.create_run({
            "platform": "dy", "crawler_type": "search",
            "keywords": "编程副业", "max_notes_count": 15,
        })
        await store.update_run(run_id, "cancelled", stage="finalize", error_type="cancelled")
        return run_id

    source_run_id = asyncio.run(seed())
    monkeypatch.setattr(tasks_router, "task_store", store)
    monkeypatch.setattr(tasks_router, "crawler_manager", Manager())
    response = TestClient(app).post(f"/api/tasks/{source_run_id}/rerun")
    assert response.status_code == 200
    assert response.json()["source_run_id"] == source_run_id
    assert response.json()["run_id"] != source_run_id
    cloned = asyncio.run(store.get_run(response.json()["run_id"]))
    assert cloned["status"] == "queued"
    assert __import__("json").loads(cloned["config_json"])["keywords"] == "编程副业"

    missing_confirm = TestClient(app).delete(f"/api/tasks/{source_run_id}")
    assert missing_confirm.status_code == 409
    deleted = TestClient(app).delete(
        f"/api/tasks/{source_run_id}", params={"confirm": "true"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["results_preserved"] is True
    assert asyncio.run(store.get_run(source_run_id)) is None
