# -*- coding: utf-8 -*-
import pytest
import config
import asyncio
import os
import signal
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from cmd_arg import parse_cmd
from api.schemas import CrawlerStartRequest, PlatformEnum, LoginTypeEnum, CrawlerTypeEnum
from api.services.crawler_manager import CrawlerManager
from api.main import app

@pytest.mark.asyncio
async def test_cmd_arg_crawler_max_notes_count():
    # Store original values
    orig_notes = config.CRAWLER_MAX_NOTES_COUNT
    orig_comments = config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES

    try:
        await parse_cmd([
            "--platform", "xhs",
            "--crawler_max_notes_count", "42",
            "--max_comments_count_singlenotes", "24"
        ])
        assert config.CRAWLER_MAX_NOTES_COUNT == 42
        assert config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES == 24
    finally:
        config.CRAWLER_MAX_NOTES_COUNT = orig_notes
        config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = orig_comments

def test_crawler_manager_build_command():
    cm = CrawlerManager()

    # 1. No max limits passed in API request
    req1 = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords="test",
        max_notes_count=None,
        max_comments_count=None
    )
    cmd1 = cm._build_command(req1)
    # Check that the custom arguments are NOT present
    assert "--crawler_max_notes_count" not in cmd1
    assert "--max_comments_count_singlenotes" not in cmd1

    # 2. Both limits passed in API request
    req2 = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords="test",
        max_notes_count=50,
        max_comments_count=5
    )
    cmd2 = cm._build_command(req2)
    # Check that they are correctly added
    assert "--crawler_max_notes_count" in cmd2
    idx_notes = cmd2.index("--crawler_max_notes_count")
    assert cmd2[idx_notes + 1] == "50"

    assert "--max_comments_count_singlenotes" in cmd2
    idx_comments = cmd2.index("--max_comments_count_singlenotes")
    assert cmd2[idx_comments + 1] == "5"

def test_api_start_crawler_with_limits():
    client = TestClient(app)

    with patch("api.routers.crawler.crawler_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = True

        # Test case 1: with limits
        response = client.post("/api/crawler/start", json={
            "platform": "xhs",
            "login_type": "qrcode",
            "crawler_type": "search",
            "keywords": "test",
            "max_notes_count": 50,
            "max_comments_count": 5
        })

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "message": "Crawler started successfully"}

        mock_start.assert_called_once()
        called_request = mock_start.call_args[0][0]
        assert called_request.platform == PlatformEnum.XHS
        assert called_request.max_notes_count == 50
        assert called_request.max_comments_count == 5

def test_api_start_crawler_without_limits():
    client = TestClient(app)

    with patch("api.routers.crawler.crawler_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = True

        # Test case 2: without limits
        response = client.post("/api/crawler/start", json={
            "platform": "xhs",
            "login_type": "qrcode",
            "crawler_type": "search",
            "keywords": "test"
        })

        assert response.status_code == 200
        mock_start.assert_called_once()
        called_request = mock_start.call_args[0][0]
        assert called_request.platform == PlatformEnum.XHS
        assert called_request.max_notes_count is None
        assert called_request.max_comments_count is None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_notes_count", 0),
        ("max_notes_count", -1),
        ("max_notes_count", 10001),
        ("max_comments_count", -1),
        ("max_comments_count", 10001),
    ],
)
def test_api_rejects_invalid_limits(field_name, value):
    client = TestClient(app)
    payload = {
        "platform": "xhs",
        "login_type": "qrcode",
        "crawler_type": "search",
        "keywords": "test",
        field_name: value,
    }

    with patch("api.routers.crawler.crawler_manager.start", new_callable=AsyncMock) as mock_start:
        response = client.post("/api/crawler/start", json=payload)

    assert response.status_code == 422
    mock_start.assert_not_called()


def test_douyin_topic_and_enhancement_arguments_are_forwarded():
    request = CrawlerStartRequest(
        platform=PlatformEnum.DOUYIN,
        crawler_type=CrawlerTypeEnum.TOPIC,
        topics="人工智能,123",
        max_comments_count=0,
        enable_creator_profile=True,
        force_creator_refresh=True,
        enable_native_subtitle=False,
        enable_asr=True,
        asr_model="small",
        asr_language="zh",
        save_raw_payload=True,
        keep_media=False,
    )
    command = CrawlerManager()._build_command(request)
    assert command[command.index("--topics") + 1] == "人工智能,123"
    assert command[command.index("--max_comments_count_singlenotes") + 1] == "0"
    assert command[command.index("--enable_creator_profile") + 1] == "true"
    assert command[command.index("--enable_native_subtitle") + 1] == "true"
    assert command[command.index("--enable_asr") + 1] == "true"
    assert command[command.index("--save_raw_payload") + 1] == "true"


def test_api_rejects_topic_and_douyin_options_for_other_platforms():
    client = TestClient(app)
    topic_response = client.post("/api/crawler/start", json={
        "platform": "xhs", "crawler_type": "topic", "topics": "人工智能"
    })
    option_response = client.post("/api/crawler/start", json={
        "platform": "xhs", "crawler_type": "search", "enable_asr": True
    })
    assert topic_response.status_code == 422
    assert option_response.status_code == 422


@pytest.mark.asyncio
async def test_crawler_manager_uses_windows_graceful_break_signal(monkeypatch):
    manager = CrawlerManager()

    class FakeProcess:
        def __init__(self):
            self.signals = []
            self.running = True

        def poll(self):
            return None if self.running else 0

        def send_signal(self, value):
            self.signals.append(value)
            self.running = False

        def kill(self):
            raise AssertionError("graceful stop should not force kill")

    process = FakeProcess()
    manager.process = process
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    assert await manager.stop() is True
    assert process.signals == [signal.CTRL_BREAK_EVENT]
