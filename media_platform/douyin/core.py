# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/douyin/core.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import json
import os
import random
import uuid
from asyncio import Task
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import douyin as douyin_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from tools.user_hash import anonymize_user_id
from database.douyin_state import load_checkpoint, save_checkpoint
from model.m_douyin import DouyinCrawlCheckpoint, DouyinTopic
from var import (
    crawl_run_id_var,
    crawler_type_var,
    request_keyword_var,
    source_keyword_var,
    source_topic_var,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .client import DouYinClient
from .exception import ApiSchemaChangedError, DataFetchError, RiskControlledError
from .field import PublishTimeType
from .help import (
    parse_creator_info_from_url,
    parse_topic_id_from_url,
    parse_video_info_from_url,
)
from .normalizer import optional_int, sanitize_raw_payload
from .transcript import DouyinTranscriptService
from .login import DouYinLogin
from .media_downloader import PermanentMediaDownloader, MediaDownloadError, video_candidates
from api.services.task_store import task_store


class DouYinCrawler(AbstractCrawler):
    context_page: Page
    dy_client: DouYinClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://www.douyin.com"
        self.cookie_urls = [
            "https://douyin.com",
            self.index_url,
            "https://creator.douyin.com",
            "https://douhot.douyin.com",
            "https://live.douyin.com",
        ]
        self.cdp_manager = None
        self.ip_proxy_pool = None  # Proxy IP pool for automatic proxy refresh
        self.seen_aweme_ids: set[str] = set()
        self.seen_creator_ids: set[str] = set()
        self.transcript_service: Optional[DouyinTranscriptService] = None
        self.media_downloader: Optional[PermanentMediaDownloader] = None
        self.media_queue: Optional[asyncio.Queue] = None
        self.media_worker: Optional[asyncio.Task] = None
        self.media_worker_error: Optional[Exception] = None
        self.media_downloaded_awemes = 0
        self.new_aweme_ids: set[str] = set()

    async def start(self) -> None:
        crawl_run_id_var.set(os.getenv("FLOWLENS_RUN_ID") or uuid.uuid4().hex)
        await task_store.initialize()
        await task_store.ensure_run(
            crawl_run_id_var.get(),
            {"platform": "dy", "crawler_type": config.CRAWLER_TYPE, "source": "cli"},
        )
        source_keyword_var.set("")
        source_topic_var.set("")
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            self.ip_proxy_pool = await create_ip_pool(config.IP_PROXY_POOL_COUNT, enable_validate_ip=True)
            ip_proxy_info: IpInfoModel = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # Select startup mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[DouYinCrawler] 使用CDP模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    None,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[DouYinCrawler] 使用标准模式启动浏览器")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    user_agent=None,
                    headless=config.HEADLESS,
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(path="libs/stealth.min.js")

            self.context_page = await self.browser_context.new_page()
            # Douyin keeps advertising/analytics requests alive; waiting for the
            # full load event makes a healthy CDP session look unavailable.
            try:
                await self.context_page.goto(
                    self.index_url, wait_until="domcontentloaded", timeout=45_000
                )
            except PlaywrightTimeoutError:
                if "douyin.com" not in str(self.context_page.url):
                    raise
                utils.logger.warning("[DouYinCrawler] homepage navigation timed out after reaching Douyin")
            page_url = str(self.context_page.url).lower()
            page_text = (await self.context_page.locator("body").inner_text(timeout=5_000)).lower()
            if any(token in page_url or token in page_text for token in ("captcha", "验证码", "安全验证")):
                raise RuntimeError("captcha_required: complete verification in the connected Chrome")
            if any(token in page_url for token in ("passport", "/login")):
                raise RuntimeError("login_required: sign in using the connected Chrome")

            self.dy_client = await self.create_douyin_client(httpx_proxy_format)
            if getattr(config, "DY_DOWNLOAD_MEDIA", False):
                media_headers = {
                    key: value for key, value in self.dy_client.headers.items()
                    if key.lower() not in {"host", "content-type", "origin"}
                }
                self.media_downloader = PermanentMediaDownloader(
                    headers=media_headers, proxy=httpx_proxy_format,
                )
                self.media_queue = asyncio.Queue(maxsize=3)
                self.media_worker = asyncio.create_task(self._media_worker_loop())
            if getattr(config, "DY_ENABLE_NATIVE_SUBTITLE", True) or getattr(config, "DY_ENABLE_ASR", True):
                self.transcript_service = DouyinTranscriptService(self.dy_client.get_aweme_media)
                await self.transcript_service.start()
            if not await self.dy_client.pong(browser_context=self.browser_context):
                login_obj = DouYinLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # you phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.dy_client.update_cookies(
                    browser_context=self.browser_context,
                    urls=self.cookie_urls,
                )
            crawler_type_var.set(config.CRAWLER_TYPE)
            try:
                if config.CRAWLER_TYPE == "search":
                    await self.search()
                elif config.CRAWLER_TYPE == "detail":
                    await self.get_specified_awemes()
                elif config.CRAWLER_TYPE == "creator":
                    await self.get_creators_and_videos()
                elif config.CRAWLER_TYPE == "topic":
                    await self.search_topics()
                await self._drain_media_queue()
            except asyncio.CancelledError:
                await self._cancel_media_queue()
                if self.transcript_service:
                    await self.transcript_service.cancel_and_close()
                await task_store.update_run(
                    crawl_run_id_var.get(), "partial", stage="finalize",
                    error_type="cancelled", error_message="crawler cancelled",
                )
                raise
            except Exception as exc:
                await self._cancel_media_queue()
                await task_store.update_run(
                    crawl_run_id_var.get(), "partial", stage="finalize",
                    error_type=getattr(exc, "error_type", "unknown"),
                    error_message=str(exc),
                )
                raise
            else:
                if self.transcript_service:
                    await self.transcript_service.drain_and_close()
                await task_store.update_stage(
                    crawl_run_id_var.get(), "finalize", "completed",
                    total=1, completed=1, failed=0,
                )
                await task_store.update_run(
                    crawl_run_id_var.get(), "completed", stage="finalize"
                )
            finally:
                if self.media_downloader:
                    await self.media_downloader.close()

            utils.logger.info("[DouYinCrawler.start] Douyin Crawler finished ...")

    async def search(self) -> None:
        utils.logger.info("[DouYinCrawler.search] Begin search douyin keywords")
        page_size = 15
        max_count = config.CRAWLER_MAX_NOTES_COUNT
        start_page = max(1, config.START_PAGE)
        for keyword in config.KEYWORDS.split(","):
            keyword = keyword.strip()
            if not keyword:
                continue
            source_keyword_var.set(keyword)
            request_keyword_var.set(keyword)
            source_topic_var.set("")
            utils.logger.info(f"[DouYinCrawler.search] Current keyword: {keyword}")
            aweme_list: List[str] = []
            checkpoint = await load_checkpoint("search", keyword)
            page = start_page
            collected = 0
            if checkpoint and checkpoint.status in {"partial", "failed", "running"}:
                collected = checkpoint.collected_count
                try:
                    page = max(start_page, int(checkpoint.cursor))
                except ValueError:
                    page = start_page
            dy_search_id = ""
            while collected < max_count:
                posts_res = None
                for attempt in range(3):
                    try:
                        utils.logger.info(
                            f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page}"
                        )
                        posts_res = await self.dy_client.search_info_by_keyword(
                            keyword=keyword,
                            offset=(page - 1) * page_size,
                            publish_time=PublishTimeType(config.PUBLISH_TIME_TYPE),
                            search_id=dy_search_id,
                            count=page_size,
                        )
                        break
                    except DataFetchError as exc:
                        if attempt == 2:
                            await save_checkpoint(
                                DouyinCrawlCheckpoint(
                                    scope="search",
                                    scope_id=keyword,
                                    cursor=str(page),
                                    status="partial" if collected else "failed",
                                    collected_count=collected,
                                    last_error=str(exc),
                                    updated_at=utils.get_current_timestamp(),
                                )
                            )
                            utils.logger.error(
                                f"[DouYinCrawler.search] keyword {keyword} failed after retries"
                            )
                            break
                        await asyncio.sleep(2 ** attempt)
                if posts_res is None:
                    break
                if "data" not in posts_res:
                    error = (
                        "risk_controlled: search response was rejected"
                        if posts_res.get("status_code") not in (None, 0)
                        else "api_schema_changed: search response is missing data"
                    )
                    utils.logger.error(
                        f"[DouYinCrawler.search] keyword {keyword}: {error}"
                    )
                    await save_checkpoint(
                        DouyinCrawlCheckpoint(
                            scope="search", scope_id=keyword, cursor=str(page),
                            status="partial" if collected else "failed",
                            collected_count=collected, last_error=error,
                            updated_at=utils.get_current_timestamp(),
                        )
                    )
                    raise RiskControlledError(error) if error.startswith("risk_controlled") else ApiSchemaChangedError(error)
                if not posts_res.get("data"):
                    utils.logger.info(
                        f"[DouYinCrawler.search] keyword {keyword}, page {page} is empty"
                    )
                    await save_checkpoint(
                        DouyinCrawlCheckpoint(
                            scope="search",
                            scope_id=keyword,
                            cursor=str(page),
                            status="complete",
                            collected_count=collected,
                            updated_at=utils.get_current_timestamp(),
                        )
                    )
                    break
                dy_search_id = posts_res.get("extra", {}).get("logid", "")
                page_aweme_list: List[str] = []
                for post_item in posts_res.get("data"):
                    try:
                        aweme_info: Dict = (post_item.get("aweme_info") or post_item.get("aweme_mix_info", {}).get("mix_items")[0])
                    except (IndexError, TypeError):
                        continue
                    aweme_id = str(aweme_info.get("aweme_id") or "")
                    if not aweme_id or aweme_id in self.seen_aweme_ids:
                        continue
                    self.seen_aweme_ids.add(aweme_id)
                    page_aweme_list.append(aweme_id)
                    aweme_list.append(aweme_id)
                    collected += 1
                    if collected >= max_count:
                        break

                semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
                detail_tasks = [
                    self.get_aweme_detail(aweme_id, semaphore)
                    for aweme_id in page_aweme_list
                ]
                details = await asyncio.gather(*detail_tasks) if detail_tasks else []
                successful_ids: List[str] = []
                for detail in details:
                    if detail:
                        is_new = await self.process_aweme_detail(detail)
                        if is_new is not False or getattr(config, "DY_REFRESH_EXISTING_COMMENTS", False):
                            successful_ids.append(str(detail.get("aweme_id") or ""))

                await self.batch_get_note_comments([item for item in successful_ids if item])
                page += 1
                await save_checkpoint(
                    DouyinCrawlCheckpoint(
                        scope="search",
                        scope_id=keyword,
                        cursor=str(page),
                        status="complete" if collected >= max_count else "running",
                        collected_count=collected,
                        updated_at=utils.get_current_timestamp(),
                    )
                )

                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[DouYinCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page-1}")
            utils.logger.info(f"[DouYinCrawler.search] keyword:{keyword}, aweme_list:{aweme_list}")

    async def process_aweme_detail(self, aweme_item: Dict) -> bool:
        """Persist one full detail response and its related public creator data."""
        aweme_id = str(aweme_item.get("aweme_id") or "")
        run_id = crawl_run_id_var.get()
        await task_store.upsert_task_item(run_id, aweme_id, "detail", "running", 0.1)
        existed = await task_store.entity_exists("dy", "aweme", aweme_id)
        should_refresh = not (
            existed and getattr(config, "DY_INCREMENTAL", False)
            and not getattr(config, "DY_REFRESH_EXISTING_METRICS", True)
        )
        if should_refresh:
            await douyin_store.update_douyin_aweme(aweme_item=aweme_item)
        await task_store.touch_entity("dy", "aweme", aweme_id, run_id)
        await task_store.upsert_task_item(run_id, aweme_id, "detail", "completed", 1)
        if existed and getattr(config, "DY_INCREMENTAL", False):
            return False
        self.new_aweme_ids.add(aweme_id)
        if getattr(config, "DY_DOWNLOAD_MEDIA", False):
            if self.media_queue:
                await self.media_queue.put(aweme_item)
        else:
            await self.get_aweme_media(aweme_item=aweme_item)
        if self.transcript_service:
            await self.transcript_service.enqueue(aweme_item)
        author = aweme_item.get("author") or {}
        sec_user_id = str(author.get("sec_uid") or "")
        await self.fetch_creator_profile(sec_user_id)
        return True

    async def _media_worker_loop(self) -> None:
        assert self.media_queue is not None
        while True:
            item = await self.media_queue.get()
            try:
                if item is None:
                    return
                await self.download_permanent_media(item)
            except Exception as exc:
                self.media_worker_error = exc
            finally:
                self.media_queue.task_done()

    async def _drain_media_queue(self) -> None:
        if not self.media_queue or not self.media_worker:
            return
        await self.media_queue.put(None)
        await self.media_queue.join()
        await self.media_worker
        if self.media_worker_error:
            raise self.media_worker_error

    async def _cancel_media_queue(self) -> None:
        if self.media_worker and not self.media_worker.done():
            self.media_worker.cancel()
            await asyncio.gather(self.media_worker, return_exceptions=True)

    async def download_permanent_media(self, aweme_item: Dict) -> None:
        if not self.media_downloader:
            return
        aweme_id = str(aweme_item.get("aweme_id") or "")
        author = aweme_item.get("author") or {}
        creator_hash = anonymize_user_id(str(author.get("uid") or author.get("sec_uid") or "")) or "unknown"
        base = self.media_downloader.root / creator_hash / aweme_id
        assets: list[tuple[str, list[str], str]] = []
        if getattr(config, "DY_DOWNLOAD_VIDEO", True) and not (aweme_item.get("images") or []):
            assets.append(("video", video_candidates(aweme_item), "video.mp4"))
        if getattr(config, "DY_DOWNLOAD_IMAGES", True):
            for index, image in enumerate(aweme_item.get("images") or []):
                assets.append(("image", list((image or {}).get("url_list") or []), f"images/{index:03d}.jpeg"))
        if getattr(config, "DY_DOWNLOAD_COVER", True):
            cover = ((aweme_item.get("video") or {}).get("origin_cover") or {}).get("url_list") or []
            if cover: assets.append(("cover", list(cover), "cover.jpg"))
        if getattr(config, "DY_DOWNLOAD_MUSIC", False):
            music = (((aweme_item.get("music") or {}).get("play_url") or {}).get("url_list") or [])
            if music: assets.append(("music", list(music), "music.mp3"))
        if not assets:
            return
        if self.media_downloaded_awemes >= int(config.DY_MAX_MEDIA_DOWNLOADS):
            await task_store.upsert_task_item(
                crawl_run_id_var.get(), aweme_id, "media_download", "quota_reached", 0
            )
            return
        self.media_downloaded_awemes += 1
        metadata = {
            "aweme_id": aweme_id, "creator_hash": creator_hash,
            "title": str(aweme_item.get("desc") or ""),
            "duration_ms": aweme_item.get("duration") or (aweme_item.get("video") or {}).get("duration"),
            "crawl_run_id": crawl_run_id_var.get(), "assets": [],
        }
        for kind, urls, relative in assets:
            if not urls: continue
            try:
                await task_store.upsert_task_item(crawl_run_id_var.get(), aweme_id, "media_download", "running", 0.1)
                result = await self.media_downloader.download(urls, base / relative, verify=bool(config.DY_VERIFY_MEDIA))
                await task_store.upsert_media({
                    "run_id": crawl_run_id_var.get(), "aweme_id": aweme_id, "creator_hash": creator_hash,
                    "kind": kind, "status": "completed", "path": str(result.path.resolve()),
                    "source_url": result.source_url, "mime_type": result.mime_type,
                    "size_bytes": result.size_bytes, "sha256": result.sha256, "quality": config.DY_MEDIA_QUALITY,
                })
                metadata["assets"].append({"kind":kind,"path":str(result.path.relative_to(base)),"size_bytes":result.size_bytes,"sha256":result.sha256})
                await task_store.upsert_task_item(crawl_run_id_var.get(), aweme_id, "media_download", "completed", 1)
            except MediaDownloadError as exc:
                # Signed CDN addresses can expire between detail discovery and the
                # background download. Refresh once, then rebuild this asset's URLs.
                if exc.error_type == "media_url_expired":
                    try:
                        refreshed = await self.dy_client.get_video_by_id(aweme_id)
                        if kind == "video":
                            refreshed_urls = video_candidates(refreshed)
                        elif kind == "cover":
                            refreshed_urls = list((((refreshed.get("video") or {}).get("origin_cover") or {}).get("url_list") or []))
                        elif kind == "music":
                            refreshed_urls = list(((((refreshed.get("music") or {}).get("play_url") or {}).get("url_list")) or []))
                        else:
                            image_index = int(Path(relative).stem)
                            refreshed_images = refreshed.get("images") or []
                            refreshed_urls = list((refreshed_images[image_index] or {}).get("url_list") or []) if image_index < len(refreshed_images) else []
                        if refreshed_urls:
                            result = await self.media_downloader.download(refreshed_urls, base / relative, verify=bool(config.DY_VERIFY_MEDIA))
                            await task_store.upsert_media({
                                "run_id": crawl_run_id_var.get(), "aweme_id": aweme_id, "creator_hash": creator_hash,
                                "kind": kind, "status": "completed", "path": str(result.path.resolve()),
                                "source_url": result.source_url, "mime_type": result.mime_type,
                                "size_bytes": result.size_bytes, "sha256": result.sha256, "quality": config.DY_MEDIA_QUALITY,
                                "retry_count": 1,
                            })
                            metadata["assets"].append({"kind":kind,"path":str(result.path.relative_to(base)),"size_bytes":result.size_bytes,"sha256":result.sha256})
                            await task_store.upsert_task_item(crawl_run_id_var.get(), aweme_id, "media_download", "completed", 1)
                            continue
                    except Exception as refresh_exc:
                        utils.logger.warning(f"[DouYinCrawler.download] media_url_expired refresh failed: {refresh_exc}")
                await task_store.upsert_media({"run_id": crawl_run_id_var.get(), "aweme_id": aweme_id,
                    "creator_hash": creator_hash, "kind": kind, "status": "waiting_for_space" if exc.error_type.startswith("disk_") else "failed",
                    "part_path": str((base / relative).with_suffix(Path(relative).suffix + '.part').resolve()),
                    "error_type": exc.error_type, "error_message": str(exc)})
                await task_store.upsert_task_item(crawl_run_id_var.get(), aweme_id, "media_download",
                    "waiting_for_space" if exc.error_type.startswith("disk_") else "failed", 0,
                    exc.error_type, str(exc))
                if exc.error_type.startswith("disk_"):
                    raise
        base.mkdir(parents=True, exist_ok=True)
        (base / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    async def fetch_creator_profile(self, sec_user_id: str) -> None:
        if not getattr(config, "DY_ENABLE_CREATOR_PROFILE", True):
            return
        if not sec_user_id or sec_user_id in self.seen_creator_ids:
            return
        self.seen_creator_ids.add(sec_user_id)
        checkpoint_id = anonymize_user_id(sec_user_id)
        await task_store.upsert_task_item(
            crawl_run_id_var.get(), checkpoint_id, "creator", "running", 0.1
        )
        checkpoint = await load_checkpoint("creator_profile", checkpoint_id)
        refresh_ms = int(getattr(config, "DY_CREATOR_REFRESH_INTERVAL_SEC", 86400) * 1000)
        if (
            checkpoint and checkpoint.status == "complete"
            and not getattr(config, "DY_FORCE_CREATOR_REFRESH", False)
            and utils.get_current_timestamp() - checkpoint.updated_at < refresh_ms
        ):
            await task_store.upsert_task_item(
                crawl_run_id_var.get(), checkpoint_id, "creator", "completed", 1
            )
            return
        try:
            creator_info = await self.dy_client.get_user_info(sec_user_id)
            if creator_info:
                await douyin_store.save_creator(sec_user_id, creator_info)
                await save_checkpoint(
                    DouyinCrawlCheckpoint(
                        scope="creator_profile", scope_id=checkpoint_id, status="complete",
                        collected_count=1, updated_at=utils.get_current_timestamp(),
                    )
                )
                await task_store.upsert_task_item(
                    crawl_run_id_var.get(), checkpoint_id, "creator", "completed", 1
                )
        except DataFetchError as exc:
            await task_store.upsert_task_item(
                crawl_run_id_var.get(), checkpoint_id, "creator", "failed", 0,
                "unknown", str(exc),
            )
            utils.logger.warning(
                f"[DouYinCrawler.process_aweme_detail] creator profile unavailable: {exc}"
            )

    async def resolve_topic(self, value: str) -> tuple[str, str]:
        try:
            return parse_topic_id_from_url(value), ""
        except ValueError:
            discovered = await self.dy_client.discover_topic(value)
            return str(discovered["topic_id"]), str(discovered.get("name") or value)

    async def search_topics(self) -> None:
        values = [item.strip() for item in config.DY_TOPICS.split(",") if item.strip()]
        for value in values:
            source_keyword_var.set("")
            try:
                topic_id, discovered_name = await self.resolve_topic(value)
                detail_response = await self.dy_client.get_topic_detail(topic_id)
            except Exception as exc:
                await save_checkpoint(
                    DouyinCrawlCheckpoint(
                        scope="topic_resolution", scope_id=value, status="failed",
                        last_error=f"{type(exc).__name__}: {exc}",
                        updated_at=utils.get_current_timestamp(),
                    )
                )
                utils.logger.error(
                    f"[DouYinCrawler.search_topics] topic '{value}' could not be resolved: {exc}"
                )
                continue
            detail = (
                detail_response.get("ch_info")
                or detail_response.get("challenge_info")
                or detail_response.get("cha_info")
                or {}
            )
            topic_name = str(detail.get("cha_name") or detail.get("name") or discovered_name)
            source_topic_var.set(topic_name or topic_id)
            raw_payload = (
                sanitize_raw_payload(detail_response)
                if getattr(config, "DY_SAVE_RAW_PAYLOAD", False)
                else None
            )
            await douyin_store.save_topic(
                DouyinTopic(
                    topic_id=topic_id,
                    name=topic_name,
                    topic_url=f"https://www.douyin.com/challenge/{topic_id}",
                    view_count=optional_int(detail.get("view_count")),
                    aweme_count=optional_int(detail.get("user_count") or detail.get("aweme_count")),
                    crawl_run_id=crawl_run_id_var.get(),
                    collected_at=utils.get_current_timestamp(),
                    raw_payload=raw_payload,
                )
            )

            checkpoint = await load_checkpoint("topic", topic_id)
            cursor = int(checkpoint.cursor) if checkpoint and checkpoint.status != "complete" else 0
            collected = checkpoint.collected_count if checkpoint and checkpoint.status != "complete" else 0
            max_count = config.CRAWLER_MAX_NOTES_COUNT
            consecutive_existing = 0
            while collected < max_count:
                try:
                    response = await self.dy_client.get_topic_awemes(
                        topic_id, cursor=cursor, count=min(20, max_count - collected)
                    )
                except Exception as exc:
                    await save_checkpoint(
                        DouyinCrawlCheckpoint(
                            scope="topic", scope_id=topic_id, cursor=str(cursor),
                            status="partial" if collected else "failed",
                            collected_count=collected,
                            last_error=f"{type(exc).__name__}: {exc}",
                            updated_at=utils.get_current_timestamp(),
                        )
                    )
                    utils.logger.error(
                        f"[DouYinCrawler.search_topics] true topic endpoint failed for {topic_id}: {exc}"
                    )
                    raise
                awemes = response.get("aweme_list") or []
                page_ids = []
                for item in awemes:
                    aweme_id = str(item.get("aweme_id") or "")
                    if not aweme_id or aweme_id in self.seen_aweme_ids:
                        continue
                    self.seen_aweme_ids.add(aweme_id)
                    page_ids.append(aweme_id)
                    if getattr(config, "DY_INCREMENTAL", False):
                        if await task_store.entity_exists("dy", "aweme", aweme_id):
                            consecutive_existing += 1
                        else:
                            consecutive_existing = 0
                    collected += 1
                    if collected >= max_count or consecutive_existing >= int(getattr(config, "DY_STOP_AFTER_EXISTING", 5)):
                        break
                semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
                details = await asyncio.gather(
                    *[self.get_aweme_detail(item, semaphore) for item in page_ids]
                ) if page_ids else []
                successful = []
                for item in details:
                    if item:
                        is_new = await self.process_aweme_detail(item)
                        if is_new is not False or getattr(config, "DY_REFRESH_EXISTING_COMMENTS", False):
                            successful.append(str(item.get("aweme_id") or ""))
                await self.batch_get_note_comments([item for item in successful if item])
                has_more = bool(response.get("has_more"))
                next_cursor = int(response.get("cursor") or response.get("max_cursor") or cursor)
                status = "running" if has_more and collected < max_count else "complete"
                await save_checkpoint(
                    DouyinCrawlCheckpoint(
                        scope="topic", scope_id=topic_id, cursor=str(next_cursor),
                        status=status, collected_count=collected,
                        updated_at=utils.get_current_timestamp(),
                    )
                )
                if status == "complete" or not awemes or next_cursor == cursor or consecutive_existing >= int(getattr(config, "DY_STOP_AFTER_EXISTING", 5)):
                    break
                cursor = next_cursor
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

    async def get_specified_awemes(self):
        """Get the information and comments of the specified post from URLs or IDs"""
        utils.logger.info("[DouYinCrawler.get_specified_awemes] Parsing video URLs...")
        aweme_id_list = []
        for video_url in config.DY_SPECIFIED_ID_LIST:
            try:
                video_info = parse_video_info_from_url(video_url)

                # Handling short links
                if video_info.url_type == "short":
                    utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Resolving short link: {video_url}")
                    resolved_url = await self.dy_client.resolve_short_url(video_url)
                    if resolved_url:
                        # Extract video ID from parsed URL
                        video_info = parse_video_info_from_url(resolved_url)
                        utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Short link resolved to aweme ID: {video_info.aweme_id}")
                    else:
                        utils.logger.error(f"[DouYinCrawler.get_specified_awemes] Failed to resolve short link: {video_url}")
                        continue

                if video_info.aweme_id not in aweme_id_list:
                    aweme_id_list.append(video_info.aweme_id)
                utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Parsed aweme ID: {video_info.aweme_id} from {video_url}")
            except ValueError as e:
                utils.logger.error(f"[DouYinCrawler.get_specified_awemes] Failed to parse video URL: {e}")
                continue

        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(aweme_id=aweme_id, semaphore=semaphore) for aweme_id in aweme_id_list]
        aweme_details = await asyncio.gather(*task_list)
        comment_ids = []
        for aweme_detail in aweme_details:
            if aweme_detail is not None:
                is_new = await self.process_aweme_detail(aweme_detail)
                if is_new is not False or getattr(config, "DY_REFRESH_EXISTING_COMMENTS", False):
                    comment_ids.append(str(aweme_detail.get("aweme_id") or ""))
        await self.batch_get_note_comments(comment_ids)

    async def get_aweme_detail(self, aweme_id: str, semaphore: asyncio.Semaphore) -> Any:
        """Get note detail"""
        async with semaphore:
            try:
                result = await self.dy_client.get_video_by_id(aweme_id)
                # Sleep after fetching aweme detail
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[DouYinCrawler.get_aweme_detail] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching aweme {aweme_id}")
                return result
            except ApiSchemaChangedError:
                raise
            except DataFetchError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] Get aweme detail error: {ex}")
                message = str(ex).lower()
                if any(token in message for token in ("argussecurityplugin", "validate error", "risk", "captcha")):
                    raise RiskControlledError(
                        "risk_controlled: Douyin rejected the detail request; pause and continue later"
                    ) from ex
                return None
            except KeyError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] have not fund note detail aweme_id:{aweme_id}, err: {ex}")
                return None

    async def batch_get_note_comments(self, aweme_list: List[str]) -> None:
        """
        Batch get note comments
        """
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(f"[DouYinCrawler.batch_get_note_comments] Crawling comment mode is not enabled")
            return

        task_list: List[Task] = []
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        for aweme_id in aweme_list:
            task = asyncio.create_task(self.get_comments(aweme_id, semaphore), name=aweme_id)
            task_list.append(task)
        if len(task_list) > 0:
            await asyncio.gather(*task_list)

    async def get_comments(self, aweme_id: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                await task_store.upsert_task_item(
                    crawl_run_id_var.get(), aweme_id, "comments", "running", 0.1
                )
                # Pass the list of keywords to the get_aweme_all_comments method
                # Use fixed crawling interval
                crawl_interval = config.CRAWLER_MAX_SLEEP_SEC
                await self.dy_client.get_aweme_all_comments(
                    aweme_id=aweme_id,
                    crawl_interval=crawl_interval,
                    is_fetch_sub_comments=config.ENABLE_GET_SUB_COMMENTS,
                    callback=douyin_store.batch_update_dy_aweme_comments,
                    max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
                )
                # Sleep after fetching comments
                await asyncio.sleep(crawl_interval)
                utils.logger.info(f"[DouYinCrawler.get_comments] Sleeping for {crawl_interval} seconds after fetching comments for aweme {aweme_id}")
                utils.logger.info(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} comments have all been obtained and filtered ...")
                await task_store.upsert_task_item(
                    crawl_run_id_var.get(), aweme_id, "comments", "completed", 1
                )
            except Exception as e:
                error_type = getattr(e, "error_type", "unknown")
                await task_store.upsert_task_item(
                    crawl_run_id_var.get(), aweme_id, "comments", "partial", 0,
                    error_type, str(e),
                )
                utils.logger.error(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} get comments failed, error: {e}")
                if error_type in {"api_schema_changed", "risk_controlled"}:
                    raise

    async def get_creators_and_videos(self) -> None:
        """
        Get the information and videos of the specified creator from URLs or IDs
        """
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Begin get douyin creators")
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Parsing creator URLs...")

        for creator_url in config.DY_CREATOR_ID_LIST:
            try:
                creator_info_parsed = parse_creator_info_from_url(creator_url)
                user_id = creator_info_parsed.sec_user_id
                utils.logger.info(
                    f"[DouYinCrawler.get_creators_and_videos] Parsed creator: "
                    f"{anonymize_user_id(user_id)}"
                )
            except ValueError as e:
                utils.logger.error(f"[DouYinCrawler.get_creators_and_videos] Failed to parse creator URL: {e}")
                continue

            await self.fetch_creator_profile(user_id)

            # Get all video information of the creator
            all_video_list = await self.dy_client.get_all_user_aweme_posts(
                sec_user_id=user_id,
                callback=self.fetch_creator_video_detail,
                max_count=config.CRAWLER_MAX_NOTES_COUNT,
                existing_checker=(
                    (lambda aweme_id: task_store.entity_exists("dy", "aweme", aweme_id))
                    if getattr(config, "DY_INCREMENTAL", False) else None
                ),
                stop_after_existing=int(getattr(config, "DY_STOP_AFTER_EXISTING", 5)),
            )

            video_ids = [
                video_item.get("aweme_id") for video_item in all_video_list
                if not getattr(config, "DY_INCREMENTAL", False)
                or str(video_item.get("aweme_id") or "") in self.new_aweme_ids
                or getattr(config, "DY_REFRESH_EXISTING_COMMENTS", False)
            ]
            await self.batch_get_note_comments(video_ids)

    async def fetch_creator_video_detail(self, video_list: List[Dict]):
        """
        Concurrently obtain the specified post list and save the data
        """
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(post_item.get("aweme_id"), semaphore) for post_item in video_list]

        note_details = await asyncio.gather(*task_list)
        for aweme_item in note_details:
            if aweme_item is not None:
                await self.process_aweme_detail(aweme_item)

    async def create_douyin_client(self, httpx_proxy: Optional[str]) -> DouYinClient:
        """Create douyin client"""
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context,
            urls=self.cookie_urls,
        )  # type: ignore
        douyin_client = DouYinClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                "Cookie": cookie_str,
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=self.ip_proxy_pool,  # Pass proxy pool for automatic refresh
        )
        return douyin_client

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        if config.SAVE_LOGIN_STATE:
            user_data_dir = os.path.join(os.getcwd(), "browser_data", config.USER_DATA_DIR % config.PLATFORM)  # type: ignore
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={
                    "width": 1920,
                    "height": 1080
                },
                user_agent=user_agent,
            )  # type: ignore
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy)  # type: ignore
            browser_context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent)
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        使用CDP模式启动浏览器
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # Add anti-detection script
            await self.cdp_manager.add_stealth_script()

            # Show browser information
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[DouYinCrawler] CDP浏览器信息: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] CDP模式启动失败，回退到标准模式: {e}")
            # Fall back to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self) -> None:
        """Close browser context"""
        # If you use CDP mode, special processing is required
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[DouYinCrawler.close] Browser context closed ...")

    async def get_aweme_media(self, aweme_item: Dict):
        """
        获取抖音媒体，自动判断媒体类型是短视频还是帖子图片并下载

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            utils.logger.info(f"[DouYinCrawler.get_aweme_media] Crawling image mode is not enabled")
            return
        # List of note urls. If it is a short video type, an empty list will be returned.
        note_download_url: List[str] = douyin_store._extract_note_image_list(aweme_item)
        # The video URL will always exist, but when it is a short video type, the file is actually an audio file.
        video_download_url: str = douyin_store._extract_video_download_url(aweme_item)
        # TODO: Douyin does not adopt the audio and video separation strategy, so the audio can be separated from the original video and will not be extracted for the time being.
        if note_download_url:
            await self.get_aweme_images(aweme_item)
        else:
            await self.get_aweme_video(aweme_item)

    async def get_aweme_images(self, aweme_item: Dict):
        """
        get aweme images. please use get_aweme_media

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")
        # List of note urls. If it is a short video type, an empty list will be returned.
        note_download_url: List[str] = douyin_store._extract_note_image_list(aweme_item)

        if not note_download_url:
            return
        picNum = 0
        for url in note_download_url:
            if not url:
                continue
            content = await self.dy_client.get_aweme_media(url)
            await asyncio.sleep(random.random())
            if content is None:
                continue
            extension_file_name = f"{picNum:>03d}.jpeg"
            picNum += 1
            await douyin_store.update_dy_aweme_image(aweme_id, content, extension_file_name)

    async def get_aweme_video(self, aweme_item: Dict):
        """
        get aweme videos. please use get_aweme_media

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")

        # The video URL will always exist, but when it is a short video type, the file is actually an audio file.
        video_download_url: str = douyin_store._extract_video_download_url(aweme_item)

        if not video_download_url:
            return
        content = await self.dy_client.get_aweme_media(video_download_url)
        await asyncio.sleep(random.random())
        if content is None:
            return
        extension_file_name = f"video.mp4"
        await douyin_store.update_dy_aweme_video(aweme_id, content, extension_file_name)
