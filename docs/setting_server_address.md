# 设置页「服务」区改造：服务器地址输入框 + 认证密钥 + 真实检测

> **状态（2026-11-03 当前实现）**：preset server / picker 已移除；Settings 仅保留地址/密钥输入、TLS 开关、检测服务与直接保存。点击「保存」直接落盘并进入重启提示，**没有中间确认框或保存校验弹窗**。
> 本文 §1 / §2 / §8 / §10 中关于 preset/picker、保存校验弹窗、四改方案的内容为历史记录；§3.4 / §3.5 / §4 / §5 已按 §0 更新，如仍有残留旧描述，以 §0 / 当前代码为准。
> 历史状态：2026-10-13 初版实施；2026-10-14 四改方案实施，现均已被 2026-11-03 实现取代。
> 方案拍板记录（Q1–Q5）见 §7；历史视觉 mock 见 §7/§10（不入库）。
> 本文同名覆盖了 2026-10-10 的「IP/域名映射」文档，旧内容压缩归档于 §9——`ServerLinks.kt:39`、
> `AppModule.kt:74`、`docs/webroot_feature.md` 对本文档的交叉引用由 §9 承接。

## 0. 当前实现（2026-11-03）

| 项 | 当前实现 |
|----|----------|
| 模型 | `ServerConfig(address, token)`；`ServerOption` preset 类已删除；保留 `parseServerAddress` / `wsUrlFromAddress` / `httpBase` / `isValidAuthToken` / `isServerConfigComplete` / `UNSET_SERVER_WS_URL` 纯函数与占位常量 |
| 持久化 | DataStore `server_address` + `auth_token` + `tls_allow_self_signed`；`updateServerConfig(address, token, tlsAllowSelfSigned)` 原子写入 |
| Settings UI | 服务器地址 + 认证密钥输入；TLS 开关；检测服务；保存按钮。无 picker / 选择按钮 |
| 保存 | `dirty` 时显示「保存」；`canProbe=false` 时按钮置灰并显示 inline 校验文案；点击直接保存 address + token + TLS → `ServerSwitchDialog`（服务器配置已更新 / 重新打开 App 后生效 / 退出），无中间确认框 |
| TLS | 默认 `true`；Settings 草稿随服务器配置一起保存，重启进程后生效。SDK WS（`LivaChatConfig.allowSelfSigned`）、MyLife/Coil/ExoPlayer（`TlsOkHttpClientFactory`）、ServiceProbe（探测草稿值）、WebView（仅对已配置服务器同源放行）共用同一策略 |
| 检测 | `ServiceProbe.probe(address, token, deviceId, allowSelfSigned)`：`/healthz_token`，200/401/其它三映射；5s connect timeout |
| Manifest | Release `usesCleartextTraffic=false`；Debug manifest 允许明文，供本地 MockWebServer / E2E 与局域网调试 |
| 测试 | `TlsSettingsTest`、`ServiceProbeTest`、`ConnectionManagerTest`、`SettingsViewModelTest`、`ServerSwitchDialogTest` 等；`ServerOptionPickerTest` / `SaveValidationDialogTest` 已删除 |

> §2、§8、§10 为历史记录；§3、§4、§5、§7 已按当前实现更新，如与本节冲突，以本节为准。

## 1. 需求（2026-10-13，历史需求）

> 以下为初版需求；其中「选择」按钮 / picker 已在 2026-11-03 移除，当前实现见 §0。

1. 「服务器」变为输入框：用户输入 `ip:port` 或 `域名:port`；不带 `wss://` 前缀、不带 `/v1/xxx` 路径。
2. ~~右侧加「选择」按钮 → 弹出既有 server option~~（已废弃，2026-11-03 移除 picker）。
3. 「服务器」下新增「认证密钥」输入框：用户手工输入 auth token，替代代码硬编码。
4. 「检测服务」改为真实连接 server API 且带 token 校验（验证是否 ok）。
5. 以代码为据（不猜测）；分析与设计存档本文。
6. （2026-10-13 二改）**不做旧键映射**：升级/首装后地址与密钥均为空，由用户在设置页输入；
   地址或密钥为空时，**弹窗提示用户去设置**。

## 2. 历史现状与依据（2026-10-13，已被 §0 取代）

### 2.1 服务器选择与持久化

| 环节 | 现状 | 位置 |
|------|------|------|
| 模型 | `ServerOption(id, label, wsUrl)`；PUBLIC `wss://<server-host>:8080/v1/ws/stream`、LAN `wss://<lan-host>:8080/v1/ws/stream` | `domain/model/ServerOption.kt:7-19` |
| 持久化 | DataStore 存 **id 字符串**（键 `server_id`）；读取时 `fromId` 映射、非法回退 PUBLIC | `data/local/PreferencesDataSource.kt:62-69、78` |
| 进程装配 | 启动时同步读首值 → `LivaChatConfig(serverUrl = server.wsUrl, token = "dev-livachat-token", …)` | `di/AppModule.kt:40、45-47`；token 硬编码字面量在 **:47** |
| 切换 UI | 选择器选中 → 落盘 → 「必须重启」弹窗 → `exitProcess(0)` | `SettingsScreen.kt:92-100、156-185、257-323` |

### 2.2 地址与 token 的全部消费点（改动影响面）

| 消费点 | 现来源 | 位置 |
|--------|--------|------|
| WS 连接 | `config.serverUrl` + `config.token` 拼 query：`"$serverUrl?token=$token&device_id=$deviceId"`（无 URL 转义）；2026-10-14 起 `config.token` 在装配处已是 **wire 摘要**（`sha1_hex(secret+"LivaChat")`，docs/server_auth.md §4.1），SDK 不感知 | `LivaChatSDK/.../ConnectionManager.kt:137` |
| ~~mylifeUrl（时光机 WebView URL）~~ | **已删除**（2026-10-14，docs/server_auth.md §4.2）：无消费点的死代码（含明文 token 拼装），`AppModule` provider 与 `ConversationViewModel.timeMachineUrl` 一并移除 | — |
| markdown webroot 渲染基址 | `serverAddress` → `$scheme://$host:$port` | `ui/navigation/ServerLinks.kt:26-37、44-45` → `AppModule.kt:74-78` → `MainActivity` → `MarkdownContent` |
| mylife API base（`/api/mylife`） | 独立复刻同一解析规则 | `di/MylifeModule.kt:46-62` |
| mylife API/media 请求 token | `config.token` 逐个附 query | `data/remote/mylife/MylifeApi.kt:108、158`；`MylifeRepository.kt:386-389`（媒体 URL 附加） |

