import asyncio
import pytest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import config
from database import douyin_state
from database.douyin_migrations import migrate_douyin_sqlite
from database.models import Base
from media_platform.douyin.normalizer import (
    normalize_aweme,
    optional_int,
    sanitize_raw_payload,
)
from media_platform.douyin.core import DouYinCrawler
from media_platform.douyin.client import DouYinClient
from media_platform.douyin.help import parse_topic_id_from_url
from media_platform.douyin.transcript import (
    DouyinTranscriptService,
    parse_caption_payload,
    segments_to_srt,
)
from tools.user_hash import anonymize_user_id, mask_nickname
from var import crawl_run_id_var, crawler_type_var, source_keyword_var


def test_optional_int_preserves_missing_values():
    assert optional_int(None) is None
    assert optional_int("") is None
    assert optional_int("1,234") == 1234
    assert optional_int(12) == 12
    assert optional_int("not-a-number") is None


def test_aweme_normalization_and_raw_payload_privacy(monkeypatch):
    monkeypatch.setattr(config, "DY_SAVE_RAW_PAYLOAD", True)
    crawl_run_id_var.set("run-1")
    crawler_type_var.set("search")
    source_keyword_var.set("人工智能")
    payload = {
        "aweme_id": "123",
        "desc": "完整文案",
        "duration": 3210,
        "author": {
            "uid": "raw-user-id",
            "sec_uid": "raw-sec-id",
            "nickname": "创作者昵称",
            "avatar_thumb": {"url_list": ["https://avatar"]},
        },
        "statistics": {
            "digg_count": "12",
            "collect_count": None,
            "comment_count": 3,
            "share_count": "4",
            "play_count": "500",
        },
        "video": {
            "duration": 3210,
            "play_addr": {
                "url_list": ["https://video"],
                "width": 1080,
                "height": 1920,
            },
        },
    }

    item = normalize_aweme(payload)
    assert item.creator_hash == anonymize_user_id("raw-user-id")
    assert item.nickname == mask_nickname("创作者昵称")
    assert item.liked_count == 12
    assert item.collected_count is None
    assert item.play_count == 500
    assert item.width == 1080
    assert item.raw_payload is not None
    raw_text = str(item.raw_payload)
    assert "raw-user-id" not in raw_text
    assert "raw-sec-id" not in raw_text
    assert "https://avatar" not in raw_text


def test_sanitize_raw_payload_recursively_masks_names():
    sanitized = sanitize_raw_payload(
        {"user": {"uid": "1", "nickname": "测试昵称", "signature": "secret"}}
    )
    assert sanitized == {"user": {"nickname": mask_nickname("测试昵称")}}


def test_sqlite_migration_is_idempotent(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE douyin_aweme (id INTEGER PRIMARY KEY, aweme_id TEXT)")
            )
            await conn.execute(
                text(
                    "CREATE TABLE douyin_aweme_comment "
                    "(id INTEGER PRIMARY KEY, comment_id TEXT)"
                )
            )
            await migrate_douyin_sqlite(conn)
            await migrate_douyin_sqlite(conn)
            await conn.run_sync(Base.metadata.create_all)
            aweme_columns = {
                row[1] for row in (await conn.execute(text("PRAGMA table_info(douyin_aweme)"))).fetchall()
            }
            comment_columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(douyin_aweme_comment)"))
                ).fetchall()
            }
        await engine.dispose()
        return aweme_columns, comment_columns

    aweme_columns, comment_columns = asyncio.run(scenario())
    assert {"play_count", "duration_ms", "source_topic", "raw_payload"} <= aweme_columns
    assert {"root_comment_id", "level", "crawl_run_id"} <= comment_columns


def test_checkpoint_round_trip(monkeypatch, tmp_path):
    from model.m_douyin import DouyinCrawlCheckpoint

    monkeypatch.setattr(douyin_state, "STATE_PATH", tmp_path / "crawl_state.sqlite")

    async def scenario():
        checkpoint = DouyinCrawlCheckpoint(
            scope="search",
            scope_id="人工智能",
            cursor="3",
            status="partial",
            collected_count=17,
            updated_at=123,
        )
        await douyin_state.save_checkpoint(checkpoint)
        return await douyin_state.load_checkpoint("search", "人工智能")

    loaded = asyncio.run(scenario())
    assert loaded is not None
    assert loaded.cursor == "3"
    assert loaded.collected_count == 17


