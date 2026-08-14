# FlowLens 1.3.0 发布说明

FlowLens 1.3.0 为远程网站模式增加第一版账号系统。普通用户不开放自助注册，由本机初始化的管理员在管理后台创建；用户首次使用一次性临时密码登录后，必须设置正式密码。

## 本版能力

- Argon2id 密码哈希、服务端 Cookie 会话、CSRF 与 Origin 校验。
- 12 小时空闲会话、7 天绝对会话和登录失败限流。
- 管理员创建普通用户、调整配额、重置临时密码、撤销会话、暂停和恢复用户。
- 用户连接多个抖音账号，每个连接独占一个 Chrome Profile。
- 新建任务时显式选择抖音连接；任务、内容、媒体和定时计划可按连接筛选。
- 管理员自己的采集工作区与普通用户业务数据严格隔离。
- 远程网站 WebSocket 使用网站会话认证；Worker 继续使用独立 Ed25519 设备认证。
- 旧远程归属数据迁移为不可直接登录的暂停占位账号，不重写原有 `user_id`。

## 兼容边界

`FLOWLENS_REMOTE_WORKER=false` 时仍为免登录本机模式，已有本机采集、媒体、任务及其他平台行为不变。远程账号系统不会迁移或绑定已有共享 `browser_data`，每个远程抖音连接都创建新的隔离 Profile。

## 升级步骤

1. 安装新依赖并构建 WebUI：`uv sync`、`cd webui && npm ci && npm run build`。
2. 配置 `FLOWLENS_PUBLIC_ORIGIN`、`FLOWLENS_COOKIE_SECURE` 和高熵 `FLOWLENS_AUTH_HASH_KEY`。
3. 在服务端运行 `python -m tools.create_admin`，保存只显示一次的临时密码。
4. 启动 API，通过 `/#/login` 登录并完成首次改密。
5. 在 `/#/admin/users` 创建普通用户，再执行双用户、多账号和权限隔离冒烟测试。

生产环境必须使用 HTTPS，并将 `FLOWLENS_COOKIE_SECURE` 设为 `true`。系统不提供网页管理员初始化、公众注册、忘记密码、管理员代用户采集或验证码绕过。