结论：所有链路都从 `LivaChatConfig.serverUrl`（完整 wss URL）与 `config.token` 派生。**只要装配层把「地址+token」转成同样形态，下游全部零改动**。

### 2.3 检测服务现状

- raw TCP `Socket().connect(host, port, 3000)`：只验端口可达，不验 TLS / HTTP 路由 / token（`SettingsViewModel.kt:93-117`）。
- UI 四态 Idle / Checking / Available / Unavailable（`SettingsUiState.kt:5-10`）。

### 2.4 服务端可用的 token 校验手段（关键调研）

| 手段 | 是否校验 token | 副作用 | 依据 | 结论 |
|------|----------------|--------|------|------|
| **REST 预检 `/healthz_token`（2026-10-13 新增，工作树待提交）** | ✅ 与握手逐条同语义：先比 wire token 摘要、后校验 `device_id`；成功 200 `{"ok": true}`，失败 401（detail 与握手字符串相同） | **无**（不建 WS、不 register、不触录音） | `services/app/routes.py:61-77`；测试 `services/tests/unit/app/test_healthz_token.py`；契约 `docs/server_design.md` §9.1 | **探测首选** |
| WS 握手 `/v1/ws/stream` | ✅ dev 模式要求 `token == sha1_hex(auth.dev_token + "LivaChat")`（2026-10-14 起，docs/server_auth.md），否则 accept 前 HTTP 401；`device_id` 另校验 UUID v4 | 同 device_id 踢旧连接 + 录音 acquire/detach（见下） | `services/gateway/endpoint.py:27-51`；`services/gateway/auth.py:25-47` | 有副作用，探测不用 |
| `/healthz` `/readyz` | ❌ 无鉴权 | 无 | `services/app/routes.py:16-21、24-58` | 只能验可达 |
| `/api/mylife`（BFF） | ✅ 经 `get_principal` 校验（Phase 2 已落地，与握手同语义；2026-10-14 起为 wire 摘要） | 无（只读查询） | `services/bff/common/deps.py:40-53` | 可用；预检已有专用端点 |

**WS 握手路径的副作用（为何探测不走 WS；供未来复用该链路时参考）**：

- 握手成功后端点执行 `conn_mgr.register(device_id, handler)`——**同 device_id 的旧连接被立即踢掉**（`services/gateway/connection.py:409-414`，`_kick_old`）。若探测带真实设备 id 且 App 正连着同一服务器，会把进行中的会话踢断。
- 握手时 `recording_service.acquire(device_id)`（`endpoint.py:63-71`）；连接关闭时 `sink.on_connection_closed() → RecordingSession.detach()`（`connection.py:190-193`；`recording/session.py:308-309`）——detach 会**关闭当前音频分段并把共享的按设备录音会话置 idle**（`session.py:193-207`）。探测连接早于真实连接关闭时，会干扰在跑的录音会话。
- ⇒ WS 握手因副作用不适合做探测；**`/healthz_token` 预检端点无任何副作用，探测直接用它 + 真实 device_id**（§3.3）。

## 3. 设计

### 3.1 数据模型与持久化（D1）

- 新增 DataStore 键：`server_address`（如 `<server-host>:8080`）、`auth_token`。
- 领域模型：`ServerConfig(address: String, token: String)`（读取/保存的原子对）。
- `ServerOption` 预设列表已于 2026-11-03 移除；当前只保留 `ServerConfig` 与纯函数（§0）。
- 读取口径（**2026-10-13 二改**）：`resolveServerAddress`——合法则用原文；缺失 / 非法一律**空串**；**不做旧 `server_id` 映射**（旧键原样留存、不读取）；`auth_token` 缺失 → 空串。即升级 / 首装后**地址与密钥均为空**，用户须在设置页输入后保存；未配置期由对话页引导弹窗提示（§3.4）。
- 未配置装配兜底：装配层（`AppModule`）对空 / 非法地址使用 `UNSET_SERVER_WS_URL`（`wss://0.0.0.0:1/v1/ws/stream`）——语法合法但不可路由，保证 `MylifeModule.provideMylifeApiBase`（`MylifeModule.kt:55` 对无 host 的 serverUrl 直接抛 `IllegalStateException`）等解析路径不崩进程；真实连接由对话页门控拦截。

### 3.2 地址解析 / 校验 / 拼装（D2 / D3，单点）

- 新增纯函数（实现期位置调整：放 `domain/model/ServerOption.kt`——数据层读取校验 / 装配层 / ui 三处共用，避免 data→ui 依赖；`serverAddress(wsUrl)` 解析仍留在 `ServerLinks.kt` 不动）：
  - `parseServerAddress(input): String?`——严格校验 `host:port`：host 为 IPv4 或域名字符集（`[A-Za-z0-9.-]`、非空），端口 1–65535；拒绝含 scheme / 路径 / 空白 / 端口缺省。
  - `wsUrlFromAddress(addr) = "wss://$addr/v1/ws/stream"`；`httpBase(addr) = "https://$addr"`。
  - `isServerConfigComplete(address, token)`：地址与密钥齐备判定——对话页启动门控与设置页 `canProbe` 共用同一条规则。
  - `UNSET_SERVER_WS_URL`：未配置装配占位（不可路由；§3.1 兜底）。
- scheme 固定 wss / https；自签策略由 Settings TLS 开关控制（默认 true），见 §0。
- 装配层（`AppModule`）用 `wsUrlFromAddress` 造 `LivaChatConfig.serverUrl`（仍为完整 wss URL）+ 读 `auth_token` 造 token ⇒ §2.2 全部下游零改动。
- token 校验（Q4 拍板）：仅允许 `[A-Za-z0-9._-]`（大小写字母 / 数字 / `-` / `_` / `.`），其余字符非法；空串非法（Q3 默认空）。SDK 不改（拼接转义问题以输入侧校验规避，§6）。**2026-10-14 备注**：wire 传输已是固定 hex 摘要（明文 secret 不入 URL），此字符集规则**保留不变**（docs/server_auth.md §4.1/§8）。
- 地址或 token 校验失败 → 「检测」置灰；`dirty` 时「保存」出现但置灰，并在保存区显示 inline `validationMessage`（地址/密钥非法时不再有红框或通用校验弹窗）。TLS 开关与地址/密钥一起保存（默认 true；修改后需重启生效）。

