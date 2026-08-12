# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/config/dy_config.py
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

# Douyin platform configuration
PUBLISH_TIME_TYPE = 0

# Douyin enhanced collection. These options are intentionally scoped to Douyin
# so enabling them never changes another platform's behaviour.
DY_TOPICS = ""
DY_ENABLE_CREATOR_PROFILE = True
DY_FORCE_CREATOR_REFRESH = False
DY_CREATOR_REFRESH_INTERVAL_SEC = 24 * 60 * 60
DY_ENABLE_NATIVE_SUBTITLE = True
DY_ENABLE_ASR = True
DY_ASR_MODEL = "small"
DY_ASR_LANGUAGE = "zh"
DY_SAVE_RAW_PAYLOAD = False
DY_KEEP_MEDIA = False

# Permanent media library (independent from temporary ASR media).
DY_DOWNLOAD_MEDIA = False
DY_DOWNLOAD_VIDEO = True
DY_DOWNLOAD_IMAGES = True
DY_DOWNLOAD_COVER = True
DY_DOWNLOAD_MUSIC = False
DY_MEDIA_QUALITY = "best_h264"
DY_MAX_MEDIA_DOWNLOADS = 15
DY_MAX_MEDIA_TOTAL_BYTES = 5 * 1024 ** 3
DY_MEDIA_LIBRARY_MAX_BYTES = 20 * 1024 ** 3
DY_MIN_FREE_DISK_BYTES = 10 * 1024 ** 3
DY_SKIP_EXISTING_MEDIA = True
DY_VERIFY_MEDIA = True
DY_KEEP_ASR_SOURCE_MEDIA = False
DY_INCREMENTAL = False
DY_STOP_AFTER_EXISTING = 5
DY_REFRESH_EXISTING_METRICS = True
DY_REFRESH_EXISTING_COMMENTS = False

# Specify DY video URL list (supports multiple formats)
# Supported formats:
# 1. Full video URL: "https://www.douyin.com/video/7525538910311632128"
# 2. URL with modal_id: "https://www.douyin.com/user/xxx?modal_id=7525538910311632128"
# 3. The search page has modal_id: "https://www.douyin.com/root/search/python?modal_id=7525538910311632128"
# 4. Short link: "https://v.douyin.com/drIPtQ_WPWY/"
# 5. Pure video ID: "7280854932641664319"
DY_SPECIFIED_ID_LIST = [
    "https://www.douyin.com/video/7525538910311632128",
    "https://v.douyin.com/drIPtQ_WPWY/",
    "https://www.douyin.com/user/MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE?from_tab_name=main&modal_id=7525538910311632128",
    "7202432992642387233",
    # ........................
]

# Specify DY creator URL list (supports full URL or sec_user_id)
# Supported formats:
# 1. Complete creator homepage URL: "https://www.douyin.com/user/MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE?from_tab_name=main"
# 2. sec_user_id: "MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE"
DY_CREATOR_ID_LIST = [
    "https://www.douyin.com/user/MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE?from_tab_name=main",
    "MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE"
    # ........................
]
