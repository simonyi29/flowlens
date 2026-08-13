"""User-facing view models shared by the local and remote FlowLens APIs."""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


STATUS_LABELS = {
    "queued": "排队中",
    "running": "正在采集",
    "pausing": "正在暂停",
    "paused": "已暂停",
    "waiting_for_login": "等待登录",
    "waiting_for_space": "磁盘空间不足",
    "partial": "部分完成",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}

STAGE_LABELS = {
    "discover": "发现作品",
    "detail": "作品详情",
    "creator": "账号资料",
    "comments": "评论",
    "native_transcript": "原生字幕",
    "media_download": "媒体下载",
    "asr": "语音转写",
    "finalize": "整理结果",
}


def parse_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_source(config: dict[str, Any], key: str) -> str:
    raw = config.get(key)
    if isinstance(raw, (list, tuple)):
        return next((str(item).strip() for item in raw if str(item).strip()), "")
    value = str(raw or "").strip()
    if not value:
        return ""
    return next((item.strip() for item in value.replace("\n", ",").split(",") if item.strip()), "")


def source_view(crawler_type: str, config: dict[str, Any]) -> tuple[str, str]:
    if crawler_type == "topic":
        source = _first_source(config, "topics")
        return (f"话题：{source}" if source else "历史话题采集"), source
    if crawler_type == "detail":
        source = _first_source(config, "specified_ids")
        compact = source if len(source) <= 18 else f"{source[:14]}…"
        return (f"视频详情：{compact}" if compact else "历史视频采集"), source
    if crawler_type == "creator":
        source = _first_source(config, "creator_ids")
        compact = source if len(source) <= 18 else f"{source[:14]}…"
        prefix = "账号增量" if config.get("incremental") else "指定账号"
        return (f"{prefix}：{compact}" if compact else "历史账号采集"), source
    source = _first_source(config, "keywords")
    return (f"关键词：{source}" if source else "历史关键词采集"), source


def allowed_actions(status: str, failed_count: int = 0) -> list[str]:
    return {
        "queued": ["cancel"],
        "running": ["pause", "cancel"],
        "pausing": ["cancel"],
        "paused": ["resume", "cancel"],
        "waiting_for_login": ["reconnect", "continue_after_login", "cancel"],
        "waiting_for_space": ["resume", "cancel"],
        "partial": ["view_failures", "retry_failed"] if failed_count else ["view_details", "rerun"],
        "completed": ["view_results", "rerun"],
        "failed": ["view_error", "retry_failed"],
        "cancelled": ["rerun"],
    }.get(status, [])


def _stage_counts(stages: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for stage in stages:
        key = str(stage.get("stage") or "unknown")
        result[key] = {
            "label": STAGE_LABELS.get(key, key),
            "status": stage.get("status") or "queued",
            "total": int(stage.get("total_count") or 0),
            "completed": int(stage.get("completed_count") or 0),
            "failed": int(stage.get("failed_count") or 0),
        }
    return result


def present_run(
    run: dict[str, Any],
    *,
    stages: Iterable[dict[str, Any]] = (),
    summary: dict[str, Any] | None = None,
    account_label: str | None = None,
    remote: bool = False,
) -> dict[str, Any]:
    raw_config = run.get("sanitized_config_json") if remote else run.get("config_json")
    config = parse_config(raw_config)
    crawler_type = str(run.get("crawler_type") or config.get("crawler_type") or "search")
    display_name, source_summary = source_view(crawler_type, config)
    stage_counts = _stage_counts(stages)
    detail = stage_counts.get("detail", {})
    discover = stage_counts.get("discover", {})
    completed = int(detail.get("completed") or discover.get("completed") or 0)
    total = int(config.get("max_notes_count") or detail.get("total") or discover.get("total") or 0)
    percent = round(min(completed / total * 100, 100), 1) if total else 0.0
    status = str(run.get("status") or "queued")
    failed_count = sum(int(item.get("failed") or 0) for item in stage_counts.values())
    if status in {"partial", "failed"} and run.get("error_type") and not failed_count:
        failed_count = 1
    summary = summary or {}
    return {
        **run,
        "display_name": display_name,
        "source_summary": source_summary,
        "source_missing": not bool(source_summary),
        "account_label": account_label or ("已连接抖音账号" if remote else "本机抖音账号"),
        "status_label": STATUS_LABELS.get(status, status),
        "stage_label": STAGE_LABELS.get(str(run.get("stage") or "discover"), str(run.get("stage") or "discover")),
        "progress": {
            "completed": completed,
            "total": total,
            "percent": percent,
            "determinate": total > 0,
        },
        "stage_counts": stage_counts,
        "failed_count": failed_count,
        "allowed_actions": allowed_actions(status, failed_count),
        "estimated_remaining_seconds": summary.get("estimated_remaining_seconds"),
        "elapsed_seconds": summary.get("elapsed_seconds"),
        "downloaded_bytes": summary.get("downloaded_bytes", 0),
        "task_media_quota_bytes": summary.get("task_media_quota_bytes", int(config.get("max_media_total_bytes") or 0)),
    }


def safe_error(error_type: str | None, detail: str | None) -> dict[str, Any] | None:
    if not error_type and not detail:
        return None
    messages = {
        "login_required": ("抖音登录已失效，请重新扫码后继续任务。", True, "reconnect"),
        "captcha_required": ("账号需要人工验证，请等待管理员处理。", True, "contact_admin"),
        "risk_controlled": ("抖音暂时限制了当前会话，请稍后重试或联系管理员。", True, "contact_admin"),
        "disk_quota_reached": ("任务媒体配额已用完，可调整配额后继续。", True, "increase_quota"),
        "disk_space_low": ("抓取设备磁盘空间不足，下载已暂停。", True, "free_space"),
        "asr_environment_error": ("本地语音转写环境不可用，其他采集结果不受影响。", True, "check_asr"),
        "network_timeout": ("网络请求超时，可以重试失败项。", True, "retry_failed"),
        "api_schema_changed": ("抖音接口结构发生变化，相关阶段已安全停止。", False, "contact_admin"),
    }
    user_message, recoverable, action = messages.get(
        error_type or "", ("任务执行时遇到问题，请查看技术详情或重试。", True, "retry_failed")
    )
    # Detail is already sanitized at ingestion. Cap it here to keep list responses concise.
    technical = str(detail or "")[:1000] or None
    return {
        "error_type": error_type or "unknown",
        "user_message": user_message,
        "technical_detail": technical,
        "recoverable": recoverable,
        "recommended_action": action,
    }