### 3.3 检测服务 = REST 预检探测（D4）

- 端点（服务端 2026-10-13 已实施，工作树待提交）：`GET https://<addr>/healthz_token?token=…&device_id=…`（`token` 为 wire 摘要——`ServiceProbe` 实现内由输入框草稿 secret 派生，`ServiceProbe.kt`）
  - 200 `{"ok": true}` → 可用；401（token 不符 / device_id 非法）→ 认证失败；连接失败 / 超时 / 其它状态码 → 不可用。
  - device_id 用 **App 自己持久化的 UUID v4**（既有 `AppModule.getOrCreateDeviceId`）——REST 预检无副作用，无需合成 id；device_id 类 401 正常不会出现（App 自身产物，同为 v4）。
- 客户端实现：`ServiceProbe`（interface + OkHttp 实现，`data/remote/ServiceProbe.kt`）：
  - 签名：`probe(address, token, deviceId, allowSelfSigned)`。
  - 通过 `TlsOkHttpClientFactory.create(allowSelfSigned)` 选择系统校验/自签 client，再浅克隆 5s connect timeout；一次 GET，无 WebSocket。
  - URL 用 `HttpUrl.Builder` 构造（**顺带解决探测侧 token 的 URL 转义**）。
  - 映射：`200 → Available`；`401 → AuthFailed`；连接异常 / 超时 / 其它状态码 → `Unreachable`。
- 状态：`ServiceStatus` 增加 `AuthFailed`（区分「地址可达但 token 错」与「不可达」）。UI 文案：可用（绿）/ 认证失败（红）/ 不可用（红）/ 检测中（转圈）。
- 探测输入 = **当前输入框草稿值 + 当前 TLS 草稿**（先测后存）；结果落地沿用现有守卫（快照 `(address, token, tlsAllowSelfSigned)` 未再变化才写回）；并发沿用 `Checking` 早退。
- 备选与未采纳（2026-10-13 前评估，随端点落地废弃）：WS 握手探测——副作用见 §2.4；合成探测 device_id（固定 UUID）——REST 预检无副作用后不再需要；服务端 `probe=1` 分支——`/healthz_token` 已提供专门预检，无需再动握手路由。

### 3.4 保存语义与交互（D5）

- 草稿模型：输入框编辑只改 UI 草稿，不动 DataStore。
- 「保存」按钮：地址、密钥或 TLS 与已存基线不同时出现；`canProbe=false` 时按钮置灰并显示 inline 校验文案；点击直接一次性持久化 address + token + TLS → 弹 `ServerSwitchDialog`（标题「服务器配置已更新」、正文「重新打开 App 后生效。」）→ 退出。**没有中间确认框或保存校验弹窗。**
- 选择器已删除（2026-11-03）：Settings 只保留地址/密钥输入；不再有「选择」按钮或 picker。
- 「检测服务」行 subtitle 为「连接服务器并校验认证密钥」；探测使用当前 TLS 草稿。
- 地址/密钥输入框保持 44dp `CompactField`；TLS 开关位于认证密钥与检测服务之间。
- 重启必要性不变：serverUrl、token 与 TLS 策略都在进程启动装配（`AppModule`/`MylifeModule`/`MainActivity`），改任一项均需重启进程。
- **未配置引导弹窗（2026-10-13 二改 → 三改）**：对话页观察 `serverConfig`——地址或密钥不齐备时**不启动连接与采集**。**提示策略（三改）**：本次运行首次检测到不齐备时提示一次「需要先配置服务器」（可外点/返回关闭；单按钮「去设置」→ 设置页，点击后关窗并导航）；此后**仅点击 app bar 连接按钮**（DISCONNECTED 态状态指示器 → `RetryConnection`；未配置期不发起连接、再次弹窗）才提示，其他时机不提示；**进程重启后重复上述行为**。`OnScreenStart` 不再触发弹窗；**首发射前点击连接按钮忽略**（防以占位/旧默认值误发起连接，review 加固）。配置齐备（保存并重启进程生效）后弹窗消失。**首发射前 `OnScreenStart` 延后决策**（pendingScreenStart，review 修正）：DataStore 首读晚于页面进入，未加载即决策会以占位配置误启动或漏 stop。运行中转为不齐备 → 已启动的协调器被 stop。

### 3.5 改动点清单（文件级）

