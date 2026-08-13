# FlowLens 1.2 远程网站与 Worker 部署指南

FlowLens 1.2 可以作为抓取 Worker 主动连接已有网站。网站用户在网页中扫描抓取机 Chrome 生成的抖音二维码，随后使用该用户独立的浏览器 Profile 创建采集任务。普通用户不需要也不会获得抓取机桌面、Cookie、CDP 地址或文件路径。

> 本功能仍受仓库非商业学习许可证约束。若要对外提供商业服务，必须先单独处理许可证、平台条款、隐私和数据合规问题。FlowLens 不绕过验证码、滑块或平台风险控制。

## 架构与信任边界

```text
用户浏览器 → 已有网站（现有登录会话） → FlowLens 控制 API
                                      ↕ TLS/WSS + Ed25519
                               FlowLens Worker → 127.0.0.1 CDP → Chrome
```

- FlowLens API 不应直接暴露给浏览器或公网。现有网站后端完成用户认证后，反向代理 `/api/flowlens/*`，并注入 `X-FlowLens-Proxy-Token` 和服务端取得的 `X-FlowLens-User-ID`。
- 不得从用户请求体转发 `user_id`。管理员接口还需由网站后端注入 `X-FlowLens-Role: admin`。
- Worker 只建立出站 HTTPS/WSS 连接，不开放 HTTP 或 CDP 入站端口。Chrome CDP 固定监听 `127.0.0.1`。
- `FLOWLENS_TRUSTED_PROXY_TOKEN` 只存在于网站后端和控制中心环境中，绝不能写进浏览器 JavaScript。WebUI 的 `VITE_FLOWLENS_*` 变量仅用于本机开发测试，不适用于生产。

## 控制中心配置

生成两个独立的高熵随机值，并配置：

```powershell
$env:FLOWLENS_REMOTE_WORKER='true'
$env:FLOWLENS_TRUSTED_PROXY_TOKEN='<至少 32 字节随机值>'
$env:FLOWLENS_TENANT_HASH_KEY='<另一个至少 32 字节随机值>'
python -m api.main
```

远程模式默认关闭。关闭 `FLOWLENS_REMOTE_WORKER` 即可回滚到 1.1 本机模式；本地 Profile、任务和已同步数据不会被删除。

网站后端需要代理以下路径：

- 用户 API：`/api/flowlens/*`
- 用户事件：`/api/flowlens/events`
- Worker 注册：`/internal/flowlens/workers/register`
- Worker 控制通道：`/internal/flowlens/workers/connect`
- Worker 媒体通道：`/internal/flowlens/workers/media/*`

所有通道必须使用 TLS。反向代理应关闭二维码响应缓存，并避免记录请求头、WebSocket 消息正文和媒体内容。二维码响应自带 `no-store`、`private`、`nosniff` 头，只在控制进程内存中保存最多 180 秒。

## 注册并运行 Worker

管理员在“Worker 管理”页面生成一次性注册码。注册码有效期 10 分钟且只能消费一次。在抓取机执行：

```powershell
python -m api.worker register `
  --control-url https://control.example.com `
  --enrollment-code '<一次性注册码>' `
  --name worker-01

python -m api.worker run
```

