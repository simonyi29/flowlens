import asyncio
import builtins
import json
from contextlib import asynccontextmanager
import pytest

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import config
from database import douyin_state
from database.douyin_migrations import migrate_douyin_sqlite
from database.models import Base, DouyinAweme, DouyinAwemeMetricSnapshot
from media_platform.douyin.normalizer import (
    normalize_aweme,
    optional_int,
    sanitize_raw_payload,
)
from media_platform.douyin.core import DouYinCrawler
from media_platform.douyin.client import DouYinClient
from media_platform.douyin.help import parse_creator_info_from_url, parse_topic_id_from_url
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


def test_sanitize_raw_payload_removes_real_douyin_token_and_profile_media_shape():
    sanitized = sanitize_raw_payload({
        "authentication_token": "secret-token",
        "author_user_id": "raw-author-id",
        "author": {
            "uid": "raw-uid",
            "nickname": "创作者昵称",
            "cover_url": [{"url_list": ["https://personal-cover"]}],
            "avatar_large": {"url_list": ["https://avatar"]},
            "share_info": {
                "share_qrcode_url": {"url_list": ["https://personal-qr"]},
            },
        },
        "video": {"play_addr": {"url_list": ["https://content-video"]}},
        "music": {
            "cover_hd": {
                "url_list": ["https://p3.douyinpic.com/aweme-avatar/private.jpeg"]
            },
        },
        "ent_log_extra": '{"aweme_log_extra":{"author_id":"nested-raw-id"}}',
    })
    raw_text = json.dumps(sanitized, ensure_ascii=False)
    assert "secret-token" not in raw_text
    assert "raw-author-id" not in raw_text
    assert "raw-uid" not in raw_text
    assert "personal-cover" not in raw_text
    assert "https://avatar" not in raw_text
    assert "personal-qr" not in raw_text
    assert "nested-raw-id" not in raw_text
    assert "aweme-avatar" not in raw_text
    assert "https://content-video" in raw_text
    assert sanitized["author"]["nickname"] == mask_nickname("创作者昵称")


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
            aweme_indexes = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA index_list(douyin_aweme)"))
                ).fetchall()
            }
            comment_indexes = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA index_list(douyin_aweme_comment)"))
                ).fetchall()
            }
        await engine.dispose()
        return aweme_columns, comment_columns, aweme_indexes, comment_indexes

    aweme_columns, comment_columns, aweme_indexes, comment_indexes = asyncio.run(scenario())
    assert {"play_count", "duration_ms", "source_topic", "raw_payload"} <= aweme_columns
    assert {"root_comment_id", "level", "crawl_run_id"} <= comment_columns
    assert "ix_douyin_aweme_aweme_id" in aweme_indexes
    assert "ix_douyin_aweme_comment_comment_id" in comment_indexes


def test_creator_posts_use_private_checkpoint_and_strict_cap(monkeypatch):
    import media_platform.douyin.client as client_module

    raw_creator_id = "MS4wLjAB-sensitive"
    captured_checkpoints = []
    callbacks = []
    pages = [
        {
            "aweme_list": [
                {"aweme_id": "1"}, {"aweme_id": "1"},
                {"aweme_id": "2"}, {"aweme_id": "3"},
            ],
            "has_more": 1,
            "max_cursor": "next",
        }
    ]
    client = object.__new__(DouYinClient)

    async def no_checkpoint(scope, scope_id):
        assert scope == "creator_posts"
        assert scope_id == anonymize_user_id(raw_creator_id)
        assert raw_creator_id not in scope_id
        return None

    async def save(item):
        captured_checkpoints.append(item)

    async def get_page(_sec_user_id, _cursor):
        return pages.pop(0)

    async def callback(items):
        callbacks.append([item["aweme_id"] for item in items])

    monkeypatch.setattr(client_module, "load_checkpoint", no_checkpoint)
    monkeypatch.setattr(client_module, "save_checkpoint", save)
    monkeypatch.setattr(client, "get_user_aweme_posts", get_page)

    result = asyncio.run(
        client.get_all_user_aweme_posts(raw_creator_id, callback=callback, max_count=2)
    )

    assert [item["aweme_id"] for item in result] == ["1", "2"]
    assert callbacks == [["1", "2"]]
    assert captured_checkpoints[-1].scope_id == anonymize_user_id(raw_creator_id)
    assert captured_checkpoints[-1].status == "complete"
    assert captured_checkpoints[-1].collected_count == 2


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