1. `domain/model/ServerOption.kt`：**2026-11-03 已删除 preset / `ServerOption` 类**；保留 `ServerConfig` 与 `parseServerAddress` / `wsUrlFromAddress` / `httpBase` / `isValidAuthToken` / `isServerConfigComplete` 纯函数与 `UNSET_SERVER_WS_URL`。
2. `ui/navigation/ServerLinks.kt`：`serverAddress(wsUrl)` 继续服务 serverBaseUrl / MylifeModule 推导；新增纯函数落 `domain.model`（§3.2）。
3. `data/local/PreferencesDataSource.kt`：`server_address` / `auth_token` / `tls_allow_self_signed` 键；`updateServerConfig(address, token, tlsAllowSelfSigned)` 原子写入；旧 `server_id` 只读保留。
4. `SettingsRepository` / `SettingsUseCase`：`observeServerConfig()` / `setServerConfig(address, token, tlsAllowSelfSigned)`。
5. `SettingsUiState.kt`：`serverAddressInput` / `authTokenInput` / 已存基线；`tlsAllowSelfSigned` / `savedTlsAllowSelfSigned`；`ServiceStatus` + `AuthFailed`；dirty 派生。
6. `SettingsViewModel.kt`：草稿编辑 / `saveServerConfig()` 直接落盘 + 重启提示 / `checkService()` 传 TLS 草稿；注入 `ServiceProbe` 与 `@Named("deviceId")`。
7. `data/remote/ServiceProbe.kt`：`probe(address, token, deviceId, allowSelfSigned)`；OKHttp GET `/healthz_token`（200/401/其它 三映射）；`TlsOkHttpClientFactory` + Hilt provide。
8. `SettingsScreen.kt`：地址/密钥输入 + TLS 开关 + 保存按钮 + 检测状态；无选择器 / `ServerOptionPicker`；`ServerSwitchDialog` 为唯一保存后提示。
9. `di/AppModule.kt` / `di/MylifeModule.kt`：config 装配「地址 + token + allowSelfSigned」；MyLife/Coil/ExoPlayer 复用 `LivaChatConfig.allowSelfSigned` 快照。
9a. `MainActivity.kt` / `ui/navigation/TlsSettings.kt` / `ui/screens/webview/WebViewScreen.kt`：注入 `LocalAllowSelfSigned`；WebView SSL 错误仅对已配置服务器同源、且开关开启时放行。
9b. `AndroidManifest.xml`（App + SDK）：Release `usesCleartextTraffic=false`；`src/debug/AndroidManifest.xml` 允许本地明文供 E2E/MockWebServer。
10. 服务端：**无需改动**——`/healthz_token` 已实施（工作树待提交：`services/app/routes.py:61-77`、`services/tests/unit/app/test_healthz_token.py`、`docs/server_design.md` §9.1/§10.2 同步）。
11. `livachat.sh`：`check_required_env`（:140-155）增加 `APP_AUTH_TOKEN` 字符集校验——`^[A-Za-z0-9._-]+$`，不符 → die（与 App 输入校验同规则；服务端 `validation.py` 不加，不在本轮范围）。
12. 测试与文档：见 §5；`ServerSwitchDialogTest` / `SettingsScreen.kt` 已同步。
- versionCode 规则：仅随 git 提交递增（迭代期不动）。

## 4. 边界矩阵

| 场景 | 行为 |
|------|------|
| 地址非法（空 / 含 scheme / 无端口 / 端口越界） | 检测置灰；`dirty` 时保存按钮出现但置灰，并显示 inline 校验文案（无红框、无校验弹窗） |
| 地址合法 + token 正确 + 服务端新版 | 检测 → 可用 |
| token 错 | 检测 → 认证失败（HTTP 401）；WS 连接 → 401/close 4001 **终态失败不重试**，app bar 显示「连接断开」 |
| token 未填写（默认空，Q3） | 检测置灰（空串非法，§3.2）；保存按钮置灰 + inline 文案；进程连接为 401——须先在设置页填写 |
| 地址不可达 / 超时 | 检测 → 不可用（connect 超时 5s） |
| TLS 开关开启 + 自签服务器 | WS/MyLife/Coil/ExoPlayer/ServiceProbe 放行自签；WebView 仅对已配置服务器同源放行 |
| TLS 开关关闭 + 自签服务器 | 系统校验失败：检测显示不可用；WS 连接失败；需开启开关或改用受信证书 |
| 旧服务端（无 `/healthz_token`） | 404 → 检测显示「不可用」（服务器实际可达但无法预检）；服务端与 App 同库发布，需同批升级 |
| 保存时值与已存相同 | 保存按钮不出现（dirty 才出现） |
| 保存后 | 立即显示不可关闭的重启提示；只能「退出」；配置在下次启动生效 |
| 离开设置页未保存 | 草稿丢弃（下次进入回显已存值）；不弹挽留 |
| 升级 / 首装（无配置） | 地址与密钥为空 → 对话页不可关闭弹窗「去设置」；须输入并保存 → 重启生效（旧 `server_id` 原样留存、不读取；二改） |
| 地址或密钥任一为空 / 非法 | 对话页 `OnScreenStart` 不启动连接与采集（占位 URL 兜底装配不崩）；运行中转不齐备 → 已启动连接被 stop |
| 存储值非法（异常写入 / 损坏） | 回退空串（同未配置），不做默认回退（§3.1 二改） |
| token 含字符集外字符（仅允许大小写字母 / 数字 / `-` / `_` / `.`） | 保存/检测门控拒绝：保存按钮置灰 + inline 文案，检测置灰——防 SDK query 拼接破坏（§6）；`livachat.sh start` 同规则拦截 |
| webroot 渲染基址 / 域名映射 | 不变（serverAddress 解析完整 wsUrl；映射注释状态不动，见 §9） |

## 5. 测试计划

| 层 | 用例 |
|----|------|
| JVM 单测 | `ServerOptionTest`：`parseServerAddress`（合法 IP / 域名 / 端口边界 / 非法样式）+ `wsUrlFromAddress` / `httpBase` / `isValidAuthToken` / `isServerConfigComplete`（preset 用例已删除）；`ResolveServerAddressTest`：合法用原文 / 缺失与非法一律空串；`SettingsRepositoryTest` / `SettingsUseCaseTest`：observe / set ServerConfig（含 TLS）；`SettingsViewModelTest`：草稿编辑 / dirty / 直接保存 / inline 校验 / TLS / 检测三结果映射 + 并发早退 + 快照守卫（fake `ServiceProbe`）；`ConversationViewModelServerConfigGateTest`：不齐备门控；`ServiceProbeTest`（https MockWebServer + 自签证书）：200 / 401 / 非 2xx-401 / 连接拒绝 + TLS 两态；`TlsSettingsTest`：WebView 同源判断；`ConnectionManagerTest`：WS 自签两态 |
| 仪器 androidTest | `ServerSwitchDialogTest`、`CompactFieldTest`、`LegalRowTest`、`LicensesDialogTest` 等；`ServerOptionPickerTest` / `SaveValidationDialogTest` 已删除 |
| 服务端 | **既有（随端点落地）**：`services/tests/unit/app/test_healthz_token.py` 8 例（200×2、401×4、422、metrics 摘除）——本改造不新增服务端测试 |
| 编译 | `:LivaChatApp:assembleDebug`；services 相关测试子集 |
| 脚本 | `livachat.sh start` 前置：`APP_AUTH_TOKEN` 含字符集外字符 / 为空 → die（手工验证） |
| 真机 | 改地址 → 检测 → 保存 → 直接出现重启提示 → 退出 → 冷启动连新地址；错 token → 认证失败；对 token → 可用；同服并发：探测不影响进行中会话；TLS 开启/关闭对自签服务器的影响；**未配置路径（二改）**：清 DataStore 重启 → 对话页弹「去设置」引导（不发起连接/采集）→ 设置页输入并保存 → 退出 → 重启后正常连接 |

