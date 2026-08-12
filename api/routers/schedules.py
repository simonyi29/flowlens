import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..schemas.crawler import CrawlerStartRequest
from ..schemas.schedule import ScheduleRequest, next_occurrence
from ..services import crawler_manager
from ..services.task_store import task_store

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def list_schedules(): return {"items": await task_store.list_schedules()}


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: str):
    item = await task_store.get_schedule(schedule_id)
    if not item: raise HTTPException(404, "Schedule not found")
    return item


@router.post("")
async def create_schedule(request: ScheduleRequest):
    payload = request.model_dump(mode="json")
    base = request.next_run_at or request.run_at or datetime.now(timezone.utc)
    payload["next_run_at"] = base.isoformat()
    schedule_id = await task_store.save_schedule(payload)
    return {"schedule_id": schedule_id}


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: str, request: ScheduleRequest):
    if not await task_store.get_schedule(schedule_id): raise HTTPException(404, "Schedule not found")
    payload = request.model_dump(mode="json")
    payload["next_run_at"] = (request.next_run_at or request.run_at or datetime.now(timezone.utc)).isoformat()
    await task_store.save_schedule(payload, schedule_id)
    return {"schedule_id": schedule_id}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str):
    await task_store.delete_schedule(schedule_id); return {"status": "deleted"}


@router.post("/{schedule_id}/run-now")
async def run_now(schedule_id: str):
    item = await task_store.get_schedule(schedule_id)
    if not item: raise HTTPException(404, "Schedule not found")
    config = json.loads(item["config_json"])
    config.update({"platform":"dy", "crawler_type":item["crawler_type"]})
    config["schedule_id"] = schedule_id
    if await task_store.schedule_has_active_run(schedule_id): raise HTTPException(409, "Schedule already has an active run")
    if item["crawler_type"] == "creator": config["creator_ids"] = item["source"]
    else: config["topics"] = item["source"]
    run_id = await crawler_manager.enqueue(CrawlerStartRequest.model_validate(config))
    now = datetime.now(timezone.utc)
    nxt = next_occurrence(item["interval_type"], item["interval_value"], now)
    await task_store.mark_schedule_run(schedule_id, now.isoformat(), nxt.isoformat() if nxt else None)
    return {"run_id": run_id}
