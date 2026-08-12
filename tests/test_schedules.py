from datetime import datetime, timezone, timedelta
import asyncio

import pytest
from pydantic import ValidationError

from api.schemas.schedule import ScheduleRequest, initial_occurrence, next_occurrence
from api.services.schedule_runner import ScheduleRunner


def test_schedule_only_accepts_douyin_creator_or_topic():
    item = ScheduleRequest(name="daily", crawler_type="creator", source="creator", interval_type="daily")
    assert item.platform == "dy"
    with pytest.raises(ValidationError):
        ScheduleRequest(name="bad", crawler_type="search", source="x", interval_type="daily")


def test_schedule_next_occurrence_and_once_validation():
    now = datetime.now(timezone.utc)
    assert next_occurrence("hourly", 2, now) == now + timedelta(hours=2)
    assert next_occurrence("daily", 3, now) == now + timedelta(days=3)
    assert next_occurrence("once", 1, now) is None
    with pytest.raises(ValidationError):
        ScheduleRequest(name="once", crawler_type="topic", source="ai", interval_type="once")


def test_daily_schedule_preserves_configured_local_clock():
    run_at = datetime(2026, 8, 13, 9, 30)  # naive input is Asia/Shanghai wall time
    request = ScheduleRequest(
        name="daily", crawler_type="creator", source="creator", interval_type="daily",
        run_at=run_at, timezone="Asia/Shanghai",
    )
    first = initial_occurrence(request, datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc))
    assert first == datetime(2026, 8, 13, 1, 30, tzinfo=timezone.utc)
    following = next_occurrence("daily", 1, first, run_at=run_at, timezone_name="Asia/Shanghai")
    assert following == datetime(2026, 8, 14, 1, 30, tzinfo=timezone.utc)


def test_missed_schedule_enqueues_only_one_catchup_run():
    now=datetime(2026,8,12,1,tzinfo=timezone.utc)
    class Store:
        def __init__(self): self.marked=[]; self.active=False
        async def due_schedules(self,_now):
            return [] if self.marked else [{"schedule_id":"s1","crawler_type":"creator","source":"creator",
                "config_json":"{}","interval_type":"daily","interval_value":1,"run_at":None,"timezone":"Asia/Shanghai"}]
        async def schedule_has_active_run(self,_id): return self.active
        async def mark_schedule_run(self,*args): self.marked.append(args)
    class Manager:
        def __init__(self): self.items=[]
        async def enqueue(self,item): self.items.append(item)
    store=Store(); manager=Manager(); runner=ScheduleRunner(store=store,manager=manager,clock=lambda:now)
    asyncio.run(runner.tick()); asyncio.run(runner.tick())
    assert len(manager.items)==1
    assert len(store.marked)==1