## 6. 风险

- token 明文存 DataStore：与现状（硬编码）等价、不更差；加密存储不在本轮。
- SDK `ConnectionManager.kt:137` 的 token / deviceId query 拼接**无 URL 转义**——**Q4 拍板：SDK 修复不做**，以输入侧字符集校验规避：token 限 `[A-Za-z0-9._-]`（均为 URL-safe 字符，拼接天然安全）。该字符集含 `-`/`_`/`.`，base64url 与 JWT compact 形式均天然兼容；若未来 token 需集外字符，再考虑 HttpUrl 改造。
- 检测依赖服务端已含 `/healthz_token`（现为工作树未提交改动）——发布需与 App 同批；旧服务端 → 404 误报不可用（§4）。
- 探测为一次普通 GET：会出现在服务端访问日志（`services/app/access_log.py` 只记 method / path / status / ip / 延迟，**不含 query** → token 不入日志）；不进入连接数 / 在线状态统计；无 register / 录音副作用。

## 7. 决策记录（Q1 已废弃 / Q2–Q4 已拍板 / Q5 已解决）

| # | 问题 | 选项 / 倾向 |
|---|------|-------------|
| Q1 ~~已拍板~~ 已废弃 | 「选择」按钮形态（视觉） | **已废弃（2026-11-03 移除 picker）**；历史方案 A=输入框尾内图标按钮（2026-10-13 拍板），不再实现 |
| Q2 ✅ 已拍板 | 重启对话框文案 | **泛化「服务器配置已更新」口径（2026-10-13 拍板；同日再简：正文仅「重新打开 App 后生效。」）**；定稿见 §3.4，`ServerSwitchDialogTest` 已同步 |
| Q3 ✅ 已拍板 | token 首启默认 | **默认为空（2026-10-13 拍板）**——不预填；首次使用须手工填写（迁移口径见 §3.1） |
| Q4 ✅ 已拍板 | SDK URL 拼接转义修复 | **仅输入侧校验（2026-10-13 拍板定稿；SDK 修复不做）**：token 限 `[A-Za-z0-9._-]`、`livachat.sh start` 同规则拦截 |

> Q5（探测方案）已解决（2026-10-13）：服务端新增 `GET /healthz_token` 预检端点，检测方案按 §3.3 定为 REST 预检；
> WS 握手探测 / 合成 device_id / `probe=1` 分支候选均废弃。

## 8. 实施记录

> 本节为历史记录；其中 preset/picker、保存校验弹窗、四改方案等内容已被 2026-11-03 实现取代，当前行为见 §0。

