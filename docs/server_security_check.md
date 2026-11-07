# LivaChat Server 上线前安全检查

- 检查日期：2026-11-02
- 检查对象：`services/` 下的 chat server（gateway / session / BFF / media / recording / agent_relay / shared）及 `infra/` 部署配置
- 明确排除：`tools/lvdata-mcp`（按需求不检查）；客户端 App 仅在与服务端凭据传输直接相关时提及
- 方法：第一方代码审计 + 沙箱运行探针（临时 SQLite/临时目录，未接触真实数据与真实密钥）+ OSV/PyPI 公开漏洞库核对

## 1. 结论

**当前不具备公网多用户上线条件。** 最严重的问题不是单个三方 CVE，而是**认证与租户隔离缺失**：

1. 全服务只支持一个共享的 `dev_token`，没有用户/设备身份；`auth.mode=jwt` 未实现，开启即全部拒绝。仓库内 K8s 配置仍显式使用 `mode: dev`。
2. BFF 的 `device_id` 完全由调用方 query 参数决定，且可省略；省略时 timeline/search/graph 默认返回**所有设备**的数据，`/overview` 更是设计上忽略 `device_id`。沙箱实测：持有同一 token 的设备 A 可读取设备 B 的时间线、搜索结果、媒体文件。

Pillow 与 certifi 已在本次检查后升级并复测：Pillow 12.3.0 → 13.0.0（OSV 0 命中），certifi 2026.7.22 → 2026.10.23（OSV 0 命中）；全量测试 1871 passed / 1 skipped，Pillow 专项 183 passed。详见 §6.1。认证与 BFF 隔离问题仍未修复。

## 2. 风险分级

| 级别 | 定义 |
| --- | --- |
| P0 | 上线阻断：可导致越权访问、远程代码执行或全量数据泄露，必须在公网开放前修复 |
| P1 | 高：可导致拒绝服务、成本滥用、凭据泄露或供应链风险，应在首发前修复 |
| P2 | 中/低：纵深防御、信息泄露、依赖卫生，可与首发并行处理 |

## 3. 严重发现摘要

| ID | 级别 | 发现 | 关键证据 |
| --- | --- | --- | --- |
| P0-01 | P0 | 认证只有共享 `dev_token`，无用户/设备身份绑定；JWT 未实现；K8s 配置仍为 dev 模式 | `services/gateway/auth.py:39-47`、`services/bff/common/deps.py:57-70`、`infra/kubernetes/configmap.yaml:15-16` |
| P0-02 | P0 | BFF 跨设备越权：可省略/伪造 `device_id` 读取全部设备的 timeline/search/graph/media，`/overview` 固定全设备合并；remove 在省略 `device_id` 时也可跨设备 | `bff/timeline/service.py:83-88,190-192`、`bff/search/service.py:110`、`bff/graph/service.py:95,343`、`bff/remove/service.py:33`、`bff/common/media.py:23-36` |
| P1-01 | P1 | WebSocket/会话未绑定设备：同一 `device_id` 可踢掉旧连接；任意设备可用别人的 `session_id` resume | `gateway/connection.py:429-434`、`session/manager.py:108`、`session/orchestrator.py:573-574` |
| P1-02 | P1 | 无速率限制、连接上限、配额或成本控制；可暴力试 token、刷 WS 连接、刷付费 ASR/LLM/TTS | 全仓无 rate-limit middleware；`/healthz_token` 与 WS 均无限流；单 worker/单副本 |
| P1-03 | P1 | token 固定在 URL query；摘要即凭据、可无限重放；Android SDK/MyLife HTTP 为 trust-all TLS；媒体响应 `Cache-Control: public` | `docs/server_auth.md §3.1/§3.3`、`apps/android/LivaChatSDK/.../ConnectionManager.kt:142-156`、`bff/common/media.py:20,36` |
| P1-04 | P1 | `server.max_frame_bytes` 仅定义未使用；2.2MB 帧被服务端接受；实际通常受 uvicorn 默认 16MB 限制 | `shared/config/models.py:26`；全仓无第二处引用；沙箱实测见 §7 |
| P1-05 | P1 | 构建与密钥卫生：dev 私钥入库、literal 内部 token 入库、无 `.dockerignore`、Dockerfile 复制整个 `configs/`、依赖与基础镜像未锁定 | `configs/dev/key.pem`（`git ls-files` 可见）、`configs/dev/server.yaml:277`、`infra/docker/server.Dockerfile:5,26,36` |
| P2-01 | P2 | `/webroot` 无鉴权且与 API 同源；dev/test 已开启，生产默认关闭 | `app/application.py:466-488`、`configs/dev/server.yaml:281-283` |
| P2-02 | P2 | 休眠依赖漏洞：PyJWT 29 条、Mako 2 条、opentelemetry-api 1 条；当前代码路径未触发 | `gateway/auth.py:47`（JWT 未实现）、Alembic 迁移、`tracing_enabled=false` |
| P2-03 | P2 | 信息泄露与容器/K8s 加固不足：`/readyz` 失败回显 DB 路径、无安全响应头、无 `securityContext`/`NetworkPolicy`、9090 Service 端口已声明但当前无监听 | `app/routes.py:50-51`、`infra/kubernetes/deployment.yaml:10`、`infra/kubernetes/service.yaml` |
| P2-04 | P2 | Pillow 已升级修复 CVE，但图片解码仍无显式像素/尺寸/超时上限 | `services/media/jpeg.py:9-39` |

