from datetime import datetime, timedelta, timezone
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


def normalize_utc(value: datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    localized = value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    return localized.astimezone(timezone.utc)


def initial_occurrence(request: ScheduleRequest, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if request.run_at:
        candidate = normalize_utc(request.run_at, request.timezone)
        if candidate > now or request.interval_type == "once":
            return candidate
    return next_occurrence(
        request.interval_type, request.interval_value, now,
        run_at=request.run_at, timezone_name=request.timezone,
    ) or now


def next_occurrence(interval_type: str, interval_value: int, base: datetime,
                    *, run_at: datetime | None = None,
                    timezone_name: str = "Asia/Shanghai") -> datetime | None:
    base = normalize_utc(base, timezone_name)
    if interval_type == "once":
        return None
    if interval_type == "hourly":
        return base + timedelta(hours=interval_value)
    zone = ZoneInfo(timezone_name)
    local_base = base.astimezone(zone)
    target = local_base + timedelta(days=interval_value)
    if run_at:
        local_run = run_at.replace(tzinfo=zone) if run_at.tzinfo is None else run_at.astimezone(zone)
        target = target.replace(hour=local_run.hour, minute=local_run.minute, second=local_run.second, microsecond=0)
    return target.astimezone(timezone.utc)
