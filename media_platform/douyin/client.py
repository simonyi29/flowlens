# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/douyin/client.py
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
import copy
import json
import urllib.parse
from typing import TYPE_CHECKING, Any, Callable, Dict, Union, Optional

import httpx
from playwright.async_api import BrowserContext

from base.base_crawler import AbstractApiClient
from proxy.proxy_mixin import ProxyRefreshMixin
from tools import utils
from tools.httpx_util import make_async_client
from tools.user_hash import anonymize_user_id
from database.douyin_state import load_checkpoint, save_checkpoint
from model.m_douyin import DouyinCrawlCheckpoint
from var import request_keyword_var

if TYPE_CHECKING:
    from proxy.proxy_ip_pool import ProxyIpPool

from .exception import *
from .field import *
from .help import *
from .normalizer import optional_int


class DouYinClient(AbstractApiClient, ProxyRefreshMixin):

    def __init__(
        self,
        timeout=60,  # If the crawl media option is turned on, Douyin’s short videos will require a longer timeout.
        proxy=None,
        *,
        headers: Dict,
        playwright_page: Optional[Page],
        cookie_dict: Dict,
        proxy_ip_pool: Optional["ProxyIpPool"] = None,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        self._host = "https://www.douyin.com"
        self.cookie_urls = [
            "https://douyin.com",
            self._host,
            "https://creator.douyin.com",
            "https://douhot.douyin.com",
            "https://live.douyin.com",
        ]
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict
        # Initialize proxy pool (from ProxyRefreshMixin)
        self.init_proxy_pool(proxy_ip_pool)

    async def __process_req_params(
        self,
        uri: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        request_method="GET",
    ):

        if not params:
            return
        headers = headers or self.headers
        local_storage: Dict = await self.playwright_page.evaluate("() => window.localStorage")  # type: ignore
        common_params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "version_code": "190600",
            "version_name": "19.6.0",
            "update_version_code": "170400",
            "pc_client_type": "1",
            "cookie_enabled": "true",
            "browser_language": "zh-CN",
            "browser_platform": "MacIntel",
            "browser_name": "Chrome",
            "browser_version": "125.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "os_name": "Mac OS",
            "os_version": "10.15.7",
            "cpu_core_num": "8",
            "device_memory": "8",
            "engine_version": "109.0",
            "platform": "PC",
            "screen_width": "2560",
            "screen_height": "1440",
            'effective_type': '4g',
            "round_trip_time": "50",
            "webid": get_web_id(),
            "msToken": local_storage.get("xmst"),
        }
        params.update(common_params)
        query_string = urllib.parse.urlencode(params)

        # 20240927 a-bogus update (JS version)
        post_data = {}
        if request_method == "POST":
            post_data = params

        if "/v1/web/general/search" not in uri:
            a_bogus = await get_a_bogus(uri, query_string, post_data, headers["User-Agent"], self.playwright_page)
            params["a_bogus"] = a_bogus

    async def request(self, method, url, **kwargs):
        # Check whether the proxy has expired before each request
        await self._refresh_proxy_if_expired()

        async with make_async_client(proxy=self.proxy) as client:
            response = await client.request(method, url, timeout=self.timeout, **kwargs)
        try:
            if response.text == "" or response.text == "blocked":
                utils.logger.error(f"request params incrr, response.text: {response.text}")
                raise Exception("account blocked")
            return response.json()
        except Exception as e:
            raise DataFetchError(f"{e}, {response.text}")

    async def get(self, uri: str, params: Optional[Dict] = None, headers: Optional[Dict] = None):
        """
        GET请求
        """
        await self.__process_req_params(uri, params, headers)
        headers = headers or self.headers
        return await self.request(method="GET", url=f"{self._host}{uri}", params=params, headers=headers)

    async def post(self, uri: str, data: dict, headers: Optional[Dict] = None):
        await self.__process_req_params(uri, data, headers)
        headers = headers or self.headers
        return await self.request(method="POST", url=f"{self._host}{uri}", data=data, headers=headers)

    async def pong(self, browser_context: BrowserContext) -> bool:
        local_storage = await self.playwright_page.evaluate("() => window.localStorage")
        if local_storage.get("HasUserLogin", "") == "1":
            return True

        _, cookie_dict = await utils.convert_browser_context_cookies(
            browser_context,
            urls=self.cookie_urls,
        )
        return cookie_dict.get("LOGIN_STATUS") == "1"

    async def update_cookies(self, browser_context: BrowserContext, urls: Optional[list[str]] = None):
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            browser_context,
            urls=urls or self.cookie_urls,
        )
        self.headers["Cookie"] = cookie_str
        self.cookie_dict = cookie_dict

    async def search_info_by_keyword(
        self,
        keyword: str,
        offset: int = 0,
        search_channel: SearchChannelType = SearchChannelType.GENERAL,
        sort_type: SearchSortType = SearchSortType.GENERAL,
        publish_time: PublishTimeType = PublishTimeType.UNLIMITED,
        search_id: str = "",
        count: int = 15,
    ):
        """
        DouYin Web Search API
        :param keyword:
        :param offset:
        :param search_channel:
        :param sort_type:
        :param publish_time: ·
        :param search_id: ·
        :return:
        """
        query_params = {
            'search_channel': search_channel.value,
            'enable_history': '1',
            'keyword': keyword,
            'search_source': 'tab_search',
            'query_correct_type': '1',
            'is_filter_search': '0',
            'from_group_id': '7378810571505847586',
            'offset': offset,
            'count': str(count),
            'need_filter_settings': '1',
            'list_type': 'multi',
            'search_id': search_id,
        }
        if sort_type.value != SearchSortType.GENERAL.value or publish_time.value != PublishTimeType.UNLIMITED.value:
            query_params["filter_selected"] = json.dumps({"sort_type": str(sort_type.value), "publish_time": str(publish_time.value)})
            query_params["is_filter_search"] = 1
            query_params["search_source"] = "tab_search"
        referer_url = f"https://www.douyin.com/search/{keyword}?aid=f594bbd9-a0e2-4651-9319-ebe3cb6298c1&type=general"
        headers = copy.copy(self.headers)
        headers["Referer"] = urllib.parse.quote(referer_url, safe=':/')
        return await self.get("/aweme/v1/web/general/search/single/", query_params, headers=headers)

    async def get_video_by_id(self, aweme_id: str) -> Any:
        """
        DouYin Video Detail API
        :param aweme_id:
        :return:
        """
        params = {"aweme_id": aweme_id}
        headers = copy.copy(self.headers)
        del headers["Origin"]
        res = await self.get("/aweme/v1/web/aweme/detail/", params, headers)
        return res.get("aweme_detail", {})

    async def discover_topic(self, topic_name: str) -> Dict:
        """Resolve a topic name to a real challenge id using search metadata only."""
        response = await self.search_info_by_keyword(
            keyword=f"#{topic_name.lstrip('#')}", count=10
        )
        wanted = topic_name.lstrip("#").strip().casefold()
        candidates = []
        for entry in response.get("data") or []:
            aweme = entry.get("aweme_info") or {}
            for extra in aweme.get("text_extra") or []:
                topic_id = str(extra.get("hashtag_id") or "")
                name = str(extra.get("hashtag_name") or "")
                if topic_id and name:
                    candidates.append({"topic_id": topic_id, "name": name})
        exact = [item for item in candidates if item["name"].casefold() == wanted]
        if len({item["topic_id"] for item in exact}) == 1:
            return exact[0]
        raise DataFetchError(f"Unable to resolve a unique topic for {topic_name!r}")

    async def get_topic_detail(self, topic_id: str) -> Dict:
        return await self.get(
            "/aweme/v1/web/challenge/detail/", {"ch_id": topic_id}
        )

    async def get_topic_awemes(
        self, topic_id: str, cursor: int = 0, count: int = 20
    ) -> Dict:
        return await self.get(
            "/aweme/v1/web/challenge/aweme/",
            {"ch_id": topic_id, "cursor": cursor, "count": count},
        )

    async def get_aweme_comments(self, aweme_id: str, cursor: int = 0):
        """get note comments

        """
        uri = "/aweme/v1/web/comment/list/"
        params = {"aweme_id": aweme_id, "cursor": cursor, "count": 20, "item_type": 0}
        keywords = request_keyword_var.get()
        referer_url = "https://www.douyin.com/search/" + keywords + '?aid=3a3cec5a-9e27-4040-b6aa-ef548c2c1138&publish_time=0&sort_type=0&source=search_history&type=general'
        headers = copy.copy(self.headers)
        headers["Referer"] = urllib.parse.quote(referer_url, safe=':/')
        return await self.get(uri, params)

    async def get_sub_comments(self, aweme_id: str, comment_id: str, cursor: int = 0):
        """
            获取子评论
        """
        uri = "/aweme/v1/web/comment/list/reply/"
        params = {
            'comment_id': comment_id,
            "cursor": cursor,
            "count": 20,
            "item_type": 0,
            "item_id": aweme_id,
        }
        keywords = request_keyword_var.get()
        referer_url = "https://www.douyin.com/search/" + keywords + '?aid=3a3cec5a-9e27-4040-b6aa-ef548c2c1138&publish_time=0&sort_type=0&source=search_history&type=general'
        headers = copy.copy(self.headers)
        headers["Referer"] = urllib.parse.quote(referer_url, safe=':/')
        return await self.get(uri, params)

    async def get_aweme_all_comments(
        self,
        aweme_id: str,
        crawl_interval: float = 1.0,
        is_fetch_sub_comments=False,
        callback: Optional[Callable] = None,
        max_count: int = 10,
    ):
        """
        获取帖子的所有评论，包括子评论
        :param aweme_id: 帖子ID
        :param crawl_interval: 抓取间隔
        :param is_fetch_sub_comments: 是否抓取子评论
        :param callback: 回调函数，用于处理抓取到的评论
        :param max_count: 一次帖子爬取的最大评论数量
        :return: 评论列表
        """
        result = []
        checkpoint = await load_checkpoint("comments", aweme_id)
        if checkpoint and checkpoint.status == "complete":
            return result
        comments_cursor = int(checkpoint.cursor) if checkpoint else 0
        collected_count = checkpoint.collected_count if checkpoint else 0
        expected_count = checkpoint.expected_count if checkpoint else None
        pending_items = list(checkpoint.pending_items) if checkpoint else []
        comments_has_more = (
            checkpoint.sub_cursor != "0"
            if checkpoint and checkpoint.pending_items
            else True
        )

        def remaining() -> Optional[int]:
            return None if max_count == 0 else max(0, max_count - collected_count)

        async def persist_parent(status: str = "running", error: str = "") -> None:
            await save_checkpoint(
                DouyinCrawlCheckpoint(
                    scope="comments", scope_id=aweme_id,
                    cursor=str(comments_cursor), sub_cursor="1" if comments_has_more else "0",
                    status=status, expected_count=expected_count,
                    collected_count=collected_count, last_error=error,
                    pending_items=pending_items,
                    updated_at=utils.get_current_timestamp(),
                )
            )

        async def process_pending_replies() -> None:
            nonlocal collected_count
            for pending in list(pending_items):
                if max_count and collected_count >= max_count:
                    break
                comment_id = str(pending.get("comment_id") or "")
                reply_total = optional_int(pending.get("expected_count")) or 0
                if not comment_id:
                    pending_items.remove(pending)
                    continue
                sub_scope_id = f"{aweme_id}:{comment_id}"
                sub_checkpoint = await load_checkpoint("sub_comments", sub_scope_id)
                accounted = int(pending.get("accounted_count") or 0)
                already_saved = sub_checkpoint.collected_count if sub_checkpoint else 0
                if already_saved > accounted:
                    collected_count += already_saved - accounted
                    pending["accounted_count"] = already_saved
                    await persist_parent()
                if sub_checkpoint and sub_checkpoint.status == "complete":
                    pending_items.remove(pending)
                    await persist_parent()
                    continue
                sub_cursor = int(sub_checkpoint.cursor) if sub_checkpoint else 0
                sub_collected = already_saved
                sub_has_more = True
                while sub_has_more and (max_count == 0 or collected_count < max_count):
                    previous_sub_cursor = sub_cursor
                    response = await self.get_sub_comments(aweme_id, comment_id, sub_cursor)
                    sub_has_more = bool(response.get("has_more", 0))
                    sub_cursor = int(response.get("cursor") or previous_sub_cursor)
                    sub_comments = response.get("comments") or []
                    limit = remaining()
                    if limit is not None:
                        sub_comments = sub_comments[:limit]
                    for sub_comment in sub_comments:
                        sub_comment.setdefault("aweme_id", aweme_id)
                        sub_comment["reply_id"] = str(sub_comment.get("reply_id") or comment_id)
                        sub_comment.setdefault("reply_to_reply_id", comment_id)
                    if sub_comments:
                        if callback:
                            await callback(aweme_id, sub_comments)
                        result.extend(sub_comments)
                        sub_collected += len(sub_comments)
                    sub_status = "complete" if not sub_has_more or (max_count and collected_count + len(sub_comments) >= max_count) else "running"
                    await save_checkpoint(
                        DouyinCrawlCheckpoint(
                            scope="sub_comments", scope_id=sub_scope_id,
                            cursor=str(sub_cursor), status=sub_status,
                            expected_count=reply_total, collected_count=sub_collected,
                            updated_at=utils.get_current_timestamp(),
                        )
                    )
                    collected_count += len(sub_comments)
                    pending["accounted_count"] = sub_collected
                    await persist_parent()
                    if not sub_comments or sub_cursor == previous_sub_cursor:
                        break
                    await asyncio.sleep(crawl_interval)
                if not sub_has_more or (max_count and collected_count >= max_count):
                    pending_items.remove(pending)
                    await persist_parent()

        try:
            if pending_items:
                await process_pending_replies()
                if not comments_has_more and not pending_items:
                    await persist_parent("complete")
                    return result

            while comments_has_more and (max_count == 0 or collected_count < max_count):
                current_cursor = comments_cursor
                response = await self.get_aweme_comments(aweme_id, comments_cursor)
                comments_has_more = bool(response.get("has_more", 0))
                comments_cursor = int(response.get("cursor") or current_cursor)
                expected_count = optional_int(response.get("total") or response.get("total_count")) or expected_count
                comments = response.get("comments") or []
                limit = remaining()
                if limit is not None:
                    comments = comments[:limit]
                if comments:
                    if callback:
                        await callback(aweme_id, comments)
                    result.extend(comments)
                    collected_count += len(comments)
                if is_fetch_sub_comments:
                    for comment in comments:
                        reply_total = optional_int(comment.get("reply_comment_total")) or 0
                        comment_id = str(comment.get("cid") or "")
                        if reply_total > 0 and comment_id:
                            pending_items.append({
                                "comment_id": comment_id,
                                "expected_count": reply_total,
                                "accounted_count": 0,
                            })

                # The primary page is durable before any reply request begins.
                await persist_parent()
                if pending_items and (max_count == 0 or collected_count < max_count):
                    await process_pending_replies()
                status = "complete" if not comments_has_more or (max_count and collected_count >= max_count) else "running"
                if status == "complete":
                    pending_items.clear()
                await persist_parent(status)
                if not comments or comments_cursor == current_cursor:
                    break
                await asyncio.sleep(crawl_interval)
        except Exception as exc:
            await persist_parent("partial" if collected_count else "failed", str(exc))
            raise
        return result

    async def get_user_info(self, sec_user_id: str):
        uri = "/aweme/v1/web/user/profile/other/"
        params = {
            "sec_user_id": sec_user_id,
            "publish_video_strategy_type": 2,
            "personal_center_strategy": 1,
        }
        return await self.get(uri, params)

    async def get_user_aweme_posts(self, sec_user_id: str, max_cursor: str = "") -> Dict:
        uri = "/aweme/v1/web/aweme/post/"
        params = {
            "sec_user_id": sec_user_id,
            "count": 18,
            "max_cursor": max_cursor,
            "locate_query": "false",
            "publish_video_strategy_type": 2,
        }
        return await self.get(uri, params)

    async def get_all_user_aweme_posts(
        self,
        sec_user_id: str,
        callback: Optional[Callable] = None,
        max_count: int = 0,
    ):
        checkpoint_id = anonymize_user_id(sec_user_id)
        checkpoint = await load_checkpoint("creator_posts", checkpoint_id)
        if checkpoint and checkpoint.status == "complete":
            return []
        posts_has_more = 1
        max_cursor = checkpoint.cursor if checkpoint else ""
        collected = checkpoint.collected_count if checkpoint else 0
        result = []
        seen_aweme_ids: set[str] = set()
        if max_count > 0 and collected >= max_count:
            return result
        try:
            while posts_has_more == 1 and (max_count <= 0 or collected < max_count):
                current_cursor = max_cursor
                aweme_post_res = await self.get_user_aweme_posts(sec_user_id, max_cursor)
                posts_has_more = aweme_post_res.get("has_more", 0)
                next_cursor = str(aweme_post_res.get("max_cursor") or current_cursor)
                aweme_list = aweme_post_res.get("aweme_list") or []
                unique_awemes = []
                for aweme in aweme_list:
                    aweme_id = str(aweme.get("aweme_id") or "")
                    if not aweme_id or aweme_id in seen_aweme_ids:
                        continue
                    seen_aweme_ids.add(aweme_id)
                    unique_awemes.append(aweme)
                if max_count > 0:
                    unique_awemes = unique_awemes[: max_count - collected]
                utils.logger.info(
                    f"[DouYinClient.get_all_user_aweme_posts] creator:{checkpoint_id} "
                    f"video len:{len(unique_awemes)}"
                )
                if callback:
                    await callback(unique_awemes)
                result.extend(unique_awemes)
                collected += len(unique_awemes)
                max_cursor = next_cursor
                reached_limit = max_count > 0 and collected >= max_count
                await save_checkpoint(
                    DouyinCrawlCheckpoint(
                        scope="creator_posts", scope_id=checkpoint_id,
                        cursor=max_cursor,
                        status="complete" if reached_limit or not posts_has_more else "running",
                        collected_count=collected,
                        updated_at=utils.get_current_timestamp(),
                    )
                )
                if reached_limit or not aweme_list or next_cursor == current_cursor:
                    break
        except Exception as exc:
            await save_checkpoint(
                DouyinCrawlCheckpoint(
                    scope="creator_posts", scope_id=checkpoint_id,
                    cursor=max_cursor, status="partial" if collected else "failed",
                    collected_count=collected, last_error=str(exc),
                    updated_at=utils.get_current_timestamp(),
                )
            )
            raise
        return result

    async def get_aweme_media(self, url: str) -> Union[bytes, None]:
        async with make_async_client(proxy=self.proxy) as client:
            try:
                response = await client.request("GET", url, timeout=self.timeout, follow_redirects=True)
                response.raise_for_status()
                if not response.reason_phrase == "OK":
                    utils.logger.error(f"[DouYinClient.get_aweme_media] request {url} err, res:{response.text}")
                    return None
                else:
                    return response.content
            except httpx.HTTPError as exc:  # some wrong when call httpx.request method, such as connection error, client error, server error or response status code is not 2xx
                utils.logger.error(f"[DouYinClient.get_aweme_media] {exc.__class__.__name__} for {exc.request.url} - {exc}")  # Keep the original exception type name for developers to debug
                return None

    async def resolve_short_url(self, short_url: str) -> str:
        """
        解析抖音短链接,获取重定向后的真实URL
        Args:
            short_url: 短链接,如 https://v.douyin.com/iF12345ABC/
        Returns:
            重定向后的完整URL
        """
        async with make_async_client(proxy=self.proxy, follow_redirects=False) as client:
            try:
                utils.logger.info(f"[DouYinClient.resolve_short_url] Resolving short URL: {short_url}")
                response = await client.get(short_url, timeout=10)

                # Short links usually return a 302 redirect
                if response.status_code in [301, 302, 303, 307, 308]:
                    redirect_url = response.headers.get("Location", "")
                    utils.logger.info(f"[DouYinClient.resolve_short_url] Resolved to: {redirect_url}")
                    return redirect_url
                else:
                    utils.logger.warning(f"[DouYinClient.resolve_short_url] Unexpected status code: {response.status_code}")
                    return ""
            except Exception as e:
                utils.logger.error(f"[DouYinClient.resolve_short_url] Failed to resolve short URL: {e}")
                return ""
