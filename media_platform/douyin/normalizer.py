"""Normalize Douyin responses into privacy-preserving storage models."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Optional

import config
from model.m_douyin import DouyinAweme, DouyinComment, DouyinCreator
from tools.user_hash import anonymize_user_id, mask_nickname
from tools import utils
from var import crawl_run_id_var, source_keyword_var, source_topic_var


SENSITIVE_KEYS = {
    "uid",
    "user_id",
    "sec_uid",
    "sec_user_id",
    "short_id",
    "unique_id",
    "user_unique_id",
    "avatar",
    "avatar_thumb",
    "avatar_medium",
    "avatar_larger",
    "signature",
    "gender",
    "user_age",
    "age",
    "birthday",
    "ip_label",
    "ip_location",
    "province",
    "city",
    "country",
    "mobile",
    "phone",
    "email",
    "authentication_token",
    "access_token",
    "refresh_token",
    "passport_ticket",
    "share_qrcode_url",
    "homepage",
    "homepage_url",
    "profile_url",
}

SENSITIVE_ID_KEYS = {
    "author_id",
    "author_user_id",
    "from_user_id",
    "owner_id",
    "social_author_id",
    "social_share_user_id",
    "to_user_id",
}

PERSON_PROFILE_MEDIA_KEYS = {
    "avatar_168x168",
    "avatar_300x300",
    "avatar_large",
    "avatar_medium",
    "avatar_thumb",
    "avatar_uri",
    "cover_url",
}


def optional_int(value: Any) -> Optional[int]:
    """Return a real integer or None; never turn missing data into zero."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def sanitize_raw_payload(value: Any, *, _person_context: bool = False) -> Any:
    """Recursively remove identifiers and mask nicknames in an API response."""
    if isinstance(value, str):
        stripped = value.strip()
        if "aweme-avatar" in stripped.lower() or "avatar/" in stripped.lower():
            return None
        if stripped.startswith(("{", "[")):
            try:
                nested = json.loads(stripped)
            except json.JSONDecodeError:
                return copy.deepcopy(value)
            return json.dumps(
                sanitize_raw_payload(nested, _person_context=_person_context),
                ensure_ascii=False,
            )
        return copy.deepcopy(value)
    if isinstance(value, list):
        return [sanitize_raw_payload(item, _person_context=_person_context) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)

    sanitized: Dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = str(key).lower()
        is_person_context = _person_context or normalized_key in {
            "author", "user", "user_info", "creator", "owner"
        }
        if (
            normalized_key in SENSITIVE_KEYS
            or normalized_key in SENSITIVE_ID_KEYS
            or normalized_key.endswith("_token")
            or normalized_key.endswith("_user_id")
            or "sec_uid" in normalized_key
            or "sec_user_id" in normalized_key
            or "avatar" in normalized_key
            or (is_person_context and normalized_key in PERSON_PROFILE_MEDIA_KEYS)
        ):
            continue
        if (
            normalized_key in {"nickname", "name", "screen_name"}
            or normalized_key.endswith("_nickname")
            or (normalized_key == "author" and isinstance(item, str))
        ):
            sanitized[key] = mask_nickname(item)
        elif normalized_key == "title" and isinstance(item, str):
            original_sound = re.fullmatch(r"@(.+)创作的原声", item.strip())
            sanitized[key] = (
                f"@{mask_nickname(original_sound.group(1))}创作的原声"
                if original_sound else copy.deepcopy(item)
            )
        else:
            sanitized[key] = sanitize_raw_payload(item, _person_context=is_person_context)
    return sanitized


def _first_url(container: Any) -> str:
    if isinstance(container, str):
        return container
    if not isinstance(container, dict):
        return ""
    for value in container.get("url_list", []):
        if value:
            return str(value)
    return str(container.get("uri") or "")


def _video_url(video: Dict[str, Any]) -> str:
    for key in ("play_addr_h264", "play_addr_256", "play_addr"):
        value = _first_url(video.get(key))
        if value:
            return value
    return ""


def _cover_url(video: Dict[str, Any]) -> str:
    for key in ("raw_cover", "origin_cover", "cover", "dynamic_cover"):
        value = _first_url(video.get(key))
        if value:
            return value
    return ""


