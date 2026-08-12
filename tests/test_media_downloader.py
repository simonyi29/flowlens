import asyncio

import httpx
import pytest

import config
from media_platform.douyin.media_downloader import PermanentMediaDownloader, MediaDownloadError, video_candidates


def test_video_candidates_preserve_quality_and_cdn_order():
    assert video_candidates({"video": {
        "play_addr_h264": {"url_list": ["h1", "h2"]},
        "play_addr": {"url_list": ["h2", "normal"]},
        "download_addr": {"url_list": ["download"]},
    }}) == ["h1", "h2", "normal", "download"]


def test_download_resumes_partial_file(monkeypatch, tmp_path):
    body = b"0000rest"
    async def handler(request):
        assert request.headers["range"] == "bytes=4-"
        return httpx.Response(206, headers={"content-type": "video/mp4"}, content=body[4:])
    part = tmp_path / "video.mp4.part"; part.write_bytes(body[:4])
    monkeypatch.setattr(config, "DY_MAX_MEDIA_TOTAL_BYTES", 1000)
    monkeypatch.setattr(config, "DY_MEDIA_LIBRARY_MAX_BYTES", 1000)
    monkeypatch.setattr(config, "DY_MIN_FREE_DISK_BYTES", 0)
    monkeypatch.setattr(config, "DY_SKIP_EXISTING_MEDIA", True)
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = PermanentMediaDownloader(tmp_path, client)
        result = await service.download(["https://cdn/video"], tmp_path / "video.mp4", verify=False)
        assert result.resumed and result.path.read_bytes() == body
        await client.aclose()
    asyncio.run(scenario())


def test_download_rejects_html(monkeypatch, tmp_path):
    async def handler(_request): return httpx.Response(200, headers={"content-type":"text/html"}, content=b"<html>risk</html>")
    monkeypatch.setattr(config, "DY_MAX_MEDIA_TOTAL_BYTES", 1000)
    monkeypatch.setattr(config, "DY_MEDIA_LIBRARY_MAX_BYTES", 1000)
    monkeypatch.setattr(config, "DY_MIN_FREE_DISK_BYTES", 0)
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = PermanentMediaDownloader(tmp_path, client)
        with pytest.raises(MediaDownloadError): await service.download(["https://cdn/video"], tmp_path / "video.mp4")
        assert not (tmp_path / "video.mp4").exists()
        await client.aclose()
    asyncio.run(scenario())
