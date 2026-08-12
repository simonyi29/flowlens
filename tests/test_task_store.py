import asyncio
import json

from api.services.task_store import TaskStore


def test_task_store_persists_runs_logs_and_recovers_running(tmp_path):
    path = tmp_path / "tasks.sqlite"

    async def scenario():
        store = TaskStore(path)
        await store.initialize()
        run_id = await store.create_run({"platform": "dy", "crawler_type": "search", "cookies":"secret", "static_proxy_url":"http://u:p@host"})
        assert (await store.next_queued_run())["run_id"] == run_id
        await store.update_run(run_id, "running", stage="detail")
        await store.add_log(run_id, "info", "safe log")
        assert (await store.get_run(run_id))["status"] == "running"
        assert (await store.list_logs(run_id))[0]["message"] == "safe log"
        restarted = TaskStore(path)
        await restarted.initialize()
        recovered = await restarted.get_run(run_id)
        assert recovered["status"] == "partial"
        assert recovered["stage"] == "detail"
        assert json.loads(recovered["config_json"])["platform"] == "dy"
        assert "secret" not in recovered["config_json"]
        assert "u:p" not in recovered["config_json"]

    asyncio.run(scenario())
