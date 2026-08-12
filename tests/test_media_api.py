import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

import api.routers.media as media_router
from api.main import app
from api.services.task_store import task_store


def test_media_stream_range_and_delete_confirmation(tmp_path, monkeypatch):
    root = tmp_path / "media"; file = root / "creator" / "aweme" / "video.mp4"
    file.parent.mkdir(parents=True); file.write_bytes(b"0123456789")
    monkeypatch.setattr(media_router, "MEDIA_ROOT", root.resolve())
    async def seed():
        await task_store.initialize()
        return await task_store.upsert_media({"aweme_id":"range-test","kind":"video","status":"completed","path":str(file),"size_bytes":10,"mime_type":"video/mp4"})
    asset_id=asyncio.run(seed()); client=TestClient(app)
    response=client.get(f"/api/media/{asset_id}/stream",headers={"Range":"bytes=2-5"})
    assert response.status_code==206 and response.content==b"2345"
    assert response.headers["content-range"]=="bytes 2-5/10"
    assert client.delete(f"/api/media/{asset_id}").status_code==409
    assert client.delete(f"/api/media/{asset_id}?confirm=true").status_code==200
    assert not file.exists()


def test_media_path_outside_library_is_rejected(tmp_path, monkeypatch):
    root=tmp_path/"media"; root.mkdir(); outside=tmp_path/"secret.mp4"; outside.write_bytes(b"secret")
    monkeypatch.setattr(media_router,"MEDIA_ROOT",root.resolve())
    async def seed(): return await task_store.upsert_media({"aweme_id":"unsafe","kind":"video","status":"completed","path":str(outside)})
    asset_id=asyncio.run(seed())
    assert TestClient(app).get(f"/api/media/{asset_id}/stream").status_code==403
