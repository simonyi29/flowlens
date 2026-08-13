import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

import api.routers.media as media_router
from api.main import app
from api.services.task_store import TaskStore


def test_media_stream_range_and_delete_confirmation(tmp_path, monkeypatch):
    root = tmp_path / "media"; file = root / "creator" / "aweme" / "video.mp4"
    file.parent.mkdir(parents=True); file.write_bytes(b"0123456789")
    store = TaskStore(tmp_path / "tasks.sqlite")
    monkeypatch.setattr(media_router, "MEDIA_ROOT", root.resolve())
    monkeypatch.setattr(media_router, "task_store", store)
    async def seed():
        await store.initialize()
        return await store.upsert_media({"aweme_id":"range-test","kind":"video","status":"completed","path":str(file),"size_bytes":10,"mime_type":"video/mp4"})
    asset_id=asyncio.run(seed()); client=TestClient(app)
    response=client.get(f"/api/media/{asset_id}/stream",headers={"Range":"bytes=2-5"})
    assert response.status_code==206 and response.content==b"2345"
    assert response.headers["content-range"]=="bytes 2-5/10"
    assert client.delete(f"/api/media/{asset_id}").status_code==409
    assert client.delete(f"/api/media/{asset_id}?confirm=true").status_code==200
    assert not file.exists()


def test_media_path_outside_library_is_rejected(tmp_path, monkeypatch):
    root=tmp_path/"media"; root.mkdir(); outside=tmp_path/"secret.mp4"; outside.write_bytes(b"secret")
    store = TaskStore(tmp_path / "tasks.sqlite")
    monkeypatch.setattr(media_router,"MEDIA_ROOT",root.resolve())
    monkeypatch.setattr(media_router, "task_store", store)
    async def seed():
        await store.initialize()
        return await store.upsert_media({"aweme_id":"unsafe","kind":"video","status":"completed","path":str(outside)})
    asset_id=asyncio.run(seed())
    assert TestClient(app).get(f"/api/media/{asset_id}/stream").status_code==403


def test_media_catalog_filters_paginates_and_hides_deleted_by_default(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.sqlite")
    monkeypatch.setattr(media_router, "task_store", store)

    async def seed():
        await store.initialize()
        for index, (kind, status, size) in enumerate((
            ("video", "completed", 30),
            ("cover", "completed", 20),
            ("video", "failed", 0),
            ("video", "deleted", 0),
        )):
            await store.upsert_media({
                "asset_id": f"asset-{index}", "aweme_id": f"aweme-{index}",
                "creator_hash": "creator-a", "kind": kind, "status": status,
                "size_bytes": size,
            })

    asyncio.run(seed())
    client = TestClient(app)

    first_page = client.get("/api/media", params={"limit": 2, "sort": "largest"})
    assert first_page.status_code == 200
    body = first_page.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["active_total"] == 3
    assert body["status_counts"] == {"completed": 2, "deleted": 1, "failed": 1}
    assert body["kind_counts"] == {"cover": 1, "video": 2}
    assert [item["size_bytes"] for item in body["items"]] == [30, 20]

    filtered = client.get("/api/media", params={"q": "aweme-1", "kind": "cover"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["asset_id"] == "asset-1"

    deleted = client.get("/api/media", params={"status": "deleted"})
    assert deleted.status_code == 200
    assert deleted.json()["total"] == 1
    assert deleted.json()["items"][0]["status"] == "deleted"


def test_media_catalog_rejects_unknown_filters(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.sqlite")
    asyncio.run(store.initialize())
    monkeypatch.setattr(media_router, "task_store", store)
    client = TestClient(app)
    assert client.get("/api/media", params={"kind": "archive"}).status_code == 422
    assert client.get("/api/media", params={"status": "unknown"}).status_code == 422
    assert client.get("/api/media", params={"sort": "random"}).status_code == 422
