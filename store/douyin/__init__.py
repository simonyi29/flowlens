# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/store/douyin/__init__.py
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

# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2024/1/14 18:46
# @Desc    :
from typing import Dict, List
import os

import config
from media_platform.douyin.normalizer import (
    normalize_aweme,
    normalize_comment,
    normalize_creator,
)
from model.m_douyin import (
    DouyinAwemeMetricSnapshot as DouyinAwemeMetricSnapshotData,
    DouyinCreatorMetricSnapshot as DouyinCreatorMetricSnapshotData,
    DouyinTopic as DouyinTopicData,
    DouyinTranscript as DouyinTranscriptData,
)
from tools import utils
from var import crawler_type_var
from api.services.task_store import task_store

from ._store_impl import *
from .douyin_store_media import *


async def _remote_result(entity_type: str, entity_id: str, payload: Dict):
    remote_run_id = os.getenv("FLOWLENS_WORKER_RUN_ID", "")
    if not remote_run_id:
        return
    await task_store.enqueue_outbox(f"result.{entity_type}", {
        "worker_id":os.getenv("FLOWLENS_WORKER_ID", ""),
        "run_id":remote_run_id, "entity_type":entity_type,
        "entity_id":str(entity_id), "payload":payload,
        "observed_at":payload.get("observed_at") or payload.get("collected_at"),
    })


class DouyinStoreFactory:
    STORES = {
        "csv": DouyinCsvStoreImplement,
        "db": DouyinDbStoreImplement,
        "postgres": DouyinDbStoreImplement,
        "json": DouyinJsonStoreImplement,
        "jsonl": DouyinJsonlStoreImplement,
        "sqlite": DouyinSqliteStoreImplement,
        "mongodb": DouyinMongoStoreImplement,
        "excel": DouyinExcelStoreImplement,
    }

    @staticmethod
    def create_store() -> AbstractStore:
        store_class = DouyinStoreFactory.STORES.get(config.SAVE_DATA_OPTION)
        if not store_class:
            raise ValueError("[DouyinStoreFactory.create_store] Invalid save option only supported csv or db or json or sqlite or mongodb or excel ...")
        return store_class()


def _extract_note_image_list(aweme_detail: Dict) -> List[str]:
    """
    Extract note image list

    Args:
        aweme_detail (Dict): Douyin content details

    Returns:
        List[str]: Note image list
    """
    images_res: List[str] = []
    images: List[Dict] = aweme_detail.get("images", [])

    if not images:
        return []

    for image in images:
        image_url_list = image.get("url_list", [])  # download_url_list has watermarked images, url_list has non-watermarked images
        if image_url_list:
            images_res.append(image_url_list[-1])

    return images_res


def _extract_comment_image_list(comment_item: Dict) -> List[str]:
    """
    Extract comment image list

    Args:
        comment_item (Dict): Douyin comment

    Returns:
        List[str]: Comment image list
    """
    images_res: List[str] = []
    image_list: List[Dict] = comment_item.get("image_list", [])

    if not image_list:
        return []

    for image in image_list:
        image_url_list = image.get("origin_url", {}).get("url_list", [])
        if image_url_list and len(image_url_list) > 1:
            images_res.append(image_url_list[1])

    return images_res


def _extract_content_cover_url(aweme_detail: Dict) -> str:
    """
    Extract video cover URL

    Args:
        aweme_detail (Dict): Douyin content details

    Returns:
        str: Video cover URL
    """
    res_cover_url = ""

    video_item = aweme_detail.get("video", {})
    raw_cover_url_list = (video_item.get("raw_cover", {}) or video_item.get("origin_cover", {})).get("url_list", [])
    if raw_cover_url_list and len(raw_cover_url_list) > 1:
        res_cover_url = raw_cover_url_list[1]

    return res_cover_url


def _extract_video_download_url(aweme_detail: Dict) -> str:
    """
    Extract video download URL

    Args:
        aweme_detail (Dict): Douyin video

    Returns:
        str: Video download URL
    """
    video_item = aweme_detail.get("video", {})
    url_h264_list = video_item.get("play_addr_h264", {}).get("url_list", [])
    url_256_list = video_item.get("play_addr_256", {}).get("url_list", [])
    url_list = video_item.get("play_addr", {}).get("url_list", [])
    actual_url_list = url_h264_list or url_256_list or url_list
    if not actual_url_list or len(actual_url_list) < 2:
        return ""
    return actual_url_list[-1]


def _extract_music_download_url(aweme_detail: Dict) -> str:
    """
    Extract music download URL

    Args:
        aweme_detail (Dict): Douyin video

    Returns:
        str: Music download URL
    """
    music_item = aweme_detail.get("music", {})
    play_url = music_item.get("play_url", {})
    music_url = play_url.get("uri", "")
    return music_url