def test_topic_id_parser_accepts_id_and_url_forms():
    assert parse_topic_id_from_url("123456") == "123456"
    assert parse_topic_id_from_url("https://www.douyin.com/challenge/98765") == "98765"
    assert parse_topic_id_from_url("https://www.douyin.com/topic/456") == "456"
    assert parse_topic_id_from_url("https://www.douyin.com/?challenge_id=789") == "789"


def test_comment_pagination_subcomments_and_zero_limit(monkeypatch):
    import media_platform.douyin.client as client_module

    client = object.__new__(DouYinClient)
    primary_calls = []
    sub_calls = []
    stored_pages = []
    checkpoints = {}

    async def primary(_aweme_id, cursor):
        primary_calls.append(cursor)
        if cursor == 0:
            return {
                "comments": [{"cid": "c1", "reply_comment_total": 1}],
                "cursor": 10,
                "has_more": 1,
                "total": 2,
            }
        return {"comments": [{"cid": "c2"}], "cursor": 20, "has_more": 0, "total": 2}

    async def sub(_aweme_id, comment_id, cursor):
        sub_calls.append((comment_id, cursor))
        return {
            "comments": [{"cid": "s1", "reply_id": comment_id}],
            "cursor": 1,
            "has_more": 0,
        }

    async def callback(_aweme_id, comments):
        stored_pages.append([item["cid"] for item in comments])

    async def load(scope, scope_id):
        return checkpoints.get((scope, scope_id))

    async def save(item):
        checkpoints[(item.scope, item.scope_id)] = item

    monkeypatch.setattr(client, "get_aweme_comments", primary)
    monkeypatch.setattr(client, "get_sub_comments", sub)
    monkeypatch.setattr(client_module, "load_checkpoint", load)
    monkeypatch.setattr(client_module, "save_checkpoint", save)

    result = asyncio.run(
        client.get_aweme_all_comments(
            "a1", crawl_interval=0, is_fetch_sub_comments=True,
            callback=callback, max_count=0,
        )
    )

    assert [item["cid"] for item in result] == ["c1", "s1", "c2"]
    assert primary_calls == [0, 10]
    assert sub_calls == [("c1", 0)]
    assert stored_pages == [["c1"], ["s1"], ["c2"]]
    assert checkpoints[("comments", "a1")].status == "complete"
    assert checkpoints[("comments", "a1")].collected_count == 3


def test_comment_checkpoint_resumes_after_successful_page(monkeypatch):
    import media_platform.douyin.client as client_module
    from model.m_douyin import DouyinCrawlCheckpoint

    client = object.__new__(DouYinClient)
    calls = []
    saved = []
    initial = DouyinCrawlCheckpoint(
        scope="comments", scope_id="a1", cursor="10", status="partial",
        collected_count=1, updated_at=1,
    )

    async def primary(_aweme_id, cursor):
        calls.append(cursor)
        return {"comments": [{"cid": "c2"}], "cursor": 20, "has_more": 0}

    async def load(scope, scope_id):
        return initial if (scope, scope_id) == ("comments", "a1") else None

    async def save(item):
        saved.append(item)

    monkeypatch.setattr(client, "get_aweme_comments", primary)
    monkeypatch.setattr(client_module, "load_checkpoint", load)
    monkeypatch.setattr(client_module, "save_checkpoint", save)

    asyncio.run(client.get_aweme_all_comments("a1", crawl_interval=0, max_count=0))
    assert calls == [10]
    assert saved[-1].cursor == "20"
    assert saved[-1].collected_count == 2
    assert saved[-1].status == "complete"


