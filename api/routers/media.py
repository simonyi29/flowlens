from pathlib import Path

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse, StreamingResponse

from ..services.task_store import task_store

router = APIRouter(prefix="/media", tags=["media"])
MEDIA_ROOT = (Path(__file__).resolve().parents[2] / "data" / "douyin" / "media").resolve()


def _safe_asset_path(item: dict) -> Path:
    value = item.get("path")
    if not value:
        raise HTTPException(404, "Media file is unavailable")
    path = Path(value).resolve()
    if path == MEDIA_ROOT or MEDIA_ROOT not in path.parents:
        raise HTTPException(403, "Media path is outside the library")
    return path


@router.get("")
async def list_media(limit: int = 100, offset: int = 0, aweme_id: str | None = None):
    return {"items": await task_store.list_media(min(max(limit, 1), 500), max(offset, 0), aweme_id)}


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
    if path.is_file(): path.unlink()
    item.update({"asset_id": asset_id, "status": "deleted", "path": None, "size_bytes": 0})
    await task_store.upsert_media(item)
    return {"status": "deleted", "asset_id": asset_id}