def test_checkpoint_list_is_recent_first_and_bounded(monkeypatch, tmp_path):
    from model.m_douyin import DouyinCrawlCheckpoint

    monkeypatch.setattr(douyin_state, "STATE_PATH", tmp_path / "crawl_state.sqlite")
    asyncio.run(douyin_state.save_checkpoint(DouyinCrawlCheckpoint(
        scope="search", scope_id="old", status="complete", updated_at=1,
    )))
    asyncio.run(douyin_state.save_checkpoint(DouyinCrawlCheckpoint(
        scope="topic", scope_id="new", status="partial", last_error="risk", updated_at=2,
    )))

    items = asyncio.run(douyin_state.list_checkpoints(limit=1))
    assert [(item.scope, item.scope_id, item.status) for item in items] == [
        ("topic", "new", "partial")
    ]


def test_creator_profile_uses_fresh_cache_unless_forced(monkeypatch):
    import media_platform.douyin.core as core_module
    from model.m_douyin import DouyinCrawlCheckpoint

    now = 2_000_000
    raw_creator_id = "creator-sensitive"

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def get_user_info(self, sec_user_id):
            assert sec_user_id == raw_creator_id
            self.calls += 1
            return {"user": {"uid": raw_creator_id, "nickname": "测试账号"}}

    async def fresh_checkpoint(_scope, scope_id):
        assert scope_id == anonymize_user_id(raw_creator_id)
        return DouyinCrawlCheckpoint(
            scope="creator_profile", scope_id=scope_id, status="complete",
            updated_at=now - 1_000,
        )

    saved_creators = []

    async def save_creator(_user_id, payload):
        saved_creators.append(payload)

    async def ignore_checkpoint(_item):
        return None

    monkeypatch.setattr(config, "DY_ENABLE_CREATOR_PROFILE", True)
    monkeypatch.setattr(config, "DY_CREATOR_REFRESH_INTERVAL_SEC", 86400)
    monkeypatch.setattr(config, "DY_FORCE_CREATOR_REFRESH", False)
    monkeypatch.setattr(core_module, "load_checkpoint", fresh_checkpoint)
    monkeypatch.setattr(core_module, "save_checkpoint", ignore_checkpoint)
    monkeypatch.setattr(core_module.douyin_store, "save_creator", save_creator)
    monkeypatch.setattr(core_module.utils, "get_current_timestamp", lambda: now)

    cached = DouYinCrawler()
    cached.dy_client = FakeClient()
    asyncio.run(cached.fetch_creator_profile(raw_creator_id))
    assert cached.dy_client.calls == 0
    assert saved_creators == []

    monkeypatch.setattr(config, "DY_FORCE_CREATOR_REFRESH", True)
    forced = DouYinCrawler()
    forced.dy_client = FakeClient()
    asyncio.run(forced.fetch_creator_profile(raw_creator_id))
    assert forced.dy_client.calls == 1
    assert len(saved_creators) == 1


