"""Product-oriented dashboard aggregates for the FlowLens WebUI."""
from __future__ import annotations

import hmac
import os
from collections import Counter

from fastapi import APIRouter, Depends, Header, HTTPException

from . import library as library_router
from .system import health, storage
from ..services.product_views import present_run
from ..services.task_store import task_store

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _remote_enabled() -> bool:
    return os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1", "true", "yes"}


async def dashboard_identity(
    x_flowlens_proxy_token: str | None = Header(None),
    x_flowlens_user_id: str | None = Header(None),
) -> str:
    if not _remote_enabled():
        return "__local__"
    expected = os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    if not expected or not x_flowlens_proxy_token or not hmac.compare_digest(expected, x_flowlens_proxy_token):
        raise HTTPException(401, "trusted proxy authentication required")
    if not x_flowlens_user_id or len(x_flowlens_user_id) > 128:
        raise HTTPException(401, "authenticated user context required")
    return x_flowlens_user_id


async def _local_runs() -> list[dict]:
    rows = await task_store.list_runs(5, 0)
    result = []
    for row in rows:
        result.append(present_run(
            row,
            stages=await task_store.list_stages(row["run_id"]),
            summary=await task_store.run_summary(row["run_id"]),
        ))
    return result


async def _remote_runs(user_id: str) -> list[dict]:
    rows = await task_store.list_user_remote_runs(user_id)
    connections = {item["connection_id"]: item for item in await task_store.list_user_connections(user_id)}
    return [present_run(
        row,
        account_label=(connections.get(row["connection_id"], {}).get("masked_nickname") or "抖音账号"),
        remote=True,
    ) for row in rows[:5]]


@router.get("/overview")
async def overview(user_id: str = Depends(dashboard_identity)):
    remote = _remote_enabled()
    recent_runs = await (_remote_runs(user_id) if remote else _local_runs())
    all_runs = await (task_store.list_user_remote_runs(user_id) if remote else task_store.list_runs(500, 0))
    task_counts = Counter(str(item.get("status") or "unknown") for item in all_runs)
    if remote:
        connections = await task_store.list_user_connections(user_id)
        connection = next((item for item in connections if item.get("status") == "connected"), connections[0] if connections else None)
        library_counts = await task_store.remote_result_counts(user_id)
    else:
        connection = None
        try:
            library_counts = (await library_router.library_stats())["counts"]
        except HTTPException:
            library_counts = {key: 0 for key in ("awemes", "creators", "topics", "comments", "replies", "transcripts")}
    if not remote:
        library_counts = {**library_counts, "media": await task_store.media_count()}
    health_data = await health()
    storage_data = await storage()
    return {
        "connection": connection,
        "task_counts": dict(task_counts),
        "recent_runs": recent_runs,
        "library_counts": library_counts,
        "health_summary": health_data,
        "storage_summary": storage_data,
    }