> Pillow/certifi 的依赖漏洞已通过升级修复，见 §6.1；不再列为开放项。

## 4. 对外接口逐项核查

沙箱探针使用两个设备（A/B）和临时 SQLite；所有 token 均为假值。下表“实测”列为真实运行结果，不是静态推断。

| # | 接口 | 鉴权 | 实测结果 | 风险 |
| --- | --- | --- | --- | --- |
| 1 | `GET /healthz` | 无 | 200，`{"ok":true}`，无敏感信息 | 低 |
| 2 | `GET /readyz` | 无 | 200；依赖失败时返回 `error: {exc}`，可能带 DB 路径 | 低（信息泄露） |
| 3 | `GET /healthz_token` | `token` + `device_id` query | 缺 token 422；错误 token 401；合法 token 200；无速率限制 | 中（在线爆破/探测） |
| 4 | `WS /v1/ws/stream` | `token` + `device_id` query | 错误 token 被 `WebSocketDenialResponse` 拒绝；合法 token 可建立会话；2.2MB 帧被接受；设备 B 可用设备 A 的 `session_id` resume 成功 | **高** |
| 5 | `GET /api/mylife/overview` | token | 无 token 401；合法 token 200；`device_id` 被代码显式丢弃（`del device_id`），返回全设备合并日卡 | **高** |
| 6 | `GET /api/mylife/timeline` | token；`device_id` 可选 | 省略 `device_id` 返回 A+B 两个设备共 3 条事件，包含双方 `device_uuid`、`text`、`media_url` | **高** |
| 7 | `GET /api/mylife/graph` | token；`device_id` 可选 | 省略 `device_id` 返回 `stats.sessions=2`（两台设备） | **高** |
| 8 | `GET /api/mylife/graph/entity/{entity_id}` | token；`device_id` 可选 | 代码路径与 graph 相同：省略 `device_id` 时 `list_sessions(None)` 扫描全部设备 | **高** |
| 9 | `GET /api/mylife/search` | token；`device_id` 可选 | 省略 `device_id` 查询 `secret` 命中 A+B 两条私有文本；`q`/`types` 无长度上限 | **高** |
| 10 | `GET /api/mylife/media/{device}/{session}/{kind}/{filename}` | token | 合法 token 可读取设备 B 的音频（200，`B-media`）；路径穿越 `..%2F` 返回 404；UUID/文件名正则白名单有效 | **高**（跨设备） |
| 11 | `POST /api/mylife/events/remove` | token；`device_id` 可选 | 带 `device_id=A` 删除 B 的事件 → 200 `skipped: not_found`（隔离生效）；**省略 `device_id`** → 通过可见性检查并进入上游调用（沙箱因未配置上游返回 503 `removal_disabled`）。若生产配置了 `bff.lvdata_api_url`，即可跨设备删除 | **高**（启用时） |
| 12 | `POST /api/mylife/agent-jobs/cancel` | token；`device_id` 必填 | 沙箱未启用 relay → 503 `agent_relay_unavailable`；代码会校验 `job.device_id == principal.device_id` | 中 |
| 13 | `GET /webroot/*` | 无 | dev/test 配置开启时，无 token 读取 `index.html` 返回 200；`..%2F` 穿越返回 404。生产默认关闭，需显式开启 | 中（启用时） |
| 14 | `GET /docs`、`/redoc`、`/openapi.json`、`/metrics` | — | 均 404，已关闭，符合预期 | 无 |