| 日期 | 事件 |
|------|------|
| 2026-10-13 | 需求提出（地址输入框 / 选择按钮 / 认证密钥 / 真实检测）；分析 + 设计成文（本文）；视觉 A/B 渲染（/tmp）；待 review 拍板 |
| 2026-10-13 | 服务端 `/healthz_token` 预检端点实施（工作树待提交：`services/app/routes.py:61-77`、`services/tests/unit/app/test_healthz_token.py`、`docs/server_design.md` §9.1/§10.2）；本文 §2.4 / §3.3 / §4-§7 按此更新——检测方案定为 REST 预检，Q5 关闭 |
| 2026-10-13 | review 拍板：Q1=A（「选择」改输入框尾内图标按钮）、Q3=token 默认为空（不预填，§3.1/§4 已同步）；Q2=弹窗文案泛化「服务器配置已更新」；Q4=仅输入侧校验（token 限 `[A-Za-z0-9._-]`，`livachat.sh start` 同规则）——四项全部关闭，方案定稿待实施 |
| 2026-10-13 | Q4 定稿澄清：SDK 修复**不做**（不并入本轮）；token 允许字符集 = 字母 / 数字 / `-` / `_` / `.`——§3.2/§3.5/§4-§7 已同步 |
| 2026-10-13 | 文档 review 修正三处：§5 服务端测试例数勘误（9→8，实为 200×2、401×4、422、metrics）；§3.1/§4 补「存储值非法回退 PUBLIC 默认」（延续 `fromId` 语义，防 `MylifeModule.kt:55` 装配期抛异常崩进程）；§3.4 明确选择器对勾口径（与已存地址一致，`contentDescription` 语义不变） |
| 2026-10-13 | **实施完成**（按 §3.5 清单）：领域层（`ServerConfig` + 纯函数 + 预设，位置调整见 §3.2）、数据层（新键 + `resolveServerAddress` 迁移/回退）、`ServiceProbe`（OkHttp GET `/healthz_token` + Hilt 绑定）、`AppModule`（地址+token 装配、`@Named("deviceId")` provider）、`SettingsViewModel`/`UiState`（草稿模型 + 快照守卫 + AuthFailed）、`SettingsScreen`（Q1=A 布局 + 保存并重启 + 弹窗文案泛化 Q2）、`livachat.sh` 字符集校验。验证：全量 app 单测 **811 例 0 失败（4 skip）**（新增 `ServiceProbeTest`〔https MockWebServer + okhttp-tls 测试依赖〕、`ResolveServerAddressTest`；重写 `ServerOptionTest`/`SettingsViewModelTest`/`SettingsRepositoryTest`/`SettingsUseCaseTest`）；`:LivaChatApp:assembleDebug` 与 `compileDebugAndroidTestKotlin` 通过。真机项待做（§5） |
| 2026-10-13 | **二改**（需求方）：① 去掉旧键映射与默认回退——`resolveServerAddress` 合法用原文、缺失/非法一律空串，升级/首装地址为空由用户选择（旧 `server_id` 留存不读取）；② 地址或密钥为空 → 对话页弹不可关闭「去设置」引导（`showServerConfigPrompt`），`OnScreenStart` 不启动连接/采集，运行中转不齐备 stop；③ 装配兜底 `UNSET_SERVER_WS_URL` 占位防解析崩溃。§1（需求 6）/§3.1/§3.2/§3.4/§4/§5/§8 已同步；新增 `ConversationViewModelServerConfigGateTest`（5 例）。**Review 修正**：门控改为**延后决策**（首发射前 OnScreenStart 置 pendingScreenStart——防以未加载默认值误启动占位连接/空会话/短暂开麦，及 stop 窗口竞态；§3.4）；`ServiceProbe` 补 `CancellationException` rethrow（原 `catch (Exception)` 会吞协程取消）。验证：全量单测 **816 例 0 失败（4 skip）**；`:LivaChatApp:assembleDebug` 与 `compileDebugAndroidTestKotlin` 通过 |
| 2026-10-13 | **UI 微调**（需求方，真机视感）：① 服务器/密钥输入框降高至 44dp；② 地址提示移至「服务器」title 右侧（去原括号内容），真机复核缺「域名:port」后定为「格式：ip:port 或 域名:port」；③ 移除密钥字符集提示行，「认证密钥」title 右侧「查看 server 配置文件获取」。**真机复核修正**：M3 `OutlinedTextField` 强制 44dp 会裁剪文字下半（内部按 56dp 布局）——改为自绘 `CompactField`（`BasicTextField` + 44dp 边框行、垂直居中、聚焦边色 BrandIndigo、尾内图标槽）。真机截图核验：文字完整居中、两处提示齐备。§3.2 提示句已同步 |
| 2026-10-13 | **文案定稿**（需求方）：① 按钮「保存并重启」→「**保存**」；② 保存弹窗正文仅保留「**重新打开 App 后生效。**」并**居中对齐**（地址句移除，`ServerSwitchDialog` 删 `address` 参数）；③ 密钥提示定稿「查看 server 配置文件获取」。§3.4/§7 Q2 已同步。**操作注记**：`connectedDebugAndroidTest` 跑完会卸载被测 app（设备本地 DataStore 随之清空——本次真机已重新走「未配置→引导弹窗」路径）；后续仪器验证宜用 `am instrument` 直跑（不触发卸载） |
| 2026-10-13 | **三改**（需求方）：未配置引导弹窗策略——运行首次检测到不齐备**提示一次**（可外点/返回关闭；`dismissServerConfigPrompt`），此后**仅点击 app bar 连接按钮**（DISCONNECTED 态状态指示器）再提示且不发起连接；`OnScreenStart` 不再触发弹窗；进程重启后重复。§3.4/§5 已同步；门控测试扩至 7 例 |
| 2026-10-13 | **连接语义修复**（需求方）：**HTTP 401 / 服务端 close 4001（认证拒绝）改为终态失败**——SDK `ConnectionManager` 新增 `failTerminal`，不再进入退避重连，直接落 DISCONNECTED。根因：原实现把 401 与网络失败一视同仁 → 退避重连（RECONNECTING → UI「正在连接...」，`ConversationScreen.kt:1133` 映射）；手动重连/回前台自愈调用 `sdk.connect()` 会 `reconnectScheduler.reset()` 重启整轮 5 次退避（≈31s），错 token 时 app bar 永停在「正在连接」（真机日志实证：失败间隔 8→15.8→17.5s 后归零重来）。验证：SDK 单测新增 2 例（401/4001 均终态、500ms 后仅 1 次请求）；全量 app 单测通过；真机错 token：单次 401 → 立即「连接断开」（日志 `ws_auth_failed \| HTTP 401 — terminal, no retry`，无后续重试） |
| 2026-10-14 | **四改方案**（需求方）：① 移除「服务器」「认证密钥」两行 title 右侧提示文字；② 两行改空态占位符（地址「ip:port 或 域名:port」/ 密钥「查看 server 配置文件获取」）；③ 点击「保存」校验不合格 → 通用文案弹窗（保存按钮随之不再置灰）；④ **字段红框取消**（错误反馈仅弹窗）。同日 review 定稿（Q6=B、去红框、密钥占位符），方案见 §10 |
| 2026-10-14 | **四改实施**（按 §10 定稿）：`CompactField` + 必填 `placeholder`（改 `internal`）与去 `isError`；两行提示句移除改单标题；双行占位符；保存按钮去 `enabled`/禁用色槽；+ `SaveValidationDialog`（Q6=B 通用文案；可外点/返回关闭）接 `showSaveErrorDialog`；`SettingsUiState` 删 `addressValid`/`tokenValid`（死代码，含 import）+ `dismissSaveError()`。验证：全量 app 单测 **821 例 0 失败（4 skip）**；`:LivaChatApp:assembleDebug` 与 `compileDebugAndroidTestKotlin` 通过；仪器直跑（`am instrument`，不触发卸载）**14 例通过**——新 `SaveValidationDialogTest` 3 + 新 `CompactFieldTest` 3 + 回归 `ServerSwitchDialogTest` 3 / `ServerOptionPickerTest` 5。**真机核验（截图未入库，/tmp/livachat_setting_mock_v2/real_*.png）**：两行提示移除/字段无红框；地址清空 → 占位符「ip:port 或 域名:port」显示、「保存」出现即可点、「检测」维持置灰；非法（空地址）点保存 → 通用弹窗居中版式（「无法保存/请填写正确的服务器地址与认证密钥后再保存。/知道了」）——核验中途用户接管设备，密钥占位符视觉（设备字段有已存值，仪器测试已覆盖文案）、picker 回填即隐、合格保存重启链路未回归 |
| 2026-10-14 | **弹窗文案微调**（需求方）：保存校验弹窗正文「…后再保存。」→「**请填写正确的服务器地址与认证密钥。**」（`SaveValidationDialog` + `SaveValidationDialogTest` 断言 + §10.3/§10.4/§10.5 + 定稿 mock 同步；此前真机截图 `real_save_dialog.png` 为旧文案留存） |
| 2026-11-03 | **当前实现**：移除 preset/picker、`SaveValidationDialog` 与中间确认框；保存直接落盘 → 重启提示；新增 TLS 开关并覆盖 SDK/MyLife/ServiceProbe/WebView；`ServerOption` 类删除、仅保留 `ServerConfig` 与纯函数。详见 §0；`ServerOptionPickerTest` / `SaveValidationDialogTest` 删除 |

## 9. 历史归档（2026-10-10：连接用 IP、渲染链接用域名映射）

> 原文档被本改造取代，压缩归档如下；代码注释（`ServerLinks.kt:39`、`AppModule.kt:74`）与
> `webroot_feature.md` §3.4/§6 对本文档的引用以本节为准。
> 本节及以下 §10 均为历史归档，不代表 2026-11-03 当前实现。