Windows 抓取机也可以使用带自动重启的 `tools/start_flowlens_worker.ps1`：

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_flowlens_worker.ps1
```

首次注册时会在 `data/flowlens/worker/identity.pem` 生成 Ed25519 私钥，并在 `worker.json` 保存非敏感设备配置。私钥只应允许 Worker 的系统账号读取。这两个文件均位于已被 `.gitignore` 排除的 `data/` 目录。

生产环境可用 Windows 任务计划或服务管理器启动 `python -m api.worker run`。Worker 会指数退避重连；控制中心 45 秒收不到心跳即可在网站侧判定离线。

## 登录与采集流程

1. 用户在网站的“抖音账号”页点击“连接抖音账号”；普通用户只看到友好的“执行设备”名称，不需要选择或理解 Worker。
2. 控制中心生成不可预测的连接、登录会话和 Profile 标识，下发幂等命令。
3. Worker 获取唯一抖音浏览器槽，以独立 Profile 启动 Chrome，只截取登录二维码区域并通过认证通道上传。
4. 用户在网站扫描二维码。登录成功后二维码立即从内存删除，网站只保存脱敏昵称和不可逆账号 hash。
5. 用户选择已连接账号创建关键词、真实话题、指定视频或指定账号任务。
6. Worker 使用同一个 Profile 执行任务。Cookie 只从浏览器上下文同步到 Worker 进程内存，不进入命令、CLI、SQLite、日志或网站数据库。
7. 作品、脱敏账号、话题、评论、字幕、指标和媒体元数据进入本地 outbox；网站 ACK 前保留，断线重连后按事件 ID 幂等重发。

当前 Worker 一次只运行一个需要 Chrome/CDP 的抖音登录或网络采集操作。下载和 ASR 仍沿用 1.1 的有界后台规则。

## 媒体播放

正式媒体默认留在 `data/douyin/media`。网站请求媒体时先验证 `user_id + asset_id` 所有权，再建立 30 秒有效的内存中继会话。Worker 通过独立的签名认证 WebSocket 发送 1 MB 二进制块；控制心跳不会被视频流阻塞。

Worker 只会读取本地任务库中已登记、解析后仍位于媒体根目录下的具体文件。支持标准单段 `Range`、`206`、`Content-Range` 和播放中断背压；每个 Worker 最多两个并发媒体流。不生成永久公开 URL。

## 数据和隐私

- 中央结果以 `user_id`、连接、任务和 Worker 归属保存，事件 ID 唯一，重复上报不会重复插入。
- 同步前递归删除 Cookie、Token、授权头、原始 UID、`sec_uid`、CDP、代理凭据、签名参数和本地路径。
- 原始响应即使在 Worker 本地启用，也不会通过远程同步通道上传。
- 每个连接的 Profile 位于 `data/flowlens/browser/dy/{tenant_hash}/{profile_id}/profile`；标识必须满足固定格式，删除前再次解析并确认没有越过专用根目录。
- 断开连接会拒绝仍有未完成任务的账号，随后由 Worker 删除具体 Profile；历史采集结果保留。

## 网站集成约定

本仓库内置的远程页面是网站用户端参考实现。正式接入现有网站时，应复用网站用户会话，并从服务端注入身份头。浏览器端不能自行生成这些身份头；`X-FlowLens-Role: admin` 只有同时通过受信代理令牌校验时才生效。关键接口包括：

- `POST /api/flowlens/douyin/login-sessions`
- `GET /api/flowlens/douyin/login-sessions/{id}/qr`
- `GET /api/flowlens/douyin/connections`
- `POST /api/flowlens/crawl-runs`
- `POST /api/flowlens/crawl-runs/{id}/{pause|resume|cancel|retry-failed}`
- `GET /api/flowlens/results/{entity_type}`
- `GET /api/flowlens/results/aweme/{aweme_id}/detail`
- `GET /api/flowlens/media/{asset_id}/stream`

完整 OpenAPI 可在控制中心 `/docs` 查看。生产网站必须对每个查询和操作保持服务端所有权校验，不能依赖前端隐藏按钮。

## 故障处理

- `worker is offline`：确认 Worker 进程、TLS、DNS 和反向代理 WebSocket 升级配置。
- `captcha_required` / `risk_controlled`：任务和检查点保留，由管理员通过既有运维渠道进入抓取机完成验证，再从网站继续；不要尝试自动绕过。
- 二维码过期：点击刷新，旧二维码立即失效。
- `worker did not open media stream`：确认资产尚在 Worker、本地媒体目录可读且 Worker 没有达到两个并发流上限。
- 同步积压：恢复网络后 Worker 会重发未 ACK 事件；网站按事件 ID 幂等处理。

发布前至少使用两个测试用户验证二维码、连接、任务、结果和媒体的交叉访问均返回 403/404，并检查中央数据库及日志中不存在 Cookie、原始 UID、二维码或 CDP 信息。
