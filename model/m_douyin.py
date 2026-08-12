# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/model/m_douyin.py
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

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class VideoUrlInfo(BaseModel):
    """Douyin video URL information"""
    aweme_id: str = Field(title="aweme id (video id)")
    url_type: str = Field(default="normal", title="url type: normal, short, modal")


class CreatorUrlInfo(BaseModel):
    """Douyin creator URL information"""
    sec_user_id: str = Field(title="sec_user_id (creator id)")


class DouyinAweme(BaseModel):
    aweme_id: str
    aweme_type: Optional[str] = None
    aweme_url: str = ""
    title: str = ""
    desc: str = ""
    create_time: Optional[int] = None
    duration_ms: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    creator_hash: str = ""
    nickname: str = ""
    liked_count: Optional[int] = None
    collected_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    play_count: Optional[int] = None
    cover_url: str = ""
    video_download_url: str = ""
    music_id: str = ""
    music_title: str = ""
    music_author: str = ""
    music_download_url: str = ""
    note_download_url: str = ""
    hashtags: List[Dict[str, Any]] = Field(default_factory=list)
    mentions: List[Dict[str, Any]] = Field(default_factory=list)
    source_keyword: str = ""
    source_topic: str = ""
    crawl_run_id: str = ""
    collected_at: int = 0
    raw_payload: Optional[Dict[str, Any]] = None


class DouyinCreator(BaseModel):
    creator_hash: str
    nickname: str = ""
    signature: str = ""
    verified: bool = False
    verification_type: str = ""
    follower_count: Optional[int] = None
    following_count: Optional[int] = None
    aweme_count: Optional[int] = None
    total_favorited: Optional[int] = None
    crawl_run_id: str = ""
    collected_at: int = 0
    raw_payload: Optional[Dict[str, Any]] = None


class DouyinComment(BaseModel):
    comment_id: str
    aweme_id: str
    parent_comment_id: str = ""
    root_comment_id: str = ""
    level: int = 1
    content: str = ""
    create_time: Optional[int] = None
    creator_hash: str = ""
    nickname: str = ""
    sub_comment_count: Optional[int] = None
    like_count: Optional[int] = None
    pictures: List[str] = Field(default_factory=list)
    crawl_run_id: str = ""


class DouyinTopic(BaseModel):
    topic_id: str
    name: str = ""
    topic_url: str = ""
    view_count: Optional[int] = None
    aweme_count: Optional[int] = None
    crawl_run_id: str = ""
    collected_at: int = 0
    raw_payload: Optional[Dict[str, Any]] = None


class DouyinTranscriptSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str


class DouyinTranscript(BaseModel):
    aweme_id: str
    source: Literal["native", "asr", ""] = ""
    language: str = "zh"
    full_text: str = ""
    segments: List[DouyinTranscriptSegment] = Field(default_factory=list)
    srt_path: str = ""
    model_name: str = ""
    status: Literal[
        "pending", "native_completed", "asr_completed", "not_available", "failed"
    ] = "pending"
    error_message: str = ""
    retry_count: int = 0
    processed_at: int = 0


class DouyinAwemeMetricSnapshot(BaseModel):
    aweme_id: str
    liked_count: Optional[int] = None
    collected_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    play_count: Optional[int] = None
    observed_at: int
    crawl_run_id: str
    source_mode: str


class DouyinCreatorMetricSnapshot(BaseModel):
    creator_hash: str
    follower_count: Optional[int] = None
    following_count: Optional[int] = None
    aweme_count: Optional[int] = None
    total_favorited: Optional[int] = None
    observed_at: int
    crawl_run_id: str
    source_mode: str


class DouyinCrawlCheckpoint(BaseModel):
    scope: str
    scope_id: str
    cursor: str = "0"
    sub_cursor: str = "0"
    status: Literal["running", "complete", "partial", "failed"] = "running"
    expected_count: Optional[int] = None
    collected_count: int = 0
    last_error: str = ""
    pending_items: List[Dict[str, Any]] = Field(default_factory=list)
    updated_at: int = 0