补充说明：

- BFF 默认 `enabled=True`（`shared/config/models.py:274-288`），K8s configmap 未显式关闭 BFF，因此生产默认会挂载 `/api/mylife`。
- `bff.device_uuids` 默认空列表 = **不限制设备**；即使配置，它也是服务端全局白名单，不是“每个 token 只能看自己的设备”。
- `/overview` 的“全设备合并”是代码注释中的既定行为（E2），不是配置错误；对多用户部署必须重新设计。

## 5. 第一方代码高危面审计

### P0-01 认证与身份体系

**事实**：

- `Authenticator` 只实现 `mode=dev`：把 query 中的 `token` 与 `sha1(dev_token + "LivaChat")` 做常量时间比较，成功后返回固定的 `user_id="dev-user"` 和调用方自报的 `device_id`（`services/gateway/auth.py:39-47`）。
- `auth.mode=jwt` 分支直接 `raise AuthError("jwt mode is not implemented")`（同文件 :47），因此无法通过配置切换到正式身份体系。
- BFF 复用同一个 `Authenticator`；`get_principal` 明确注释“device_id 仍不校验，仅作请求级过滤”（`services/bff/common/deps.py:57-70`）。
- K8s 生产配置仍是 `auth.mode: dev`（`infra/kubernetes/configmap.yaml:15-16`），secret 示例中的 `APP_AUTH_TOKEN` 甚至沿用 `dev-livachat-token`（`infra/kubernetes/secret.example.yaml:8`）。
- `docs/server_auth.md §3.3` 已如实列出残余风险：摘要即凭据、可无限重放、不绑定 path/method/device、trust-all TLS 未收敛。

**影响**：任何拿到该 token 的人（App 用户、代理日志读者、抓包者）等价于拥有整个部署的所有设备权限；无法区分用户、无法封禁单个用户、无法审计“谁读了谁”。这不是“token 强度”问题，而是没有授权模型。

**验收标准**：token 中必须携带可验证的身份（用户/设备）并绑定 `device_id`；服务端从认证结果派生 `device_id`，不再信任 query；`dev` 模式在生产启动时直接拒绝；至少实现一种可轮换、可过期的正式凭据（JWT 或短期签名 token）。

### P0-02 BFF 跨设备数据暴露

**事实**：

- `timeline`：只有显式传了 `device_id` 或配置了全局白名单才加设备过滤；否则 `devices=None`，SQL 不过滤（`bff/timeline/service.py:190-192`）。
- `search`/`graph`/`entity`：总是 `store.list_sessions(device_id)`；`device_id=None` 即全部会话（`bff/search/service.py:110-111`、`bff/graph/service.py:95,343`）。
- `overview`：`del device_id`，设计上返回全设备合并日卡（`bff/timeline/service.py:83-88`）。
- `media`：路径中带 `device` UUID，但只校验 token，不校验该设备是否属于调用者；timeline 响应会直接返回 `device_uuid` 和 `media_url`，等于给出枚举入口（`bff/timeline/service.py:261,271-276`、`bff/common/store.py:469-488`）。
- `remove`：可见集 = `list_sessions(principal.device_id)`；`device_id` 省略时等于全部设备（`bff/remove/service.py:33`）。请求体最多 50 个 ref，格式校验充分，但授权前提不成立（`bff/common/schemas.py:174-192`）。

