from fastapi.testclient import TestClient

from api.main import app


def test_system_health_and_storage_are_structured():
    client = TestClient(app)
    health = client.get("/api/system/health")
    assert health.status_code == 200
    assert {"cdp","faster_whisper","ffprobe","sqlite_fts5","media_writable"} <= set(health.json()["checks"])
    assert health.json()["checks"]["faster_whisper"]["device"] in {"cpu", "cuda"}
    storage = client.get("/api/system/storage")
    assert storage.status_code == 200
    assert storage.json()["library_limit_bytes"] == 20 * 1024 ** 3


def test_library_rejects_unsupported_export_format():
    client = TestClient(app)
    response = client.get("/api/library/export?format=x")
    assert response.status_code in {404, 422}


def test_library_stats_returns_local_aggregates(monkeypatch, tmp_path):
    import sqlite3
    from api.routers import library

    db_path = tmp_path / "library.sqlite"
    with sqlite3.connect(db_path) as db:
        db.executescript("""
        CREATE TABLE douyin_aweme (source_topic TEXT, liked_count INTEGER, comment_count INTEGER, share_count INTEGER);
        CREATE TABLE douyin_creator (creator_hash TEXT);
        CREATE TABLE douyin_topic (topic_id TEXT);
        CREATE TABLE douyin_aweme_comment (comment_id TEXT, aweme_id TEXT, content TEXT, like_count INTEGER, level INTEGER);
        CREATE TABLE douyin_transcript (status TEXT);
        INSERT INTO douyin_aweme VALUES ('测试话题', 10, 2, 1);
        INSERT INTO douyin_creator VALUES ('hash');
        INSERT INTO douyin_topic VALUES ('topic');
        INSERT INTO douyin_aweme_comment VALUES ('c1','a1','一级',9,1),('c2','a1','二级',2,2);
        INSERT INTO douyin_transcript VALUES ('native_completed');
        """)
    monkeypatch.setattr(library, "DB_PATH", db_path)
    response = TestClient(app).get("/api/library/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {"awemes":1,"creators":1,"topics":1,"comments":2,"replies":1,"transcripts":1}
    assert body["topic_engagement"][0]["avg_engagement"] == 13.0