def normalize_aweme(aweme_item: Dict[str, Any]) -> DouyinAweme:
    author = aweme_item.get("author") or {}
    statistics = aweme_item.get("statistics") or aweme_item.get("statistics_v2") or {}
    video = aweme_item.get("video") or {}
    music = aweme_item.get("music") or {}
    images = aweme_item.get("images") or []
    dimensions = video.get("play_addr") or video.get("play_addr_h264") or {}
    aweme_id = str(aweme_item.get("aweme_id") or "")

    image_urls = []
    for image in images:
        url = _first_url(image)
        if url:
            image_urls.append(url)

    raw_payload = None
    if getattr(config, "DY_SAVE_RAW_PAYLOAD", False):
        raw_payload = sanitize_raw_payload(aweme_item)

    return DouyinAweme(
        aweme_id=aweme_id,
        aweme_type=str(aweme_item.get("aweme_type")) if aweme_item.get("aweme_type") is not None else None,
        aweme_url=f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
        title=str(aweme_item.get("desc") or ""),
        desc=str(aweme_item.get("desc") or ""),
        create_time=optional_int(aweme_item.get("create_time")),
        duration_ms=optional_int(aweme_item.get("duration") or video.get("duration")),
        width=optional_int(dimensions.get("width") or video.get("width")),
        height=optional_int(dimensions.get("height") or video.get("height")),
        creator_hash=anonymize_user_id(author.get("uid")),
        nickname=mask_nickname(author.get("nickname")),
        liked_count=optional_int(statistics.get("digg_count")),
        collected_count=optional_int(statistics.get("collect_count")),
        comment_count=optional_int(statistics.get("comment_count")),
        share_count=optional_int(statistics.get("share_count")),
        play_count=optional_int(statistics.get("play_count")),
        cover_url=_cover_url(video),
        video_download_url=_video_url(video),
        music_id=str(music.get("id_str") or music.get("id") or ""),
        music_title=str(music.get("title") or ""),
        music_author=str(music.get("author") or ""),
        music_download_url=_first_url(music.get("play_url")),
        note_download_url=",".join(image_urls),
        hashtags=[
            {
                "id": str(item.get("hashtag_id") or ""),
                "name": str(item.get("hashtag_name") or ""),
            }
            for item in (aweme_item.get("text_extra") or [])
            if item.get("hashtag_name")
        ],
        mentions=[
            {"nickname": mask_nickname(item.get("user_unique_id") or item.get("nickname"))}
            for item in (aweme_item.get("text_extra") or [])
            if item.get("type") == 0 and (item.get("user_unique_id") or item.get("nickname"))
        ],
        source_keyword=source_keyword_var.get(),
        source_topic=source_topic_var.get(),
        crawl_run_id=crawl_run_id_var.get(),
        collected_at=utils.get_current_timestamp(),
        raw_payload=raw_payload,
    )


def normalize_creator(creator_response: Dict[str, Any]) -> Optional[DouyinCreator]:
    user = creator_response.get("user") or creator_response.get("user_info") or creator_response
    if not isinstance(user, dict):
        return None
    stats = creator_response.get("stats") or creator_response.get("user_stats") or user
    raw_id = user.get("uid")
    creator_hash = anonymize_user_id(raw_id)
    if not creator_hash:
        return None
    verification_type = str(
        user.get("custom_verify") or user.get("enterprise_verify_reason") or ""
    )
    raw_payload = None
    if getattr(config, "DY_SAVE_RAW_PAYLOAD", False):
        raw_payload = sanitize_raw_payload(creator_response)
    return DouyinCreator(
        creator_hash=creator_hash,
        nickname=mask_nickname(user.get("nickname")),
        signature=str(user.get("signature") or ""),
        verified=bool(verification_type or user.get("verification_type")),
        verification_type=verification_type,
        follower_count=optional_int(stats.get("follower_count")),
        following_count=optional_int(stats.get("following_count")),
        aweme_count=optional_int(stats.get("aweme_count")),
        total_favorited=optional_int(stats.get("total_favorited")),
        crawl_run_id=crawl_run_id_var.get(),
        collected_at=utils.get_current_timestamp(),
        raw_payload=raw_payload,
    )


def normalize_comment(aweme_id: str, comment_item: Dict[str, Any]) -> DouyinComment:
    user = comment_item.get("user") or {}
    parent_id = str(comment_item.get("reply_id") or "")
    root_id = str(comment_item.get("reply_to_reply_id") or parent_id or "")
    level = 2 if parent_id not in {"", "0"} else 1
    pictures = []
    for image in comment_item.get("image_list") or []:
        url = _first_url(image.get("origin_url") or image)
        if url:
            pictures.append(url)
    return DouyinComment(
        comment_id=str(comment_item.get("cid") or ""),
        aweme_id=aweme_id,
        parent_comment_id=parent_id,
        root_comment_id=root_id,
        level=level,
        content=str(comment_item.get("text") or ""),
        create_time=optional_int(comment_item.get("create_time")),
        creator_hash=anonymize_user_id(user.get("uid")),
        nickname=mask_nickname(user.get("nickname")),
        sub_comment_count=optional_int(comment_item.get("reply_comment_total")),
        like_count=optional_int(comment_item.get("digg_count")),
        pictures=pictures,
        crawl_run_id=crawl_run_id_var.get(),
    )