def test_comment_resume_does_not_rewrite_durable_primary_page(monkeypatch):
    import media_platform.douyin.client as client_module

    client = object.__new__(DouYinClient)
    checkpoints = {}
    primary_calls = []
    stored = []
    fail_once = True

    async def primary(_aweme_id, cursor):
        primary_calls.append(cursor)
        return {
            "comments": [{"cid": "c1", "reply_comment_total": 1}],
            "cursor": 10, "has_more": 0,
        }

    async def sub(_aweme_id, comment_id, _cursor):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("temporary reply failure")
        return {"comments": [{"cid": "s1", "reply_id": comment_id}], "cursor": 1, "has_more": 0}

    async def callback(_aweme_id, comments):
        stored.extend(item["cid"] for item in comments)

    async def load(scope, scope_id):
        return checkpoints.get((scope, scope_id))

    async def save(item):
        checkpoints[(item.scope, item.scope_id)] = item

    monkeypatch.setattr(client, "get_aweme_comments", primary)
    monkeypatch.setattr(client, "get_sub_comments", sub)
    monkeypatch.setattr(client_module, "load_checkpoint", load)
    monkeypatch.setattr(client_module, "save_checkpoint", save)

    with pytest.raises(RuntimeError):
        asyncio.run(client.get_aweme_all_comments(
            "a1", crawl_interval=0, is_fetch_sub_comments=True,
            callback=callback, max_count=0,
        ))
    asyncio.run(client.get_aweme_all_comments(
        "a1", crawl_interval=0, is_fetch_sub_comments=True,
        callback=callback, max_count=0,
    ))

    assert primary_calls == [0]
    assert stored == ["c1", "s1"]
    assert checkpoints[("comments", "a1")].status == "complete"


def test_native_caption_parsing_and_srt():
    segments = parse_caption_payload(
        {"utterances": [{"start_time": 0, "end_time": 1250, "text": "第一句"}]}
    )
    assert segments[0].text == "第一句"
    assert "00:00:00,000 --> 00:00:01,250" in segments_to_srt(segments)


def test_native_caption_service_saves_completed(monkeypatch, tmp_path):
    import media_platform.douyin.transcript as transcript_module

    saved = []

    async def downloader(_url):
        raise AssertionError("inline captions should not download media")

    async def save(item):
        saved.append(item)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(transcript_module.douyin_store, "save_transcript", save)
    service = DouyinTranscriptService(downloader)
    asyncio.run(
        service._process(
            {
                "aweme_id": "n1",
                "caption_infos": [{
                    "language": "zh",
                    "utterances": [{"start_time": 0, "end_time": 1000, "text": "原生字幕"}],
                }],
            }
        )
    )
    assert saved[-1].status == "native_completed"
    assert saved[-1].full_text == "原生字幕"
    assert (tmp_path / "data/douyin/transcripts/n1.srt").exists()


def test_asr_failure_always_removes_temporary_media(monkeypatch, tmp_path):
    import media_platform.douyin.transcript as transcript_module

    async def downloader(_url):
        return b"video"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DY_ENABLE_NATIVE_SUBTITLE", False)
    monkeypatch.setattr(config, "DY_ENABLE_ASR", True)
    monkeypatch.setattr(config, "DY_KEEP_MEDIA", False)
    service = DouyinTranscriptService(downloader)

    def fail(_path):
        raise RuntimeError("inference failed")

    monkeypatch.setattr(service, "_transcribe", fail)
    aweme = {"aweme_id": "a1", "video": {"play_addr": {"url_list": ["video-url"]}}}
    try:
        asyncio.run(service._process(aweme))
    except RuntimeError as exc:
        assert "inference failed" in str(exc)
    else:
        raise AssertionError("ASR failure should propagate to the worker")
    assert not list((tmp_path / "data/douyin/tmp").glob("*.mp4"))


