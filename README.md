# 流镜 FlowLens

> 面向学习、研究与内容分析的短视频公开数据采集平台。

[![Repository](https://img.shields.io/badge/GitHub-simonyi29%2Fflowlens-181717?logo=github)](https://github.com/simonyi29/flowlens)
[![License](https://img.shields.io/badge/license-Non--Commercial-orange)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.11-3776AB?logo=python)](pyproject.toml)

FlowLens（流镜）以抖音公开数据采集为主要增强方向，支持关键词、真实话题页、指定视频、指定账号、互动指标历史、完整可见评论、原生字幕和本地 ASR，同时保留上游项目已有的多平台能力。

## 项目定位

- 关键词搜索结果逐条补取完整视频详情。
- 真实话题页采集，不把话题静默降级成关键词搜索。
- 采集视频文案、话题、音乐、媒体信息和公开互动指标。
- 采集脱敏后的账号公开简介、认证和统计指标。
- 采集接口可见的一级、二级评论，并支持断点续跑。
- 优先解析平台原生字幕，无字幕时可使用本地 `faster-whisper`。
- 支持 JSONL 追加事件流和 SQLite 最新值/历史快照。
- 提供 CLI、FastAPI 和 WebUI 配置界面。
- 原始响应默认关闭；开启后递归移除可识别个人的信息。
- 默认关闭代理，仅保留项目已有的静态代理配置。
- FlowLens 1.1 增加持久任务中心、正式媒体下载、增量采集、定时计划、内容库和系统健康检查。
- FlowLens 1.3 增加管理员创建用户、服务端会话、首次强制改密、多抖音账号与独立 Chrome Profile；远程 Worker、结构化同步和授权媒体中继继续保留。

远程模式默认关闭，部署、网站鉴权边界、Worker 注册和安全注意事项见 [FlowLens 远程 Worker 部署指南](docs/remote-worker.md)。

## FlowLens 1.1

正式媒体下载默认关闭，与 ASR 临时媒体完全独立。开启后默认最多下载 15 个作品、单任务最多新增 5GB、媒体库上限 20GB，并在磁盘剩余不足 10GB 时暂停。视频优先使用最高可用 H.264，支持多 CDN 回退、`.part` 续传、SHA-256 和可选 `ffprobe` 校验。

```shell
uv run main.py --platform dy --type search --keywords "人工智能" \
  --crawler_max_notes_count 30 --download_media true \
  --max_media_downloads 5 --max_media_total_bytes 5368709120
```

API 启动后可使用：

- `/api/tasks`：持久任务历史、暂停、恢复、取消和失败重试。
- `/api/media`：媒体清单、本地 Range 播放和确认删除。
- `/api/schedules`：账号/话题的 once、hourly、daily 计划。
- `/api/library`：内容分页、详情、评论树、字幕、指标趋势、搜索与导出。
- `/api/system/health`：CDP、ASR、ffprobe、FTS5 和媒体目录检查。

任务和计划状态固定保存到 `data/flowlens/tasks.sqlite`。任务配置快照不会保存 Cookie 或带凭据的静态代理地址。定时器仅在 FlowLens API/WebUI 进程运行期间工作，错过多次时下次启动只补执行一次。

## 使用边界

本项目只可用于非商业学习和研究。使用者必须遵守目标平台服务条款、robots 协议、当地法律法规和仓库许可证，不得进行大规模采集、绕过访问控制、干扰平台运行或侵犯第三方权益。

FlowLens 是基于 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 演进的衍生项目。上游版权声明、贡献历史和 `NON-COMMERCIAL LEARNING LICENSE 1.1` 均予保留。FlowLens 的新增改动由 [simonyi29](https://github.com/simonyi29) 维护。

## 环境要求

- Python 3.11 或更高版本。
- Node.js 16 或更高版本（抖音签名及 WebUI 构建）。
- 最新版 Chrome。
- 推荐使用 `uv` 管理 Python 环境。

```shell
git clone https://github.com/simonyi29/flowlens.git
cd flowlens
uv sync
uv run playwright install chromium
```

如需本地 ASR：

```shell
uv sync --extra asr
```

## Chrome/CDP

项目默认连接已有 Chrome 会话。打开：

```text
chrome://inspect/#remote-debugging
```

允许远程调试，并确保浏览器中已经完成目标平台登录。若不使用 CDP，可在 `config/base_config.py` 中把 `ENABLE_CDP_MODE` 设为 `False`。

## 抖音运行示例

关键词搜索：

```shell
uv run main.py --platform dy --type search --keywords "人工智能" \
  --save_data_option jsonl --get_comment true --get_sub_comment true \
  --max_comments_count_singlenotes 0
```

真实话题页，支持名称、URL 或数字 ID：

```shell
uv run main.py --platform dy --type topic \
  --topics "人工智能,https://www.douyin.com/challenge/123456" \
  --save_data_option sqlite
```

指定视频或账号：

```shell
uv run main.py --platform dy --type detail --specified_id "https://www.douyin.com/video/123"
uv run main.py --platform dy --type creator --creator_id "https://www.douyin.com/user/xxx"
```

字幕与 ASR：

```shell
uv run main.py --platform dy --type search --keywords "人工智能" \
  --enable_native_subtitle true --enable_asr true \
  --asr_model small --asr_language zh
```

`--max_comments_count_singlenotes 0` 表示采集接口可见的全部一级和二级评论。页面展示评论数可能高于接口实际可见数量。

查看完整参数：

```shell
uv run main.py --help
```

## WebUI

开发模式：

```shell
# 终端 1
uv run uvicorn api.main:app --port 8080 --reload

# 终端 2
cd webui
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。

生产构建：

```shell
cd webui
npm ci
npm run build
cd ..
uv run uvicorn api.main:app --port 8080
```

浏览器打开 `http://localhost:8080`。

FlowLens 1.3 的 WebUI 使用 `HashRouter`。远程模式先进入 `/#/login`，账号由管理员在 `/#/admin/users` 创建；普通用户首次登录必须在 `/#/change-password` 设置正式密码。主要入口为：

- `/#/connect`：抖音账号连接与扫码状态。
- `/#/crawl/new`：关键词、话题、视频和账号三步采集向导。
- `/#/tasks`：任务中心、阶段进度和合法恢复操作。
- `/#/library`：作品、账号、话题、评论和字幕浏览与导出。
- `/#/media`：正式媒体状态、播放与安全删除。
- `/#/settings`：本机模式原有多平台工具与低频高级配置。
- `/#/admin/*`：仅在服务端能力声明允许时显示的管理员后台。

普通用户页面不显示 Worker、浏览器调试端口、Profile 路径或原始内部状态。前端先读取 `/api/system/capabilities`，再按本机/远程模式和角色决定可用入口。工作台数据由 `/api/dashboard/overview` 聚合，避免首页发起大量分散请求。

前端质量检查：

```shell
cd webui
npm run lint
npm run build
```

界面设计与实现说明见 [FlowLens WebUI 产品化设计](docs/webui-product-design.md)。

## 数据输出

抖音 JSONL 默认输出：

```text
data/douyin/contents.jsonl
data/douyin/creators.jsonl
data/douyin/comments.jsonl
data/douyin/topics.jsonl
data/douyin/transcripts.jsonl
data/douyin/aweme_metrics.jsonl
data/douyin/creator_metrics.jsonl
```

字幕 SRT：

```text
data/douyin/transcripts/{aweme_id}.srt
```

与用户输出格式无关的断点状态：

```text
data/douyin/crawl_state.sqlite
```

SQLite 使用版本化、幂等迁移，不删除既有数据；视频和账号指标快照使用独立表保存。

## 代理

默认不使用代理。在低并发、已登录的真实 Chrome/CDP 会话下先运行。确有网络需要时，可启用现有静态代理：

```shell
uv run main.py --platform dy --type search --keywords "人工智能" \
  --enable_ip_proxy true --ip_proxy_provider_name static \
  --static_proxy_url "http://user:password@host:port"
```

日志会隐藏 Cookie 和静态代理地址。项目不提供新的动态代理池。

## 测试

```shell
uv run pytest -q
cd webui && npm run build
```

部分上游集成测试需要本地 Redis、MongoDB 或真实代理服务。抖音网络测试使用 fixture，不依赖实时接口；真实站点仅建议人工、小流量冒烟验证。

## 维护与贡献

- 项目仓库：[simonyi29/flowlens](https://github.com/simonyi29/flowlens)
- 问题反馈：[GitHub Issues](https://github.com/simonyi29/flowlens/issues)
- 上游项目：[NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)

提交代码前请确保新增行为遵守隐私最小化、低并发和非商业用途边界，并为公共文件中的抖音专用逻辑保留 `platform == "dy"` 隔离。
