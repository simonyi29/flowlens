import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..schemas.crawler import CrawlerStartRequest
from ..schemas.schedule import ScheduleRequest, initial_occurrence, next_occurrence
from ..services import crawler_manager
from ..services.task_store import task_store
from ..services.auth import Identity, require_password_changed, remote_mode
from .remote import _enqueue_worker_command

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def list_schedules(identity: Identity = Depends(require_password_changed)):
    return {"items": await (task_store.list_user_schedules(identity.user_id) if remote_mode() else task_store.list_schedules())}


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: str, identity: Identity = Depends(require_password_changed)):
    item = await (task_store.get_user_schedule(schedule_id, identity.user_id) if remote_mode() else task_store.get_schedule(schedule_id))
    if not item: raise HTTPException(404, "Schedule not found")
    return item


@router.post("")
async def create_schedule(request: ScheduleRequest, identity: Identity = Depends(require_password_changed)):
    payload = request.model_dump(mode="json")
    if remote_mode():
        if not request.connection_id:
            raise HTTPException(422, "connection_id is required in remote mode")
        connection = await task_store.get_user_connection(request.connection_id, identity.user_id)
        if not connection or connection["status"] != "connected":
            raise HTTPException(409, "Douyin connection is not ready")
        payload.update({"user_id":identity.user_id, "connection_id":request.connection_id})
    payload["next_run_at"] = (request.next_run_at or initial_occurrence(request)).isoformat()
    schedule_id = await task_store.save_schedule(payload)
    return {"schedule_id": schedule_id}


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: str, request: ScheduleRequest, identity: Identity = Depends(require_password_changed)):
    existing = await (task_store.get_user_schedule(schedule_id, identity.user_id) if remote_mode() else task_store.get_schedule(schedule_id))
    if not existing: raise HTTPException(404, "Schedule not found")
    payload = request.model_dump(mode="json")
    if remote_mode():
        connection_id = request.connection_id or existing.get("connection_id")
        connection = await task_store.get_user_connection(connection_id, identity.user_id)
        if not connection or connection["status"] != "connected": raise HTTPException(409, "Douyin connection is not ready")
        payload.update({"user_id":identity.user_id,"connection_id":connection_id})
    payload["next_run_at"] = (request.next_run_at or initial_occurrence(request)).isoformat()
    await task_store.save_schedule(payload, schedule_id)
    return {"schedule_id": schedule_id}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, identity: Identity = Depends(require_password_changed)):
    if remote_mode():
        if not await task_store.get_user_schedule(schedule_id, identity.user_id): raise HTTPException(404, "Schedule not found")
        await task_store.delete_user_schedule(schedule_id, identity.user_id)
    else: await task_store.delete_schedule(schedule_id)
    return {"status": "deleted"}


@router.post("/{schedule_id}/run-now")
async def run_now(schedule_id: str, identity: Identity = Depends(require_password_changed)):
    item = await (task_store.get_user_schedule(schedule_id, identity.user_id) if remote_mode() else task_store.get_schedule(schedule_id))
    if not item: raise HTTPException(404, "Schedule not found")
    config = json.loads(item["config_json"])
    config.update({"platform":"dy", "crawler_type":item["crawler_type"]})
    config["schedule_id"] = schedule_id
    if await task_store.schedule_has_active_run(schedule_id): raise HTTPException(409, "Schedule already has an active run")
    if item["crawler_type"] == "creator": config["creator_ids"] = item["source"]
    else: config["topics"] = item["source"]
    if remote_mode():
        connection = await task_store.get_user_connection(item["connection_id"], identity.user_id)
        if not connection or connection["status"] != "connected": raise HTTPException(409, "Douyin connection is not ready")
        run_id = await task_store.create_remote_run({"user_id":identity.user_id,"connection_id":item["connection_id"],"worker_id":connection["worker_id"],"config":config})
        await _enqueue_worker_command(connection["worker_id"], "crawl.start", {
            "run_id":run_id,"connection_id":item["connection_id"],"browser_profile_id":connection["profile_id"],"config":config,
        }, 86_400)
    else:
        run_id = await crawler_manager.enqueue(CrawlerStartRequest.model_validate(config))
    now = datetime.now(timezone.utc)
    run_at = datetime.fromisoformat(item["run_at"]) if item.get("run_at") else None
    nxt = next_occurrence(item["interval_type"], item["interval_value"], now,
                          run_at=run_at, timezone_name=item["timezone"])
    await task_store.mark_schedule_run(schedule_id, now.isoformat(), nxt.isoformat() if nxt else None)
    return {"run_id": run_id}