def test_sqlite_aweme_snapshot_is_idempotent_per_run_and_appends_next_run(monkeypatch):
    import store.douyin._store_impl as store_module

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def memory_session():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        monkeypatch.setattr(store_module, "get_session", memory_session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = store_module.DouyinSqliteStoreImplement()
        first = {
            "aweme_id": "a1", "liked_count": 10, "collected_count": 2,
            "comment_count": 3, "share_count": 4, "play_count": 100,
            "observed_at": 1, "crawl_run_id": "run-1", "source_mode": "detail",
        }
        await store.store_aweme_metric(first)
        await store.store_aweme_metric({**first, "liked_count": 99})
        await store.store_aweme_metric({**first, "crawl_run_id": "run-2", "observed_at": 2})
        async with factory() as session:
            count = await session.scalar(select(func.count()).select_from(DouyinAwemeMetricSnapshot))
            rows = (await session.execute(
                select(DouyinAwemeMetricSnapshot).order_by(DouyinAwemeMetricSnapshot.crawl_run_id)
            )).scalars().all()
        await engine.dispose()
        return count, rows

    count, rows = asyncio.run(scenario())
    assert count == 2
    assert [(row.crawl_run_id, row.liked_count) for row in rows] == [
        ("run-1", 10), ("run-2", 10)
    ]


def test_jsonl_and_sqlite_content_core_fields_match(monkeypatch, tmp_path):
    import store.douyin._store_impl as store_module

    content = {
        "aweme_id": "a-parity", "creator_hash": "creator-hash", "nickname": "测***号",
        "aweme_type": "video", "title": "完整标题", "desc": "完整文案",
        "liked_count": 12, "collected_count": None, "comment_count": 3,
        "share_count": 4, "play_count": 500, "duration_ms": 3210,
        "width": 1080, "height": 1920, "hashtags": [{"id": "t1", "name": "话题"}],
        "mentions": [], "music_id": "m1", "music_title": "音乐",
        "music_author": "作者", "source_keyword": "人工智能", "source_topic": "",
        "crawl_run_id": "run-parity", "collected_at": 123,
    }

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def memory_session():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        monkeypatch.setattr(store_module, "get_session", memory_session)
        monkeypatch.setattr(config, "SAVE_DATA_PATH", str(tmp_path))
        monkeypatch.setattr(config, "ENABLE_GET_WORDCLOUD", False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await store_module.DouyinSqliteStoreImplement().store_content(content)
        await store_module.DouyinJsonlStoreImplement().store_content(content)
        async with factory() as session:
            row = await session.scalar(select(DouyinAweme).where(DouyinAweme.aweme_id == "a-parity"))
        await engine.dispose()
        return row

    row = asyncio.run(scenario())
    jsonl_record = json.loads((tmp_path / "douyin" / "contents.jsonl").read_text(encoding="utf-8"))
    sqlite_record = {key: getattr(row, key) for key in content}
    sqlite_record["hashtags"] = json.loads(sqlite_record["hashtags"])
    sqlite_record["mentions"] = json.loads(sqlite_record["mentions"])
    assert {key: jsonl_record[key] for key in content} == sqlite_record


def test_topic_id_parser_accepts_id_and_url_forms():
    assert parse_topic_id_from_url("123456") == "123456"
    assert parse_topic_id_from_url("https://www.douyin.com/challenge/98765") == "98765"
    assert parse_topic_id_from_url("https://www.douyin.com/topic/456") == "456"
    assert parse_topic_id_from_url("https://www.douyin.com/?challenge_id=789") == "789"


def test_invalid_creator_url_returns_clear_value_error():
    with pytest.raises(ValueError, match="Unable to parse creator ID"):
        parse_creator_info_from_url("https://www.douyin.com/not-a-user")


def test_topic_discovery_rejects_ambiguous_exact_names(monkeypatch):
    client = object.__new__(DouYinClient)

    async def search(**_kwargs):
        return {"data": [
            {"aweme_info": {"text_extra": [{"hashtag_id": "1", "hashtag_name": "人工智能"}]}},
            {"aweme_info": {"text_extra": [{"hashtag_id": "2", "hashtag_name": "人工智能"}]}},
        ]}

    monkeypatch.setattr(client, "search_info_by_keyword", search)
    with pytest.raises(Exception, match="unique topic"):
        asyncio.run(client.discover_topic("人工智能"))


def test_topic_resolution_failure_persists_failed_checkpoint(monkeypatch):
    import media_platform.douyin.core as core_module

    class FakeClient:
        async def discover_topic(self, _name):
            raise RuntimeError("ambiguous topic")

    crawler = DouYinCrawler()
    crawler.dy_client = FakeClient()
    checkpoints = []

    async def save(item):
        checkpoints.append(item)

    monkeypatch.setattr(config, "DY_TOPICS", "人工智能")
    monkeypatch.setattr(core_module, "save_checkpoint", save)
    asyncio.run(crawler.search_topics())

    assert checkpoints[-1].scope == "topic_resolution"
    assert checkpoints[-1].scope_id == "人工智能"
    assert checkpoints[-1].status == "failed"
    assert "ambiguous topic" in checkpoints[-1].last_error


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


def test_comment_total_limit_truncates_reply_page_exactly(monkeypatch):
    import media_platform.douyin.client as client_module

    client = object.__new__(DouYinClient)
    stored = []
    checkpoints = {}

    async def primary(_aweme_id, _cursor):
        return {
            "comments": [{"cid": "c1", "reply_comment_total": 3}],
            "cursor": 1, "has_more": 0,
        }

    async def replies(_aweme_id, comment_id, _cursor):
        return {
            "comments": [
                {"cid": "s1", "reply_id": comment_id},
                {"cid": "s2", "reply_id": comment_id},
                {"cid": "s3", "reply_id": comment_id},
            ],
            "cursor": 1, "has_more": 0,
        }

    async def callback(_aweme_id, comments):
        stored.extend(item["cid"] for item in comments)

    async def load(scope, scope_id):
        return checkpoints.get((scope, scope_id))

    async def save(item):
        checkpoints[(item.scope, item.scope_id)] = item

    monkeypatch.setattr(client, "get_aweme_comments", primary)
    monkeypatch.setattr(client, "get_sub_comments", replies)
    monkeypatch.setattr(client_module, "load_checkpoint", load)
    monkeypatch.setattr(client_module, "save_checkpoint", save)

    result = asyncio.run(client.get_aweme_all_comments(
        "a1", crawl_interval=0, is_fetch_sub_comments=True,
        callback=callback, max_count=2,
    ))

    assert [item["cid"] for item in result] == ["c1", "s1"]
    assert stored == ["c1", "s1"]
    assert checkpoints[("comments", "a1")].collected_count == 2
    assert checkpoints[("comments", "a1")].status == "complete"


def test_native_caption_parsing_and_srt():
    segments = parse_caption_payload(
        {"utterances": [{"start_time": 0, "end_time": 1250, "text": "第一句"}]}
    )
    assert segments[0].text == "第一句"
    assert "00:00:00,000 --> 00:00:01,250" in segments_to_srt(segments)


def test_webvtt_caption_parsing_supports_minute_timestamps_and_cue_settings():
    segments = parse_caption_payload(
        "WEBVTT\n\n00:01.250 --> 00:03.500 align:start position:0%\n第一句\n\n"
        "2\n00:00:04.000 --> 00:00:05.000\n第二句"
    )
    assert [(item.start_ms, item.end_ms, item.text) for item in segments] == [
        (1250, 3500, "第一句"),
        (4000, 5000, "第二句"),
    ]


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


def test_asr_missing_optional_dependency_returns_actionable_error(monkeypatch, tmp_path):
    service = DouyinTranscriptService(lambda _url: None)
    media_path = tmp_path / "sample.mp4"
    media_path.write_bytes(b"video")
    real_import = builtins.__import__

    def missing_whisper(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_whisper)
    with pytest.raises(RuntimeError, match=r"uv sync --extra asr"):
        service._transcribe(media_path)


def test_asr_empty_audio_result_is_saved_as_retryable_failure(monkeypatch, tmp_path):
    import media_platform.douyin.transcript as transcript_module

    saved = []
    checkpoints = []

    async def downloader(_url):
        return b"video-without-audio"

    async def save_transcript(item):
        saved.append(item)

    async def no_checkpoint(_scope, _scope_id):
        return None

    async def save_checkpoint(item):
        checkpoints.append(item)

    async def scenario():
        service = DouyinTranscriptService(downloader)
        monkeypatch.setattr(service, "_transcribe", lambda _path: [])
        await service.enqueue({
            "aweme_id": "silent",
            "video": {"play_addr": {"url_list": ["video-url"]}},
        })
        await service.drain_and_close()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DY_ENABLE_NATIVE_SUBTITLE", False)
    monkeypatch.setattr(config, "DY_ENABLE_ASR", True)
    monkeypatch.setattr(config, "DY_KEEP_MEDIA", False)
    monkeypatch.setattr(transcript_module.douyin_store, "save_transcript", save_transcript)
    monkeypatch.setattr(transcript_module, "load_checkpoint", no_checkpoint)
    monkeypatch.setattr(transcript_module, "save_checkpoint", save_checkpoint)
    asyncio.run(scenario())

    assert saved[-1].status == "failed"
    assert "no speech segments" in saved[-1].error_message
    assert checkpoints[-1].status == "partial"
    assert not list((tmp_path / "data/douyin/tmp").glob("*.mp4"))


def test_transcript_cancel_marks_active_and_queued_jobs_retryable(monkeypatch):
    import media_platform.douyin.transcript as transcript_module

    saved = []
    checkpoints = []

    async def scenario():
        processing_started = asyncio.Event()

        async def downloader(_url):
            return None

        async def save_transcript(item):
            saved.append(item)

        async def no_checkpoint(_scope, _scope_id):
            return None

        async def save_checkpoint(item):
            checkpoints.append(item)

        service = DouyinTranscriptService(downloader)

        async def blocked_process(_item):
            processing_started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(transcript_module.douyin_store, "save_transcript", save_transcript)
        monkeypatch.setattr(transcript_module, "load_checkpoint", no_checkpoint)
        monkeypatch.setattr(transcript_module, "save_checkpoint", save_checkpoint)
        monkeypatch.setattr(service, "_process", blocked_process)

        await service.enqueue({"aweme_id": "active"})
        await processing_started.wait()
        await service.enqueue({"aweme_id": "queued"})
        await service.cancel_and_close()

    asyncio.run(scenario())

    failed_ids = {item.aweme_id for item in saved if item.status == "failed"}
    assert failed_ids == {"active", "queued"}
    assert {item.scope_id for item in checkpoints} == {"active", "queued"}
    assert all(item.status == "partial" for item in checkpoints)


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


def test_keyword_search_missing_data_is_failed_not_empty_success(monkeypatch):
    import media_platform.douyin.core as core_module

    class FakeClient:
        async def search_info_by_keyword(self, **_kwargs):
            return {"status_code": 8, "status_msg": "login expired"}

    crawler = DouYinCrawler()
    crawler.dy_client = FakeClient()
    checkpoints = []

    async def no_checkpoint(*_args):
        return None

    async def save(item):
        checkpoints.append(item)

    monkeypatch.setattr(config, "KEYWORDS", "人工智能")
    monkeypatch.setattr(config, "START_PAGE", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 1)
    monkeypatch.setattr(core_module, "load_checkpoint", no_checkpoint)
    monkeypatch.setattr(core_module, "save_checkpoint", save)

    asyncio.run(crawler.search())

    assert checkpoints[-1].status == "failed"
    assert "missing data" in checkpoints[-1].last_error


def test_keyword_search_retries_with_exponential_backoff(monkeypatch):
    import media_platform.douyin.core as core_module
    from media_platform.douyin.exception import DataFetchError

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def search_info_by_keyword(self, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise DataFetchError("temporary")
            return {"data": [{"aweme_info": {"aweme_id": "a1"}}]}

        async def get_video_by_id(self, aweme_id):
            return {"aweme_id": aweme_id}

    crawler = DouYinCrawler()
    crawler.dy_client = FakeClient()
    sleeps = []

    async def no_checkpoint(*_args):
        return None

    async def ignore_checkpoint(_item):
        return None

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def ignore_detail(_item):
        return None

    async def ignore_comments(_items):
        return None

    monkeypatch.setattr(config, "KEYWORDS", "人工智能")
    monkeypatch.setattr(config, "START_PAGE", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 1)
    monkeypatch.setattr(config, "MAX_CONCURRENCY_NUM", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    monkeypatch.setattr(core_module, "load_checkpoint", no_checkpoint)
    monkeypatch.setattr(core_module, "save_checkpoint", ignore_checkpoint)
    monkeypatch.setattr(core_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(crawler, "process_aweme_detail", ignore_detail)
    monkeypatch.setattr(crawler, "batch_get_note_comments", ignore_comments)

    asyncio.run(crawler.search())

    assert crawler.dy_client.calls == 3
    assert sleeps[:2] == [1, 2]


def test_crawler_cancellation_uses_transcript_cancel_path(monkeypatch):
    import media_platform.douyin.core as core_module

    class FakePlaywrightContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    class FakePage:
        async def goto(self, _url):
            return None

    class FakeBrowserContext:
        async def new_page(self):
            return FakePage()

    class FakeClient:
        async def pong(self, browser_context):
            return True

        async def get_aweme_media(self, _url):
            return None

    class FakeTranscriptService:
        def __init__(self, _downloader):
            self.cancelled = 0
            self.drained = 0

        async def start(self):
            return None

        async def cancel_and_close(self):
            self.cancelled += 1

        async def drain_and_close(self):
            self.drained += 1

    crawler = DouYinCrawler()

    async def launch(*_args, **_kwargs):
        return FakeBrowserContext()

    async def client(_proxy):
        return FakeClient()

    async def cancelled_search():
        raise asyncio.CancelledError

    monkeypatch.setattr(config, "ENABLE_IP_PROXY", False)
    monkeypatch.setattr(config, "ENABLE_CDP_MODE", True)
    monkeypatch.setattr(config, "CDP_HEADLESS", False)
    monkeypatch.setattr(config, "CRAWLER_TYPE", "search")
    monkeypatch.setattr(config, "DY_ENABLE_NATIVE_SUBTITLE", True)
    monkeypatch.setattr(core_module, "async_playwright", lambda: FakePlaywrightContext())
    monkeypatch.setattr(core_module, "DouyinTranscriptService", FakeTranscriptService)
    monkeypatch.setattr(crawler, "launch_browser_with_cdp", launch)
    monkeypatch.setattr(crawler, "create_douyin_client", client)
    monkeypatch.setattr(crawler, "search", cancelled_search)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(crawler.start())

    assert crawler.transcript_service.cancelled == 1
    assert crawler.transcript_service.drained == 0


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
