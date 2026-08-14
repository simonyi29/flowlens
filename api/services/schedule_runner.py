import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from ..schemas.crawler import CrawlerStartRequest
from ..schemas.schedule import next_occurrence
from .crawler_manager import crawler_manager
from .task_store import task_store


class ScheduleRunner:
    def __init__(self, *, store=task_store, manager=crawler_manager, clock=None):
        self.task: asyncio.Task | None = None
        self.store, self.manager = store, manager
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def start(self):
        if not self.task or self.task.done(): self.task = asyncio.create_task(self._loop())

    async def stop(self):
        if self.task:
            self.task.cancel(); await asyncio.gather(self.task, return_exceptions=True); self.task = None

    async def tick(self):
        now = self.clock()
        for item in await self.store.due_schedules(now.isoformat()):
            if await self.store.schedule_has_active_run(item["schedule_id"]): continue
            payload = json.loads(item["config_json"])
            payload.update({"platform":"dy", "crawler_type":item["crawler_type"]})
            payload["schedule_id"] = item["schedule_id"]
            payload["creator_ids" if item["crawler_type"] == "creator" else "topics"] = item["source"]
            if item.get("user_id"):
                user = await self.store.get_user(item["user_id"])
                connection = await self.store.get_user_connection(item.get("connection_id") or "", item["user_id"])
                if not user or user["status"] != "active" or not connection or connection["status"] != "connected":
                    continue
                worker = await self.store.get_worker(connection["worker_id"])
                if not worker or worker["status"] != "online":
                    continue
                run_id = await self.store.create_remote_run({
                    "user_id":item["user_id"], "connection_id":connection["connection_id"],
                    "worker_id":connection["worker_id"], "config":payload,
                })
                issued = self.clock()
                await self.store.enqueue_outbox("worker.command", {
                    "worker_id":connection["worker_id"], "command_id":f"cmd_{uuid.uuid4().hex}",
                    "protocol_version":"1.0", "type":"crawl.start",
                    "issued_at":issued.isoformat(), "expires_at":(issued+timedelta(days=1)).isoformat(),
                    "payload":{"run_id":run_id,"connection_id":connection["connection_id"],
                               "browser_profile_id":connection["profile_id"],"config":payload},
                })
            else:
                await self.manager.enqueue(CrawlerStartRequest.model_validate(payload))
            run_at = datetime.fromisoformat(item["run_at"]) if item.get("run_at") else None
            nxt = next_occurrence(item["interval_type"], item["interval_value"], now,
                                  run_at=run_at, timezone_name=item["timezone"])
            await self.store.mark_schedule_run(item["schedule_id"], now.isoformat(), nxt.isoformat() if nxt else None)

    async def _loop(self):
        while True:
            await self.tick()
            await asyncio.sleep(30)


schedule_runner = ScheduleRunner()
