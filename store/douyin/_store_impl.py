# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/store/douyin/_store_impl.py
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
# @Author  : persist1@126.com
# @Time    : 2025/9/5 19:34
# @Desc    : Douyin storage implementation class
import json
from pathlib import Path
from typing import Dict

from sqlalchemy import select

import config
from base.base_crawler import AbstractStore
from database.db_session import get_session
from database.models import (
    DouyinAweme,
    DouyinAwemeComment,
    DouyinAwemeMetricSnapshot,
    DouyinCreator as DouyinCreatorModel,
    DouyinCreatorMetricSnapshot,
    DouyinTopic,
    DouyinTranscript,
)
from tools import utils
from tools.async_file_writer import AsyncFileWriter
from var import crawler_type_var
from database.mongodb_store_base import MongoDBStoreBase


class DouyinJsonlEventWriter(AsyncFileWriter):
    """Stable append-only event filenames used only by enhanced Douyin output."""

    def _get_file_path(self, file_type: str, item_type: str) -> str:
        if file_type != "jsonl":
            return super()._get_file_path(file_type, item_type)
        root = Path(config.SAVE_DATA_PATH) if config.SAVE_DATA_PATH else Path("data")
        output_dir = root / "douyin"
        output_dir.mkdir(parents=True, exist_ok=True)
        return str(output_dir / f"{item_type}.jsonl")


class DouyinCsvStoreImplement(AbstractStore):
    def __init__(self):
        self.file_writer = AsyncFileWriter(
            crawler_type=crawler_type_var.get(),
            platform="douyin"
        )

    async def store_content(self, content_item: Dict):
        """
        Douyin content CSV storage implementation
        Args:
            content_item: note item dict

        Returns:

        """
        await self.file_writer.write_to_csv(
            item=content_item,
            item_type="contents"
        )

    async def store_comment(self, comment_item: Dict):
        """
        Douyin comment CSV storage implementation
        Args:
            comment_item: comment item dict

        Returns:

        """
        await self.file_writer.write_to_csv(
            item=comment_item,
            item_type="comments"
        )

    async def store_creator(self, creator: Dict):
        """
        Douyin creator CSV storage implementation
        Args:
            creator: creator item dict

        Returns:

        """
        await self.file_writer.write_to_csv(
            item=creator,
            item_type="creators"
        )