- **背景与决策**：`ServerOption.PUBLIC` 的 WS 连接用 **IP**（`wss://<server-host>:8080/…`，连接不依赖 DNS）；
  markdown `livachat_url://webroot/…` 的渲染基址经映射 `<server-host> → <public-domain>` 用**域名**，
  未命中回退原 host。根因：dev 证书仅签 DNS SAN（无 IP SAN），系统浏览器按 hostname 校验，IP 直开必证书警告；
  应用内各链路（SDK WS、mylife OkHttp、WebView）均 trust-all 不受影响。映射表单点常量
  `ServerLinks.kt` `PUBLIC_DOMAIN_BY_HOST`，仅作用于 `serverBaseUrl()`（渲染基址）。
- **现状（截至本文归档时）**：**映射已注释停用**（commit `efdb268`，versionCode 108→109）——
  `PUBLIC_DOMAIN_BY_HOST` 与 `serverBaseUrl` 的映射查询改注释、回退 `serverAddress(wsUrl)?.base`；
  `ServerLinksTest` 两例注释跳过。**恢复方法**：取消 `ServerLinks.kt` 顶部注释 + 还原 `serverBaseUrl` 映射查询 + 两例单测。
- **实施记录（原表保留）**：2026-10-10 需求提出 → 方案 review（映射只作用 markdown 渲染基址）→
  实施（commit `6d59692` 切域名、versionCode 106→107；随后回退 IP，`ServerLinksTest` 28/28、全量 app 单测 716 例 0 失败）→
  提交 107→108 → 映射停用（`efdb268`，108→109）。
- **关联**：`docs/webroot_feature.md` §3.4（livachat_url 渲染期转换）、`docs/server_ssl_support.md`（server TLS 配置）。

## 10. 四改方案（2026-10-14 历史方案，已被 §0 取代）

> **历史方案，不代表当前实现。** 其中 `SaveValidationDialog` / `showSaveErrorDialog` / 保存点击弹窗已删除；当前为「保存置灰 + inline 校验 + 直接保存」。
> mock（定稿渲染，2026-10-14 review 后）：`/tmp/livachat_setting_mock_v2/setting_v2_final.png`（HTML 同目录，不入库）——
> ① 空态双行占位符；② 保存校验通用弹窗（无红框）。评审期候选对照：`setting_v2_abc.png`（A/B 弹窗两案，A 已否）。
> 范围：仅「设置页 · 服务区」两个输入行的提示/占位、字段红框与保存反馈；其余一律不动（需求方原文「其他不动」）。

### 10.1 需求（2026-10-14，需求方原文拆解）

1. 「服务器」title 右侧说明文字「格式：ip:port 或 域名:port」**移除**；
2. 地址为空时，输入框内显示灰色占位提示：**ip:port 或 域名:port**（输入内容后消失）；
3. 「认证密钥」title 右侧说明文字**移除**；**密钥为空时输入框内占位「查看 server 配置文件获取」**（review 增补：沿用原右侧提示文字）；
4. 点击「保存」时校验输入，不合格 → **弹窗提示**（review 定稿 **Q6=B** 通用文案）；
5. **字段不加红框**：校验错误不再以输入框边框表现，仅弹窗提示（review 定稿）；
6. 其他不动。

### 10.2 现状（按当前工作树核对）

| 项 | 现状 | 位置 |
|----|------|------|
| 地址提示句 | title 右侧 `bodySmall`/TextSecondary「格式：ip:port 或 域名:port」 | `SettingsScreen.kt:283-287` |
| 密钥提示句 | title 右侧 `bodySmall`/TextSecondary「查看 server 配置文件获取」 | `SettingsScreen.kt:327-331` |
| 输入框 | 自绘 `CompactField`（`BasicTextField` + 装饰盒），**无占位符能力**（空值渲染为纯空框） | `SettingsScreen.kt:360-401`（装饰盒 387-399） |
| 保存按钮 | `dirty` 才出现；`enabled = canProbe`（空/非法任一即置灰不可点）；VM 侧 `saveServerConfig` 再守卫 `!dirty || !canProbe` 直接 return——**无效点击无任何反馈** | `SettingsScreen.kt:123-140`；`SettingsViewModel.kt:90-92` |
| 字段红框 | 非空且非法时 `CompactField` 边色落 `Error`（优先级：红 > 聚焦 > 常态） | `SettingsScreen.kt:109、115、292、336、364、372-376` |
| 校验纯函数 | `parseServerAddress`（严格 `host:port`、端口 1–65535）/ `isValidAuthToken`（`[A-Za-z0-9._-]`、空串非法） | `ServerOption.kt:30-48` |
| 关联测试 | VM 保存用例（含无效输入静默早退 `save_ignores_invalid_input`）；组件直渲惯例（`internal` 组件 + androidTest 直渲） | `SettingsViewModelTest.kt:136-147`；`ServerSwitchDialogTest` / `ServerOptionPickerTest` |

### 10.3 设计

- **D1 占位符（双行）**：`CompactField` 增 `placeholder: String`（必填——两调用点均传文案）；装饰盒内输入区 Box **在 `innerTextField` 下层**渲染占位 `Text`（空值即显、有值即隐，单行 `maxLines=1`；字号同输入正文 `bodyLarge`，颜色 TextTertiary——与 `HistoryScreen.kt:96` 搜索框占位惯例一致）。地址行传 `"ip:port 或 域名:port"`；密钥行传 `"查看 server 配置文件获取"`（review 增补）。
- **D2 提示行移除**：两处 title 右侧提示 `Text` 删除，两行仅剩标题（原 paddings 保持；地址行尾内「选择」图标、密钥行尾内眼睛按钮均不动）。
- **D3 保存链路**：按钮出现条件不变（dirty）；**去掉 `enabled = canProbe` 置灰与禁用色槽——出现即可点**（置灰则点击无事件、弹窗无从触发）。`saveServerConfig()` 改「先校验、不合格弹窗并早退」：
  - 守卫 `!dirty` → 静默早退（不变）；`!canProbe` → 置 `showSaveErrorDialog = true` → 通用文案弹窗；合格 → 原链路不变（一次性落盘 → `ServerSwitchDialog` → 退出）；
  - `dismissSaveError()` 清空弹窗态；
  - 「检测服务」行置灰逻辑（`canProbe`）**不动**，检测点击不弹窗。
