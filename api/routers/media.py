from pathlib import Path

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse, StreamingResponse

from ..services.task_store import task_store

router = APIRouter(prefix="/media", tags=["media"])
MEDIA_ROOT = (Path(__file__).resolve().parents[2] / "data" / "douyin" / "media").resolve()
MEDIA_KINDS = {"video", "image", "cover", "music"}
MEDIA_STATUSES = {"active", "completed", "downloading", "partial", "waiting_for_space", "failed", "deleted"}
MEDIA_SORTS = {"newest", "oldest", "largest"}


def _safe_asset_path(item: dict) -> Path:
    value = item.get("path")
    if not value:
        raise HTTPException(404, "Media file is unavailable")
    path = Path(value).resolve()
    if path == MEDIA_ROOT or MEDIA_ROOT not in path.parents:
        raise HTTPException(403, "Media path is outside the library")
    return path


@router.get("")
async def list_media(
    limit: int = 12,
    offset: int = 0,
    aweme_id: str | None = None,
    q: str | None = None,
    kind: str | None = None,
    status: str = "active",
    sort: str = "newest",
):
    if kind and kind not in MEDIA_KINDS:
        raise HTTPException(422, "Unsupported media kind")
    if status not in MEDIA_STATUSES:
        raise HTTPException(422, "Unsupported media status")
    if sort not in MEDIA_SORTS:
        raise HTTPException(422, "Unsupported media sort")
    safe_limit = min(max(limit, 1), 100)
    safe_offset = max(offset, 0)
    query = (q or "").strip()[:200] or None
    items = await task_store.list_media(
        safe_limit,
        safe_offset,
        aweme_id,
        query=query,
        kind=kind,
        status=status,
        sort=sort,
    )
    summary = await task_store.media_catalog_summary(
        aweme_id=aweme_id,
        query=query,
        kind=kind,
        status=status,
    )
    return {"items": items, **summary, "limit": safe_limit, "offset": safe_offset}


@router.get("/{asset_id}")
async def get_media(asset_id: str):
    item = await task_store.get_media(asset_id)
    if not item: raise HTTPException(404, "Media asset not found")
    return item


@router.get("/{asset_id}/stream")
async def stream_media(asset_id: str, range_header: str | None = Header(default=None, alias="Range")):
    item = await task_store.get_media(asset_id)
    if not item: raise HTTPException(404, "Media asset not found")
    path = _safe_asset_path(item)
    if not path.is_file(): raise HTTPException(404, "Media file not found")
    if not range_header:
        return FileResponse(path, media_type=item.get("mime_type") or None, filename=path.name)
    try:
        value = range_header.removeprefix("bytes=").split(",", 1)[0]
        start_text, end_text = value.split("-", 1)
        size = path.stat().st_size
        start = int(start_text or 0); end = min(int(end_text) if end_text else size - 1, size - 1)
        if start < 0 or start > end: raise ValueError
    except ValueError:
        raise HTTPException(416, "Invalid byte range")
    def chunks():
        with path.open("rb") as fh:
            fh.seek(start); remaining=end-start+1
            while remaining:
                chunk=fh.read(min(1024*1024,remaining))
                if not chunk: break
                remaining-=len(chunk); yield chunk
    return StreamingResponse(chunks(), status_code=206, media_type=item.get("mime_type") or "application/octet-stream",
        headers={"Content-Range":f"bytes {start}-{end}/{size}","Accept-Ranges":"bytes","Content-Length":str(end-start+1)})


@router.delete("/{asset_id}")
async def delete_media(asset_id: str, confirm: bool = False):
    if not confirm: raise HTTPException(409, "Explicit confirmation is required")
    item = await task_store.get_media(asset_id)
    if not item: raise HTTPException(404, "Media asset not found")
    path = _safe_asset_path(item)
    deleted_file = path.is_file()
    if deleted_file: path.unlink()
    item.update({"asset_id": asset_id, "status": "deleted", "path": None, "size_bytes": 0})
    await task_store.upsert_media(item)
    return {"status": "deleted", "asset_id": asset_id, "deleted_file": deleted_file}
