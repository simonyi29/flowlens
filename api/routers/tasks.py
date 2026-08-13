from fastapi import APIRouter, HTTPException

from ..services import crawler_manager
from ..services.product_views import present_run, safe_error
from ..services.task_store import task_store

router = APIRouter(prefix="/tasks", tags=["tasks"])
TASK_STATUSES = {
    "queued", "running", "pausing", "paused", "waiting_for_login",
    "waiting_for_space", "partial", "completed", "failed", "cancelled",
}


@router.get("")
async def list_tasks(limit: int = 10, offset: int = 0, status: str | None = None):
    if status and status not in TASK_STATUSES:
        raise HTTPException(422, "Unsupported task status")
    safe_limit = min(max(limit, 1), 100)
    safe_offset = max(offset, 0)
    rows = await task_store.list_runs(safe_limit, safe_offset, status)
    items = []
    for row in rows:
        items.append(present_run(
            row,
            stages=await task_store.list_stages(row["run_id"]),
            summary=await task_store.run_summary(row["run_id"]),
        ))
    return {
        "items": items,
        "total": await task_store.count_runs(status),
        "status_counts": await task_store.run_status_counts(),
        "limit": safe_limit,
        "offset": safe_offset,
    }


@router.get("/{run_id}")
async def get_task(run_id: str):
    item = await task_store.get_run(run_id)
    if not item:
        raise HTTPException(404, "Task not found")
    summary = await task_store.run_summary(run_id)
    stages = await task_store.list_stages(run_id)
    return {
        **present_run(item, stages=stages, summary=summary),
        "summary": summary,
        "stages": stages,
        "error": safe_error(item.get("error_type"), item.get("error_message")),
    }


@router.get("/{run_id}/items")
async def get_task_items(run_id: str):
    return {"items": await task_store.list_items(run_id), "stages": await task_store.list_stages(run_id)}


@router.get("/{run_id}/logs")
async def get_task_logs(run_id: str, limit: int = 500):
    return {"logs": await task_store.list_logs(run_id, min(max(limit, 1), 2000))}


@router.post("/{run_id}/pause")
async def pause_task(run_id: str):
    if not await crawler_manager.pause(run_id):
        raise HTTPException(409, "Task cannot be paused")
    return {"status": "paused", "run_id": run_id}


@router.post("/{run_id}/resume")
async def resume_task(run_id: str):
    if not await crawler_manager.resume(run_id):
        raise HTTPException(409, "Task cannot be resumed")
    return {"status": "queued", "run_id": run_id}


@router.post("/{run_id}/continue-after-login")
async def continue_after_login(run_id: str):
    if not await crawler_manager.continue_after_login(run_id):
        raise HTTPException(409, "Task is not waiting for login or verification")
    return {"status": "queued", "run_id": run_id}


@router.post("/{run_id}/cancel")
async def cancel_task(run_id: str):
    if not await crawler_manager.cancel(run_id):
        raise HTTPException(409, "Task cannot be cancelled")
    return {"status": "cancelled", "run_id": run_id}


@router.post("/{run_id}/retry-failed")
async def retry_failed(run_id: str):
    new_run_id = await crawler_manager.retry(run_id)
    if not new_run_id:
        raise HTTPException(409, "Task cannot be retried")
    return {"status": "queued", "run_id": new_run_id}