**沙箱实测**：同一 token 下，`/timeline` 无 `device_id` 返回两个设备的事件和双方私有文本；`/search?q=secret` 命中双方；`/graph` 返回 2 个 session；`/media/<设备B>/...` 返回 200 和设备 B 的媒体内容。

**影响**：多用户/多设备部署时，任意持有共享 token 的客户端可读取全部用户的时间线、搜索命中、媒体文件，并可在 remove 启用时跨设备删除视频事件。若部署是严格单用户、单设备，风险降级为“凭据共享”，但当前代码与默认配置并未强制单设备。

**验收标准**：所有 BFF 接口的 `device_id` 必须由认证身份派生，query 中的 `device_id` 只能作为“在授权范围内再筛选”，越权返回 403/404；`/overview` 必须按设备/用户聚合，或从公网 BFF 移除；`/media` 在返回文件前校验设备归属；`remove` 默认关闭，开启时同样走身份授权。

### P1-01 WebSocket 与会话未绑定设备

- `ConnectionManager.register` 以 `device_id` 为键；同一 `device_id` 新连接会立即 `_kick_old` 旧连接（`gateway/connection.py:429-434`）。持有 token 的人只要知道设备 UUID（BFF timeline 会直接返回），就能反复踢掉该设备。
- `SessionManager.start` 把客户端传入的 `resume_id` 直接当作 `session_id`（`session/manager.py:108`）；`DialogOrchestrator` 随后 `ownership.take(ctx.session_id, self)`（`session/orchestrator.py:573-574`），不校验该 `session_id` 是否属于当前 `device_id`。
- 沙箱实测：设备 A 建立会话得到 `sess_...`，设备 B 用同一 `session_id` 发起 `session.start`，服务端返回 `session.ready` 且 `returned_session_id` 与 A 完全相同。

**影响**：会话归属被接管，旧连接的 `session.ended` 会被抑制；agent relay/审计关联可能错乱。历史消息按 `device_id` 载入，因此不直接读取对方聊天历史，但会话隔离和可审计性已被破坏。

**验收标准**：resume 时必须校验 `session.device_id == principal.device_id`；`device_id` 必须来自认证身份；同一设备重连应通过带认证的“接管”流程，而不是任意新连接直接踢旧连接。

### P1-02 无速率限制、配额与成本控制

- 全仓没有 rate-limit 中间件；`/healthz_token`、WS 握手、BFF 查询都没有按 IP/token/device 的限流或失败锁定。
- WS 连接数无上限；`ConnectionHandler` 只有 256 条入/出队列（`gateway/connection.py:68-69`），没有全局连接配额。
- 每个合法 WS 会话会按需调用付费 ASR/LLM/TTS/Omni provider；没有 per-token/per-device 预算或并发限制。
- 部署是单 worker、K8s `replicas: 1`（`infra/kubernetes/deployment.yaml:10`），单连接洪泛即可拖垮全部服务。

**影响**：token 在线爆破、连接洪泛、以及“用共享 token 刷付费模型”造成的经济型 DoS。

**验收标准**：在 ingress 或应用层增加按 IP/token/device 的请求与连接限流；对 `/healthz_token` 增加失败计数与退避；为每个身份设置并发会话上限和 provider 预算/告警；连接数达到阈值时快速拒绝而不是排队。

### P1-03 凭据传输、重放与 TLS

- token 固定在 URL query（WS 与全部 BFF 接口）；服务端自建 access log 只记录 `path`（`app/access_log.py:22-26`），但 ingress/nginx 的默认访问日志通常记录完整 request line，包含 query。
- `docs/server_auth.md §3.3` 明确：摘要即凭据，拿到摘要可无限期重放，不绑定 path/method/device；没有 `exp`/nonce/时间窗。
- Android SDK WebSocket 客户端无条件 trust-all（`apps/android/LivaChatSDK/.../ConnectionManager.kt:142-156`）；Android MyLife OkHttp 同样 trust-all（`apps/android/LivaChatApp/.../MylifeModule.kt:73-90`）。iOS SDK 默认系统校验，但提供 `.allowSelfSigned` 选项。
- `/api/mylife/media` 返回 `Cache-Control: public, max-age=31536000, immutable`（`bff/common/media.py:20,36`）；媒体是用户私有内容，`public` 允许共享缓存保存带 token 的 URL。