async def update_douyin_aweme(aweme_item: Dict):
    normalized = normalize_aweme(aweme_item)
    save_content_item = normalized.model_dump(mode="json")
    save_content_item["last_modify_ts"] = utils.get_current_timestamp()
    aweme_id = normalized.aweme_id
    utils.logger.info(f"[store.douyin.update_douyin_aweme] douyin aweme id:{aweme_id}, title:{save_content_item.get('title')}")
    store = DouyinStoreFactory.create_store()
    await store.store_content(content_item=save_content_item)
    await _remote_result("aweme", aweme_id, save_content_item)
    if hasattr(store, "store_aweme_metric"):
        metric = DouyinAwemeMetricSnapshotData(
            aweme_id=aweme_id,
            liked_count=normalized.liked_count,
            collected_count=normalized.collected_count,
            comment_count=normalized.comment_count,
            share_count=normalized.share_count,
            play_count=normalized.play_count,
            observed_at=normalized.collected_at,
            crawl_run_id=normalized.crawl_run_id,
            source_mode=crawler_type_var.get(),
        )
        await store.store_aweme_metric(metric.model_dump())
        await _remote_result("aweme_metric", aweme_id, metric.model_dump(mode="json"))


async def batch_update_dy_aweme_comments(aweme_id: str, comments: List[Dict]):
    if not comments:
        return
    for comment_item in comments:
        normalized_item = dict(comment_item)
        normalized_item.setdefault("aweme_id", aweme_id)
        await update_dy_aweme_comment(aweme_id, normalized_item)


async def update_dy_aweme_comment(aweme_id: str, comment_item: Dict):
    comment_aweme_id = comment_item.get("aweme_id")
    if aweme_id != comment_aweme_id:
        utils.logger.error(f"[store.douyin.update_dy_aweme_comment] comment_aweme_id: {comment_aweme_id} != aweme_id: {aweme_id}")
        return
    normalized = normalize_comment(aweme_id, comment_item)
    comment_id = normalized.comment_id
    save_comment_item = normalized.model_dump(mode="json")
    save_comment_item["last_modify_ts"] = utils.get_current_timestamp()
    utils.logger.info(f"[store.douyin.update_dy_aweme_comment] douyin aweme comment: {comment_id}, content: {save_comment_item.get('content')}")

    await DouyinStoreFactory.create_store().store_comment(comment_item=save_comment_item)
    await _remote_result("comment", comment_id, save_comment_item)


async def save_creator(user_id: str, creator: Dict):
    normalized = normalize_creator(creator)
    if normalized is None:
        utils.logger.warning("[store.douyin.save_creator] creator response has no usable id")
        return
    store = DouyinStoreFactory.create_store()
    creator_item = normalized.model_dump(mode="json")
    await store.store_creator(creator_item)
    await _remote_result("creator", normalized.creator_hash, creator_item)
    if hasattr(store, "store_creator_metric"):
        metric = DouyinCreatorMetricSnapshotData(
            creator_hash=normalized.creator_hash,
            follower_count=normalized.follower_count,
            following_count=normalized.following_count,
            aweme_count=normalized.aweme_count,
            total_favorited=normalized.total_favorited,
            observed_at=normalized.collected_at,
            crawl_run_id=normalized.crawl_run_id,
            source_mode=crawler_type_var.get(),
        )
        await store.store_creator_metric(metric.model_dump())
        await _remote_result("creator_metric", normalized.creator_hash, metric.model_dump(mode="json"))


async def save_topic(topic: DouyinTopicData):
    store = DouyinStoreFactory.create_store()
    if not hasattr(store, "store_topic"):
        raise ValueError("Douyin topic storage requires JSONL or SQLite")
    await store.store_topic(topic.model_dump(mode="json"))
    await _remote_result("topic", topic.topic_id, topic.model_dump(mode="json"))


async def save_transcript(transcript: DouyinTranscriptData):
    store = DouyinStoreFactory.create_store()
    if not hasattr(store, "store_transcript"):
        raise ValueError("Douyin transcript storage requires JSONL or SQLite")
    await store.store_transcript(transcript.model_dump(mode="json"))
    await _remote_result("transcript", transcript.aweme_id, transcript.model_dump(mode="json"))


async def update_dy_aweme_image(aweme_id, pic_content, extension_file_name):
    """
    Update Douyin note image
    Args:
        aweme_id:
        pic_content:
        extension_file_name:

    Returns:

    """

    await DouYinImage().store_image({"aweme_id": aweme_id, "pic_content": pic_content, "extension_file_name": extension_file_name})


async def update_dy_aweme_video(aweme_id, video_content, extension_file_name):
    """
    Update Douyin short video
    Args:
        aweme_id:
        video_content:
        extension_file_name:

    Returns:

    """

    await DouYinVideo().store_video({"aweme_id": aweme_id, "video_content": video_content, "extension_file_name": extension_file_name})