def test_keyword_search_respects_small_limit_and_fetches_details(monkeypatch):
    import media_platform.douyin.core as core_module

    class FakeClient:
        def __init__(self):
            self.search_calls = []
            self.detail_calls = []

        async def search_info_by_keyword(self, **kwargs):
            self.search_calls.append(kwargs)
            return {
                "data": [
                    {"aweme_info": {"aweme_id": "1"}},
                    {"aweme_info": {"aweme_id": "1"}},
                    {"aweme_info": {"aweme_id": "2"}},
                    {"aweme_info": {"aweme_id": "3"}},
                ],
                "extra": {"logid": "search-id"},
            }

        async def get_video_by_id(self, aweme_id):
            self.detail_calls.append(aweme_id)
            return {"aweme_id": aweme_id, "desc": f"video-{aweme_id}"}

    fake_client = FakeClient()
    crawler = DouYinCrawler()
    crawler.dy_client = fake_client
    processed = []
    comment_batches = []
    checkpoints = []

    async def process_detail(item):
        processed.append(item["aweme_id"])

    async def comments(ids):
        comment_batches.append(ids)

    async def no_checkpoint(*args):
        return None

    async def capture_checkpoint(item):
        checkpoints.append(item)

    monkeypatch.setattr(crawler, "process_aweme_detail", process_detail)
    monkeypatch.setattr(crawler, "batch_get_note_comments", comments)
    monkeypatch.setattr(core_module, "load_checkpoint", no_checkpoint)
    monkeypatch.setattr(core_module, "save_checkpoint", capture_checkpoint)
    monkeypatch.setattr(config, "KEYWORDS", "人工智能")
    monkeypatch.setattr(config, "START_PAGE", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 2)
    monkeypatch.setattr(config, "MAX_CONCURRENCY_NUM", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)

    asyncio.run(crawler.search())

    assert fake_client.detail_calls == ["1", "2"]
    assert processed == ["1", "2"]
    assert comment_batches == [["1", "2"]]
    assert len(fake_client.search_calls) == 1
    assert checkpoints[-1].status == "complete"
    assert checkpoints[-1].collected_count == 2


def test_topic_mode_uses_true_topic_endpoint_without_keyword_fallback(monkeypatch):
    import media_platform.douyin.core as core_module

    class FakeClient:
        def __init__(self):
            self.topic_calls = []
            self.detail_calls = []

        async def discover_topic(self, name):
            assert name == "人工智能"
            return {"topic_id": "t1", "name": name}

        async def get_topic_detail(self, topic_id):
            assert topic_id == "t1"
            return {"ch_info": {"cha_name": "人工智能", "view_count": "10"}}

        async def get_topic_awemes(self, topic_id, cursor, count):
            self.topic_calls.append((topic_id, cursor, count))
            return {"aweme_list": [{"aweme_id": "a1"}], "cursor": 1, "has_more": 0}

        async def get_video_by_id(self, aweme_id):
            self.detail_calls.append(aweme_id)
            return {"aweme_id": aweme_id}

        async def search_info_by_keyword(self, **_kwargs):
            raise AssertionError("topic content must never use keyword search")

    crawler = DouYinCrawler()
    crawler.dy_client = FakeClient()
    processed = []
    comments = []

    async def process(item):
        processed.append(item["aweme_id"])

    async def comment_batch(items):
        comments.append(items)

    async def no_checkpoint(*_args):
        return None

    async def ignore_checkpoint(_item):
        return None

    async def ignore_topic(_item):
        return None

    monkeypatch.setattr(config, "DY_TOPICS", "人工智能")
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 1)
    monkeypatch.setattr(config, "MAX_CONCURRENCY_NUM", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    monkeypatch.setattr(crawler, "process_aweme_detail", process)
    monkeypatch.setattr(crawler, "batch_get_note_comments", comment_batch)
    monkeypatch.setattr(core_module, "load_checkpoint", no_checkpoint)
    monkeypatch.setattr(core_module, "save_checkpoint", ignore_checkpoint)
    monkeypatch.setattr(core_module.douyin_store, "save_topic", ignore_topic)

    asyncio.run(crawler.search_topics())

    assert crawler.dy_client.topic_calls == [("t1", 0, 1)]
    assert crawler.dy_client.detail_calls == ["a1"]
    assert processed == ["a1"]
    assert comments == [["a1"]]