- **D4 弹窗组件**：新增 `internal SaveValidationDialog`（`AlertDialog`，版式沿用 `ServerSwitchDialog` house style：标题 `titleMedium` 居中、单按钮居中）；**可外点/返回关闭**（错误提示非阻断流程，不设 `dismissOnBackPress=false`）。定稿文案（Q6=B，mock ②）：
  - 标题「无法保存」；正文「请填写正确的服务器地址与认证密钥。」；按钮「知道了」。
- **D5 字段红框移除**（review 定稿）：删 `ServerAddressSetting`/`AuthTokenSetting` 的 `isError` 参数与 `CompactField.isError` 及 `borderColor` 的 `Error` 分支——字段边色恒为：聚焦 `BrandIndigo` / 常态 `SurfaceVariant`。`SettingsUiState.addressValid`/`tokenValid` 失去全部消费点（仅被红框接线引用，测试无引用）→ **一并删除**（含 `parseServerAddress`/`isValidAuthToken` import）。

### 10.4 拍板记录（2026-10-14 review）

| # | 问题 | 拍板结果 |
|---|------|----------|
| Q6 | 保存校验弹窗内容 | **B＝单句通用文案**「请填写正确的服务器地址与认证密钥。」（A 逐行列出已否；同日文案微调见 §8） |
| Q7 | 空值文案是否与格式错误区分 | **随 Q6=B 废弃**——无逐字段文案；空值与非法统一进通用弹窗 |
| — | 字段红框 | **取消**——错误反馈仅弹窗（原「非空非法红框」机制删除） |
| — | 密钥占位符 | **增补**——空态占位「查看 server 配置文件获取」（沿用原右侧提示文字） |
| — | 微文案 | 标题「无法保存」/ 按钮「知道了」/ 地址占位符「ip:port 或 域名:port」（不含「格式：」前缀）——以定稿 mock 为准 |

### 10.5 边界矩阵（2026-10-14 四改，新增/变更行）

| 场景 | 行为 |
|------|------|
| dirty 且地址与密钥均合格 | 原链路：落盘 → 重启弹窗 → 退出（不变） |
| 地址空 / 非法（含 scheme / 无端口 / 端口越界 / 字符集外） | 通用弹窗「请填写正确的服务器地址与认证密钥。」；不落盘、不弹重启窗；字段无红框 |
| 密钥空 / 含字符集外字符 | 同上——同一通用弹窗（不再区分空值/格式、不再指出具体字段） |
| 地址与密钥均不合格 | 同上——单弹窗一条文案 |
| dirty=false（含直接调用） | 静默早退（保存按钮亦不出现——均不变） |
| 点击「检测服务」而输入非法 | 不变：置灰不可点（不弹窗） |
| 弹窗展示中 | 模态：先关闭（外点/返回/「知道了」）才能继续操作；关闭后可直接继续编辑，再次保存重走校验 |
| 字段边色 | 恒为：聚焦 BrandIndigo / 常态 SurfaceVariant——不因内容非法变化（review 定稿） |
| 占位符 | 地址/密钥两行、仅空值时显示；picker 回填 / 手工输入后即隐 |

### 10.6 测试计划（增量）

| 层 | 用例 |
|----|------|
| JVM | `SettingsViewModelTest` 更新/新增：无效输入保存 → `showSaveErrorDialog=true` + 不落盘 + 不弹重启（`save_ignores_invalid_input` 重写）；`dismissSaveError` 清空；合格保存 → 落盘且不置弹窗态（并入既有用例断言）；`save_ignores_clean_state` 保持（不置弹窗态） |
| 仪器 androidTest | 新 `SaveValidationDialogTest`（组件直渲，参照 `ServerSwitchDialogTest`）：标题/通用文案/「知道了」回调/返回键可关；新 `CompactFieldTest`（`CompactField` 改 `internal`）：空值显示占位符文本（地址/密钥两文案）、有值不显示 |
| 编译 | `:LivaChatApp:assembleDebug` + `compileDebugAndroidTestKotlin` |
| 真机 | 空态截图核验占位符；非法输入 → 保存 → 弹窗（截图核验版式）；修正 → 保存 → 重启弹窗（既定路径回归）；picker 回填后占位符消失 |

### 10.7 改动点清单（文件级）

1. `ui/screens/settings/SettingsScreen.kt`：删两处提示 `Text`；双行 `placeholder`（`CompactField` 加参并改 `internal`）；删除 `isError` 链路（两设置组件参数、调用点实参 `:109/:115`、`CompactField` 参数与 `Error` 边色分支）；保存按钮去 `enabled`/禁用色槽；+ `SaveValidationDialog`；按 `showSaveErrorDialog` 显隐 + KDoc 同步。
2. `ui/screens/settings/SettingsUiState.kt`：删除 `addressValid`/`tokenValid`（含 `parseServerAddress`/`isValidAuthToken` import）；+ `showSaveErrorDialog: Boolean = false`（`canProbe`/`dirty` 等派生不动）。
3. `ui/screens/settings/SettingsViewModel.kt`：`saveServerConfig()` 守卫改「`!dirty` 早退 / `!canProbe` 置弹窗态」；+ `dismissSaveError()`；落盘路径不变。
4. 测试：§10.6 增量（1 个 VM 测试文件更新 + 2 个新仪器测试文件）。
5. 文档：本文 §10 + §3.2/§3.4 指针 + §8 记录行。
6. versionCode：随提交递增（迭代期不动，现行规则）。

### 10.8 风险与备注

- 保存（弹窗）与检测（置灰）的无效反馈方式不一致：符合需求原文（检测未提改）；如 review 认为需一致再议。
- **去红框后错误定位变弱**（review 定稿接受）：通用文案不指出具体字段，空值与格式错误不可辨——仅「保存 → 弹窗」一条反馈路径。
- 密钥「查看 server 配置文件获取」提示改为输入框占位符——空态可见，输入后消失（信息可达性依赖空态）。
- mock 为 HTML 近似渲染（真实主题色；弹窗底色按 `ServerOptionPicker` 实测近似 `#242E4A`），仅作文案/布局评审；真机视觉项实施后核验。