class DouyinDbStoreImplement(AbstractStore):
    async def store_content(self, content_item: Dict):
        """
        Douyin content DB storage implementation
        Args:
            content_item: content item dict
        """
        aweme_id = content_item.get("aweme_id")
        db_item = dict(content_item)
        for key in ("hashtags", "mentions", "raw_payload"):
            if isinstance(db_item.get(key), (list, dict)):
                db_item[key] = json.dumps(db_item[key], ensure_ascii=False)
        async with get_session() as session:
            result = await session.execute(select(DouyinAweme).where(DouyinAweme.aweme_id == aweme_id))
            aweme_detail = result.scalars().first()

            if not aweme_detail:
                db_item["add_ts"] = utils.get_current_timestamp()
                if aweme_id:
                    new_content = DouyinAweme(**db_item)
                    session.add(new_content)
            else:
                for key, value in db_item.items():
                    setattr(aweme_detail, key, value)
            await session.commit()

    async def store_comment(self, comment_item: Dict):
        """
        Douyin comment DB storage implementation
        Args:
            comment_item: comment item dict
        """
        comment_id = comment_item.get("comment_id")
        db_item = dict(comment_item)
        if isinstance(db_item.get("pictures"), list):
            db_item["pictures"] = ",".join(db_item["pictures"])
        async with get_session() as session:
            result = await session.execute(select(DouyinAwemeComment).where(DouyinAwemeComment.comment_id == comment_id))
            comment_detail = result.scalars().first()

            if not comment_detail:
                db_item["add_ts"] = utils.get_current_timestamp()
                new_comment = DouyinAwemeComment(**db_item)
                session.add(new_comment)
            else:
                for key, value in db_item.items():
                    setattr(comment_detail, key, value)
            await session.commit()

    async def store_creator(self, creator: Dict):
        db_item = dict(creator)
        if isinstance(db_item.get("raw_payload"), dict):
            db_item["raw_payload"] = json.dumps(db_item["raw_payload"], ensure_ascii=False)
        creator_hash = db_item.get("creator_hash")
        async with get_session() as session:
            result = await session.execute(
                select(DouyinCreatorModel).where(
                    DouyinCreatorModel.creator_hash == creator_hash
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(DouyinCreatorModel(**db_item))
            else:
                for key, value in db_item.items():
                    setattr(existing, key, value)
            await session.commit()

    async def store_aweme_metric(self, metric: Dict):
        async with get_session() as session:
            result = await session.execute(
                select(DouyinAwemeMetricSnapshot).where(
                    DouyinAwemeMetricSnapshot.aweme_id == metric.get("aweme_id"),
                    DouyinAwemeMetricSnapshot.crawl_run_id == metric.get("crawl_run_id"),
                )
            )
            if result.scalar_one_or_none() is None:
                session.add(DouyinAwemeMetricSnapshot(**metric))
            await session.commit()

    async def store_creator_metric(self, metric: Dict):
        async with get_session() as session:
            result = await session.execute(
                select(DouyinCreatorMetricSnapshot).where(
                    DouyinCreatorMetricSnapshot.creator_hash == metric.get("creator_hash"),
                    DouyinCreatorMetricSnapshot.crawl_run_id == metric.get("crawl_run_id"),
                )
            )
            if result.scalar_one_or_none() is None:
                session.add(DouyinCreatorMetricSnapshot(**metric))
            await session.commit()

    async def store_topic(self, topic: Dict):
        db_item = dict(topic)
        if isinstance(db_item.get("raw_payload"), dict):
            db_item["raw_payload"] = json.dumps(db_item["raw_payload"], ensure_ascii=False)
        async with get_session() as session:
            result = await session.execute(
                select(DouyinTopic).where(DouyinTopic.topic_id == db_item.get("topic_id"))
            )
            existing = result.scalars().first()
            if existing is None:
                session.add(DouyinTopic(**db_item))
            else:
                for key, value in db_item.items():
                    setattr(existing, key, value)
            await session.commit()

    async def store_transcript(self, transcript: Dict):
        db_item = dict(transcript)
        if isinstance(db_item.get("segments"), list):
            db_item["segments"] = json.dumps(db_item["segments"], ensure_ascii=False)
        async with get_session() as session:
            result = await session.execute(
                select(DouyinTranscript).where(
                    DouyinTranscript.aweme_id == db_item.get("aweme_id")
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(DouyinTranscript(**db_item))
            else:
                for key, value in db_item.items():
                    setattr(existing, key, value)
            await session.commit()


class DouyinJsonStoreImplement(AbstractStore):
    def __init__(self):
        self.file_writer = AsyncFileWriter(
            crawler_type=crawler_type_var.get(),
            platform="douyin"
        )

    async def store_content(self, content_item: Dict):
        """
        content JSON storage implementation
        Args:
            content_item:

        Returns:

        """
        await self.file_writer.write_single_item_to_json(
            item=content_item,
            item_type="contents"
        )

    async def store_comment(self, comment_item: Dict):
        """
        comment JSON storage implementation
        Args:
            comment_item:

        Returns:

        """
        await self.file_writer.write_single_item_to_json(
            item=comment_item,
            item_type="comments"
        )

    async def store_creator(self, creator: Dict):
        """
        creator JSON storage implementation
        Args:
            creator:

        Returns:

        """
        await self.file_writer.write_single_item_to_json(
            item=creator,
            item_type="creators"
        )



class DouyinJsonlStoreImplement(AbstractStore):
    def __init__(self):
        self.file_writer = DouyinJsonlEventWriter(
            crawler_type=crawler_type_var.get(),
            platform="douyin"
        )

    async def store_content(self, content_item: Dict):
        await self.file_writer.write_to_jsonl(
            item=content_item,
            item_type="contents"
        )

    async def store_comment(self, comment_item: Dict):
        await self.file_writer.write_to_jsonl(
            item=comment_item,
            item_type="comments"
        )

    async def store_creator(self, creator: Dict):
        await self.file_writer.write_to_jsonl(
            item=creator,
            item_type="creators"
        )

    async def store_aweme_metric(self, metric: Dict):
        await self.file_writer.write_to_jsonl(metric, item_type="aweme_metrics")

    async def store_creator_metric(self, metric: Dict):
        await self.file_writer.write_to_jsonl(metric, item_type="creator_metrics")

    async def store_topic(self, topic: Dict):
        await self.file_writer.write_to_jsonl(topic, item_type="topics")

    async def store_transcript(self, transcript: Dict):
        await self.file_writer.write_to_jsonl(transcript, item_type="transcripts")


class DouyinSqliteStoreImplement(DouyinDbStoreImplement):
    pass


class DouyinMongoStoreImplement(AbstractStore):
    """Douyin MongoDB storage implementation"""

    def __init__(self):
        self.mongo_store = MongoDBStoreBase(collection_prefix="douyin")

    async def store_content(self, content_item: Dict):
        """
        Store video content to MongoDB
        Args:
            content_item: Video content data
        """
        aweme_id = content_item.get("aweme_id")
        if not aweme_id:
            return

        await self.mongo_store.save_or_update(
            collection_suffix="contents",
            query={"aweme_id": aweme_id},
            data=content_item
        )
        utils.logger.info(f"[DouyinMongoStoreImplement.store_content] Saved aweme {aweme_id} to MongoDB")

    async def store_comment(self, comment_item: Dict):
        """
        Store comment to MongoDB
        Args:
            comment_item: Comment data
        """
        comment_id = comment_item.get("comment_id")
        if not comment_id:
            return

        await self.mongo_store.save_or_update(
            collection_suffix="comments",
            query={"comment_id": comment_id},
            data=comment_item
        )
        utils.logger.info(f"[DouyinMongoStoreImplement.store_comment] Saved comment {comment_id} to MongoDB")

    async def store_creator(self, creator_item: Dict):
        # 教学版：创作者个人资料不再落库
        pass


class DouyinExcelStoreImplement:
    """Douyin Excel storage implementation - Global singleton"""

    def __new__(cls, *args, **kwargs):
        from store.excel_store_base import ExcelStoreBase
        return ExcelStoreBase.get_instance(
            platform="douyin",
            crawler_type=crawler_type_var.get()
        )
