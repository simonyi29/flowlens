import asyncio
import json

import pytest
from fastapi import HTTPException

from api.routers import data as data_router


def test_jsonl_preview_streams_records_and_counts_total(monkeypatch, tmp_path):
    target = tmp_path / "douyin" / "contents.jsonl"
    target.parent.mkdir()
    target.write_text(
        "\n".join(json.dumps({"id": i}) for i in range(5)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path)

    info = data_router.get_file_info(target)
    response = asyncio.run(data_router.get_file_content("douyin/contents.jsonl", limit=2))

    assert info["record_count"] == 5
    assert response == {"data": [{"id": 0}, {"id": 1}], "total": 5}


def test_jsonl_preview_reports_corrupt_line_number(monkeypatch, tmp_path):
    target = tmp_path / "comments.jsonl"
    target.write_text('{"id": 1}\nnot-json\n', encoding="utf-8")
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path)

    with pytest.raises(HTTPException) as error:
        asyncio.run(data_router.get_file_content("comments.jsonl"))

    assert error.value.status_code == 400
    assert "line 2" in error.value.detail