**影响**：代理日志、抓包、共享缓存或终端被攻陷后，攻击者获得可无限重放的凭据；trust-all TLS 让中间人直接拿到 token 与媒体。

**验收标准**：HTTP 使用 `Authorization: Bearer`，WS 使用短期签名 token 或 `Sec-WebSocket-Protocol`，避免 query 凭据；token 带过期时间与受众/设备绑定，支持轮换与吊销；ingress 日志脱敏 query；客户端移除 trust-all（保留显式自签模式作为开发开关）；私有媒体改为 `Cache-Control: private`。

### P1-04 `max_frame_bytes` 未生效与输入边界

- `ServerConfig.max_frame_bytes` 默认 2MB（`shared/config/models.py:26`），但全仓除定义外无任何引用；`ConnectionHandler._reader` 直接 `ws.receive_text()`（`gateway/connection.py:227-247`），实际大小上限通常由 uvicorn/websockets 的默认 `ws_max_size`（16MB）决定。
- 沙箱实测：发送 2.2MB 文本帧后连接保持，服务端返回 `invalid JSON` 错误帧，证明 2MB 配置未被执行。
- 其他输入边界：`search.q`、`types`、`graph.entity_id`、`timeline.session_id` 无显式长度上限；`/media?format=m4a` 会为每个不同源文件启动 `ffmpeg`，无并发限制（`bff/common/media.py:48-68`）。

**影响**：大帧/大搜索串/大量 ffmpeg 子进程造成内存与 CPU 耗尽；与 Pillow 解码放大叠加。

**验收标准**：在 uvicorn 配置 `ws_max_size` 并在 reader 层按 `max_frame_bytes` 再次拒绝；为 query 参数设置长度/数量上限；为 ffmpeg 转码增加并发信号量与超时；对超大/畸形帧计数并断开。

### P1-05 构建与密钥卫生

- `git ls-files` 显示 `configs/dev/key.pem`、`configs/dev/server.yaml`、`configs/dev/lvdata_mcp.yaml` 已被版本控制。`key.pem` 是 CN=localhost 的自签开发私钥（`configs/dev/cert.pem` 同套），密码学价值低，但仍是私钥入库。
- `configs/dev/server.yaml:277` 的 `lvdata_api_token` 是 literal 16 字符（非环境变量占位符）；`configs/dev/lvdata_mcp.yaml` 内也有 literal token。`configs/test/server.yaml:269` 同样为 literal。
- 仓库没有 `.dockerignore`；`infra/docker/server.Dockerfile:36` 执行 `COPY configs/ ./configs/`，会复制 `configs/dev`（含私钥）以及本地存在的 `configs/test`（含真实域名证书私钥，文件权限当前为 0644）。builder 阶段 `COPY services/` 还会带入 `.venv`、缓存与测试目录。
- Dockerfile 使用可变的 `python:3.12-slim` 标签，`pip wheel` 只给下界、无 hash/lock；每次构建可能解析到不同版本。
- `infra/kubernetes/secret.example.yaml:8` 的示例 token 为 `dev-livachat-token`，若被直接复制会形成弱共享密钥。

**影响**：镜像层/构建上下文泄露开发私钥与 token；供应链不可复现，难以保证生产镜像不含已知漏洞版本。

**验收标准**：增加 `.dockerignore`（至少排除 `.env`、`configs/test`、`.venv`、缓存、测试）；Dockerfile 只复制生产配置的显式文件；把 `configs/dev/key.pem` 从仓库移除并改为本地生成；literal token 改为环境变量/Secret；基础镜像改为 digest 固定；依赖使用 lock/hash；CI 构建前跑 `pip-audit`/OSV-Scanner 与镜像扫描。

