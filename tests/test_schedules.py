from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from api.schemas.schedule import ScheduleRequest, next_occurrence


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
