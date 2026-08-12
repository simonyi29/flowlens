from fastapi.testclient import TestClient

from api.main import app


def test_system_health_and_storage_are_structured():
    client = TestClient(app)
    health = client.get("/api/system/health")
    assert health.status_code == 200
    assert {"cdp","faster_whisper","ffprobe","sqlite_fts5","media_writable"} <= set(health.json()["checks"])
    storage = client.get("/api/system/storage")
    assert storage.status_code == 200
    assert storage.json()["library_limit_bytes"] == 20 * 1024 ** 3


def test_library_rejects_unsupported_export_format():
    client = TestClient(app)
    response = client.get("/api/library/export?format=x")
    assert response.status_code in {404, 422}
