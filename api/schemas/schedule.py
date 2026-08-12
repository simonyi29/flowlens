from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator


class ScheduleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    platform: Literal["dy"] = "dy"
    crawler_type: Literal["creator", "topic"]
    source: str = Field(min_length=1)
    interval_type: Literal["once", "hourly", "daily"]
    interval_value: int = Field(default=1, ge=1, le=365)
    run_at: datetime | None = None
    timezone: str = "Asia/Shanghai"
    config: dict[str, Any] = Field(default_factory=dict)
    next_run_at: datetime | None = None

    @model_validator(mode="after")
    def validate_schedule(self):
        ZoneInfo(self.timezone)
        if self.interval_type == "once" and not self.run_at:
            raise ValueError("run_at is required for a once schedule")
        return self


def next_occurrence(interval_type: str, interval_value: int, base: datetime) -> datetime | None:
    if interval_type == "once": return None
    return base + (timedelta(hours=interval_value) if interval_type == "hourly" else timedelta(days=interval_value))
