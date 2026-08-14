# FlowLens 1.3 远程网站、账号与 Worker 部署指南

FlowLens 1.3 内置用户名、密码和服务端会话。普通用户由管理员创建，不开放公众注册；每位用户可以连接多个自己的抖音账号，每个连接使用独立 Chrome Profile。用户在网站扫码后，可以选择具体账号创建任务。Cookie、Profile、CDP 地址和 Worker 私钥始终留在抓取设备。

> 本功能仍受仓库非商业学习许可证约束。若要对外提供商业服务，必须另行处理许可证、平台条款、隐私和数据合规问题。FlowLens 不绕过验证码、滑块或平台风险控制。

## 信任边界

```text
用户浏览器 → FlowLens HTTPS 网站 → 服务端 Cookie 会话与 CSRF
                                ↕ TLS/WSS + Ed25519
                         FlowLens Worker → 127.0.0.1 CDP → Chrome
```

- 浏览器身份只来自 `HttpOnly` 的 `flowlens_session` Cookie。浏览器发送的用户 ID、角色和代理令牌一律不作为身份。
- `FLOWLENS_TRUSTED_HEADER_COMPAT` 默认关闭，只能用于私有后端迁移，不能在浏览器中启用。
- Worker 使用独立 Ed25519 注册和 challenge 签名，用户 Cookie 不进入 Worker 控制协议。
- Worker 只建立出站 HTTPS/WSS 连接。Chrome CDP 固定监听 `127.0.0.1`。
- 用户、连接、任务、结果、媒体和定时计划均按服务端会话中的 `user_id` 隔离。

## 控制中心配置

开发环境示例：

```powershell
$env:FLOWLENS_REMOTE_WORKER='true'
$env:FLOWLENS_PUBLIC_ORIGIN='http://127.0.0.1:8080'
$env:FLOWLENS_COOKIE_SECURE='false'
$env:FLOWLENS_AUTH_HASH_KEY='<至少 32 字节的高熵随机值>'
$env:FLOWLENS_TENANT_HASH_KEY='<另一个高熵随机值>'
python -m api.main
```

生产环境必须使用 HTTPS：

```text
FLOWLENS_PUBLIC_ORIGIN=https://flowlens.example.com
FLOWLENS_COOKIE_SECURE=true
FLOWLENS_SESSION_IDLE_SECONDS=43200
FLOWLENS_SESSION_ABSOLUTE_SECONDS=604800
FLOWLENS_TEMP_PASSWORD_SECONDS=86400
FLOWLENS_LOGIN_MAX_FAILURES=5
FLOWLENS_LOGIN_LOCK_SECONDS=900
FLOWLENS_AUTH_HASH_KEY=<高熵随机值>
```

未配置安全来源、哈希密钥或 HTTPS 安全 Cookie时，启动健康提示会明确列出问题。关闭 `FLOWLENS_REMOTE_WORKER` 即回到免登录的本机模式，本地 Profile、任务和数据不会删除。

## 初始化管理员

第一位管理员只能从服务器本机创建：

```powershell
python -m tools.create_admin
```

命令会询问管理员用户名和显示名称，并打印一次性临时密码。临时密码 24 小时有效且只显示一次。首次登录必须设置 12～128 位正式密码。

如果管理员忘记密码，在服务器本机执行：

```powershell
python -m tools.reset_admin_password --username <管理员用户名>
```

该命令只重置既有管理员，不创建第二个管理员，并撤销该管理员的全部网站会话。

## 创建普通用户

管理员首次改密后进入 `/#/admin/users`：

1. 点击“创建用户”。
2. 填写用户名、显示名称、抖音连接上限、任务上限和媒体配额。
3. 保存仅显示一次的临时密码并安全交给用户。
4. 用户从 `/#/login` 登录，并在 `/#/change-password` 设置正式密码。

管理员可以暂停、恢复、撤销会话或重新生成临时密码，但不能在网页创建管理员、查看用户业务数据或使用用户抖音连接。