## 6. 第三方依赖漏洞（OSV 快照）

本地 root `.venv` 安装版本（2026-11-02，升级后）：

| 包 | 当前版本 | OSV 命中 | 严重度分布 | 修复版本 | 最新 | 当前可利用性 |
| --- | --- | --- | --- | --- | --- | --- |
| Pillow | 13.0.0 | 0 | — | — | 13.0.0 | 已修复；图片解码仍建议加像素/尺寸/超时上限（P2-04） |
| certifi | 2026.10.23 | 0 | — | — | 2026.10.23 | 已修复 |
| PyJWT | 2.13.0 | 29 | 1 Critical / 6 High / 8 Moderate / 14 Unknown | 2.14.0 / 2.15.0 | 2.15.1 | 休眠：`jwt` 模式未实现，服务端未 import |
| Mako | 1.4.1 | 2 | 1 Moderate / 1 Unknown | 1.4.2 | 1.4.3 | 休眠：仅 Alembic 迁移使用 |
| opentelemetry-api | 1.44.0 | 1 | 1 Moderate | 1.45.1 | 1.45.2 | 休眠：`tracing_enabled=false`，无 OTel 埋点 |
| mcp | 1.29.1 | 4 | 4 Moderate | 1.30.0 / 2.2.0 | 2.3.0 | 仅 lvdata-mcp 使用，按需求排除 |

### 6.1 已完成升级与验证

- Pillow 12.3.0 → 13.0.0；certifi 2026.7.22 → 2026.10.23。
- `tools/lvdata-mcp/uv.lock` 已同步升级到 certifi 2026.10.23 / Pillow 13.0.0；`services/.venv` 已删除，统一使用 root `.venv`。
- OSV 对 Pillow 13.0.0、certifi 2026.10.23 均无命中。
- 测试：升级前基线 1871 passed / 1 skipped；升级后全量 1871 passed / 1 skipped；Pillow 相关专项（media/omni/avdetector）183 passed；lvdata-mcp vision 21 passed；真实 JPEG resize/decode/data_url 冒烟通过；`pip check` 无冲突。
- 残留：图片解码仍建议增加显式 `MAX_IMAGE_PIXELS`、格式/尺寸白名单与解码超时（P2-04）。

代表性命中（仅保留未处理的休眠依赖）：

- PyJWT Critical/High：`GHSA-ffc3-869f-jxw9`（HS/RS 混淆绕过）、`GHSA-9j54-fg26-wv3r`（空 HMAC key 绕过）、`GHSA-pxh4-856f-4h89`（oct JWK 空 key 导致 token 伪造）。
- Mako / opentelemetry-api / mcp 的命中见上表；均不在当前外部暴露路径。

OSV 未命中（截至 2026-11-02 的本地版本，含升级后版本）：FastAPI 0.141.1、Starlette 1.6.0、Uvicorn 0.52.4、websockets 17.1、python-multipart 0.0.32、httpx 0.28.1、Pydantic 2.13.5、PyYAML 6.0.3、SQLAlchemy 2.0.52、redis 8.1.0、asyncpg 0.31.0、openai 3.7.0、Pillow 13.0.0、certifi 2026.10.23 等。**OSV 无命中不等于无漏洞**，发布前仍需 lockfile + CI 扫描。

## 7. 沙箱运行证据

探针脚本位于 `/tmp/livachat_sec_probe.py`（临时文件，不在仓库），使用 `TestClient` + 临时 SQLite + 两个设备 A/B + 假 token，未接触真实数据/密钥。关键结果：

