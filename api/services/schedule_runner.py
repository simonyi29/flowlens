import asyncio
import json
from datetime import datetime, timezone

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
