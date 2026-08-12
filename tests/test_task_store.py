import asyncio
import json
import sqlite3

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
        await store.upsert_task_item(run_id, "a1", "comments", "running", 0.1)
        assert (await store.get_run(run_id))["stage"] == "comments"
        await store.upsert_task_item(run_id, "a1", "comments", "completed", 1)
        await store.upsert_task_item(run_id, "a2", "comments", "failed", 0, "unknown", "boom")
        assert await store.retry_failed_items(run_id) == 1
        retried = next(x for x in await store.list_items(run_id) if x["entity_id"] == "a2")
        assert retried["status"] == "queued"
        comment_stage = next(x for x in await store.list_stages(run_id) if x["stage"] == "comments")
        assert comment_stage["status"] == "queued"
        assert comment_stage["completed_count"] == 1
        assert (await store.get_run(run_id))["status"] == "running"
        assert (await store.list_logs(run_id))[0]["message"] == "safe log"
        restarted = TaskStore(path)
        await restarted.initialize()
        recovered = await restarted.get_run(run_id)
        assert recovered["status"] == "partial"
        assert recovered["stage"] == "comments"
        assert json.loads(recovered["config_json"])["platform"] == "dy"
        assert "secret" not in recovered["config_json"]
        assert "u:p" not in recovered["config_json"]

    asyncio.run(scenario())


def test_media_failure_and_completion_share_asset_identity(tmp_path):
    async def scenario():
        store=TaskStore(tmp_path/"tasks.sqlite"); await store.initialize()
        part=tmp_path/"video.mp4.part"; final=tmp_path/"video.mp4"
        failed=await store.upsert_media({"aweme_id":"a1","kind":"video","status":"failed","part_path":str(part)})
        completed=await store.upsert_media({"aweme_id":"a1","kind":"video","status":"completed","path":str(final),"size_bytes":10})
        assert failed==completed
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT COUNT(*) FROM media_asset WHERE aweme_id='a1'").fetchone()[0]==1
    asyncio.run(scenario())


def test_resuming_run_clears_previous_finished_time(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        await store.initialize()
        run_id = await store.create_run({"platform":"dy","crawler_type":"search"})
        await store.update_run(run_id, "partial")
        assert (await store.get_run(run_id))["finished_at"]
        await store.update_run(run_id, "queued")
        assert (await store.get_run(run_id))["finished_at"] is None
    asyncio.run(scenario())
