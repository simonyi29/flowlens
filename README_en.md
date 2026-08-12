# FlowLens

FlowLens is a privacy-aware public short-video data collection and research platform maintained by [simonyi29](https://github.com/simonyi29). Its primary enhancement track is Douyin: keyword discovery with full-detail hydration, real topic pages, public creator metrics, interaction snapshots, visible primary/reply comments with resumable checkpoints, native captions, and optional local `faster-whisper` ASR.

FlowLens is derived from [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler). The upstream copyright notices, contribution history, and `NON-COMMERCIAL LEARNING LICENSE 1.1` remain in effect. This project is for non-commercial learning and research only.

## Install

```shell
git clone https://github.com/simonyi29/flowlens.git
cd flowlens
uv sync
uv run playwright install chromium
```

Optional local ASR:

```shell
uv sync --extra asr
```

## Douyin examples

```shell
uv run main.py --platform dy --type search --keywords "AI" \
  --save_data_option jsonl --get_comment true --get_sub_comment true \
  --max_comments_count_singlenotes 0

uv run main.py --platform dy --type topic \
  --topics "AI,https://www.douyin.com/challenge/123456" \
  --save_data_option sqlite
```

`0` means every primary and reply comment exposed by the API. Native captions are checked first; when unavailable, optional local ASR is used. Temporary ASR media is removed by default.

## WebUI

```shell
uv run uvicorn api.main:app --port 8080 --reload
cd webui
npm install
npm run dev
```

Open `http://localhost:5173`.

## Data

Douyin JSONL event streams are written under `data/douyin/`. SQLite keeps current values plus metric snapshot tables. Output-independent resume state is stored in `data/douyin/crawl_state.sqlite`.

Proxy use is disabled by default. FlowLens retains the existing static-proxy option but does not add a dynamic proxy pool.

Repository: [github.com/simonyi29/flowlens](https://github.com/simonyi29/flowlens)