## 注册并运行 Worker

管理员在“执行设备”页生成 10 分钟一次性注册码，在抓取机执行：

```powershell
python -m api.worker register `
  --control-url https://flowlens.example.com `
  --enrollment-code '<一次性注册码>' `
  --name worker-01

python -m api.worker run
```

Windows 可用 `tools/start_flowlens_worker.ps1` 自动重连。Ed25519 私钥位于 `data/flowlens/worker/identity.pem`，只允许 Worker 系统账户读取，并已被 `.gitignore` 排除。

## 多抖音账号

- 用户进入 `/#/connect`，可在配额内反复点击“连接新账号”。
- 控制中心自动选择在线 Worker，用户不提交 Worker ID、路径或端口。
- 每个连接一对一绑定独立 `profile_id` 和 Chrome Profile。
- 登录完成后 Profile 保留，Chrome 空闲关闭；任务启动时使用同一 Profile。
- 账号页支持备注、重新扫码和断开。断开只删除该连接的 Profile，历史结果保留。
- 新建任务和远程定时计划必须选择一个属于当前用户且状态为 `connected` 的账号。

## 会话与安全

- 密码使用 Argon2id；数据库不保存明文密码或明文会话令牌。
- Cookie 为 `HttpOnly`、`SameSite=Lax`，生产环境 `Secure=true`。
- 所有写操作校验 `Origin` 和会话绑定的 `X-CSRF-Token`。
- 空闲会话 12 小时、绝对会话 7 天；改密、暂停或撤销会话立即失效。
- 同一用户名或来源连续 5 次失败后锁定 15 分钟；未知用户名和错误密码响应一致。
- 登录尝试只保存带密钥不可逆哈希，审计日志禁止保存密码、Token、二维码、Cookie、Profile 路径和 CDP 地址。

## 用户暂停与恢复

暂停用户会立即撤销网站会话，向 Worker 下发任务暂停命令，并将未完成任务置为暂停。数据、媒体和 Chrome Profile保留。恢复后用户可以重新登录，但暂停任务不会自动恢复，需由用户手动继续。

## 二维码、数据与媒体

二维码只保存在控制进程内存，TTL 最多 180 秒，响应包含 `no-store`、`private`、`nosniff`；登录、取消或过期后删除。结构化数据通过 Worker outbox 幂等同步，原始响应不上传。

正式媒体默认留在 `data/douyin/media`。网站按 `user_id + asset_id` 验证所有权后建立短期 Range 中继，不生成永久公开 URL。每个 Worker 默认最多两个并发媒体流。

## 关键路由

- 登录：`/#/login`
- 首次改密：`/#/change-password`
- 管理员用户：`/#/admin/users`
- 抖音账号：`/#/connect`
- 新建任务：`/#/crawl/new`
- 任务中心：`/#/tasks`
- 定时计划：`/#/schedules`

认证 API：

```text
POST /api/auth/login
GET  /api/auth/me
POST /api/auth/change-password
POST /api/auth/logout
POST /api/auth/logout-all
```

管理员用户 API 以 `/api/admin/users` 为前缀；远程抖音、任务、结果和媒体 API 以 `/api/flowlens` 为前缀。完整 OpenAPI 位于 `/docs`。

## 发布检查

1. 使用 CLI 创建管理员，确认网页没有注册入口或注册 API。
2. 创建两个普通用户，分别完成首次改密。
3. 用户 A 连接账号 A1、A2，用户 B 连接 B1，确认三个 Profile 不同。
4. 交叉访问连接、二维码、任务、结果、媒体和计划必须返回 403/404。
5. 暂停用户，确认网站会话失效、任务暂停；恢复后任务不自动继续。
6. 检查浏览器构建产物中没有 `VITE_FLOWLENS_PROXY_TOKEN`、用户 ID 或角色 Header。
7. 检查中央数据库和日志中没有 Cookie、二维码、原始 UID、Profile 路径或 CDP 地址。