```text
GET /healthz                                      -> 200
GET /readyz                                       -> 200
GET /docs, /openapi.json, /metrics                -> 404
GET /healthz_token (wrong token)                  -> 401
GET /healthz_token (valid token)                  -> 200
GET /api/mylife/overview (no token)               -> 401
GET /api/mylife/timeline?token=...                -> 200, devices=[A,B], texts=[A-private, B-private]
GET /api/mylife/timeline?token=...&device_id=B    -> 200, only B
GET /api/mylife/search?token=...&q=secret         -> 200, total=2 (A+B)
GET /api/mylife/graph?token=...                   -> 200, stats.sessions=2
GET /api/mylife/media/B/.../audio/...ogg?token=...-> 200, body="B-media"
GET /api/mylife/media/.../../../chat_server.db    -> 404（路径穿越被拦）
POST /api/mylife/events/remove?token=...&device_id=A (B ref) -> 200, skipped=not_found
POST /api/mylife/events/remove?token=...          (B ref)     -> 503 removal_disabled（已通过可见性检查）
WS /v1/ws/stream (wrong token)                    -> rejected: WebSocketDenialResponse
WS /v1/ws/stream (valid token) + session.start    -> session.ready
WS 发送 2.2MB 文本帧                               -> 连接保持，返回 "invalid JSON"（2MB 限制未生效）
WS 设备 B 用设备 A 的 session_id resume            -> session.ready，session_id 相同
GET /webroot/index.html (no token)                -> 200
GET /webroot/../chat_server.db                    -> 404
```

局限：该探针运行于依赖升级前，Pillow/certifi 升级后的回归结果见 §6.1；探针是进程内 TestClient，不经过真实 ingress/TLS；未对生产镜像、OS 包、原生 libopus 做漏洞扫描；K8s configmap 可能是示例配置，若生产配置不同需重新核对。

## 8. 已核查为安全或未见问题的面

以下项目经过代码审计（部分有运行时验证），未发现可利用问题，列出以说明检查覆盖面：

- **SQL 注入**：BFF 查询使用参数化 SQL；`_PRIO_SQL` 只由固定事件类型常量拼接；`events_by_refs`/`_fts_where` 的 `IN` 占位符按列表长度生成，未拼接用户输入。
- **路径穿越**：`LvdataStore.resolve_media` 对 `device`/`session` 做 UUID 正则、对 `filename` 做 `^[0-9a-f-]{8,64}\.(ogg|jpg|m4a)$` 正则，并对 `resolve()` 后的路径做前缀校验；实测 `..%2F` 返回 404。`/webroot` 的 StaticFiles 也拦截了穿越。
- **命令注入**：BFF 音频转码使用 `subprocess.run([...])` 参数列表，未使用 `shell=True`；输入文件名已被正则约束，源路径来自受控 store。未发现 `eval`/`exec`/`os.system` 或 `pickle`/不安全的 `yaml.load`（配置使用 `yaml.safe_load`）。
- **认证比较**：dev token 使用 `hmac.compare_digest`，避免时序侧信道；JWT 模式失败关闭。
- **错误暴露**：`/docs`、`/redoc`、`/openapi.json`、`/metrics` 已关闭；WS 无效帧返回通用错误。
- **容器权限**：运行时 Dockerfile 使用非 root 用户 `livachat`。
- **CORS**：未配置跨域中间件，浏览器默认无法读取跨域响应；当前无 Cookie 认证，CSRF 面较小。

## 9. 上线前修复清单

### P0（公网开放前必须完成）

1. **替换共享 dev token，建立身份与设备授权**
   - 生产启动时禁止 `auth.mode=dev`；实现并启用 JWT 或短期签名 token（含 `exp`、`sub`、`device_id`、受众）。
   - 服务端从认证结果派生 `device_id`，BFF 不再信任 query 中的 `device_id`；越权返回 403/404。
   - 更新 `infra/kubernetes/configmap.yaml` 与 `secret.example.yaml`，移除 `dev-livachat-token` 示例。
   - 验收：token A 访问设备 B 的 timeline/search/graph/media/remove 全部被拒绝；省略 `device_id` 不再返回全量。
2. **修复 BFF 设备隔离**
   - `overview` 按用户/设备聚合，或从公网 BFF 移除；`timeline/search/graph/entity/media/remove` 强制身份范围。
   - `/media` 返回前校验设备归属；`remove` 默认关闭，开启时同样走身份授权。
   - 验收：沙箱探针中的跨设备读取/删除用例全部失败（403/404）。
> Pillow/certifi 升级已完成，见 §6.1；不再列为 P0。图片解码上限作为 P2-04 继续跟踪。

### P1（首发前完成）

4. **WS/会话绑定**：resume 校验 session 所属设备；同一设备重连走认证接管；限制单设备连接数。
5. **限流与配额**：按 IP/token/device 限制 `/healthz_token`、WS 握手、BFF 请求；设置并发会话上限与 provider 预算/告警；对失败 token 尝试退避。
6. **凭据传输与 TLS**：HTTP 改 `Authorization`，WS 改短期签名/子协议；ingress 日志脱敏 query；客户端移除 trust-all（至少生产构建）；私有媒体改 `Cache-Control: private`。
7. **输入边界**：落实 `max_frame_bytes`（uvicorn `ws_max_size` + reader 复核）；限制 `q`/`types`/`entity_id`/`session_id` 长度；限制 ffmpeg 并发与超时。
8. **构建与密钥卫生**：新增 `.dockerignore`；只复制生产配置；移除 `configs/dev/key.pem` 与 literal token；基础镜像固定 digest；依赖 lock/hash；CI 运行 `pip-audit`/OSV-Scanner/Trivy。
9. **生产配置复核**：确认 K8s configmap 不是示例；生产 `auth.mode`、`bff.enabled`、`webroot.enabled`、`recording.enabled`、`bff.lvdata_api_url` 均显式设置并经过评审。

### P2（可与首发并行）

10. `/webroot` 增加鉴权或默认关闭；若必须公开，增加 CSP/安全响应头并限定内容类型。
11. 升级或移除休眠依赖：`PyJWT>=2.15.1`（实现 JWT 前必须）、`Mako>=1.4.3`、`opentelemetry-api>=1.45.1`；评估移除当前未使用的 `redis`/`asyncpg`/`sqlalchemy`/`alembic`（若确认无运行时引用）。
12. 加固：`/readyz` 失败信息脱敏；增加 `X-Content-Type-Options`/`Referrer-Policy` 等响应头；K8s 增加 `securityContext`（`runAsNonRoot`、`readOnlyRootFilesystem` 并给日志目录挂载可写卷、`allowPrivilegeEscalation: false`、drop capabilities）与 NetworkPolicy；确认 9090 无监听或加鉴权。
13. 建立 CI 安全门禁：依赖 lock + 漏洞扫描 + 镜像扫描 + SAST；每次发布保留扫描报告与升级记录。
14. 图片解码上限：Pillow 已升级修复 CVE，但仍建议设置 `Image.MAX_IMAGE_PIXELS`、格式/尺寸白名单与解码超时（P2-04）。

## 10. 未覆盖与限制

- `tools/lvdata-mcp` 未做安全审计；仅同步升级了 `uv.lock` 中的 certifi/Pillow 并跑 vision 兼容性测试，其 4 条 `mcp` 包公告不纳入本报告结论。
- 客户端 App 未做完整审计；仅确认 Android SDK/MyLife HTTP 为 trust-all，作为凭据传输风险的一部分。
- OS 包（`python:3.12-slim`、`libopus0`、`curl`）与原生库未做 CVE 扫描；发布前需用 Trivy/Grype 对最终镜像扫描。
- 依赖结论基于本地 root `.venv` 快照（2026-11-02，升级后）；`services/.venv` 已删除，`tools/lvdata-mcp/uv.lock` 已同步升级。生产 Dockerfile 仍未锁定版本，实际镜像版本可能不同，必须以最终 lockfile/镜像重新扫描。
- 未做真实网络环境下的渗透测试、模糊测试或 ingress/TLS 配置审计；沙箱探针不覆盖真实代理日志、CDN 缓存与云 IAM。
- K8s 清单可能是示例/历史配置；若生产使用不同配置，需按本报告接口清单重新核对 `auth.mode`、BFF、webroot 与 remove 开关。
