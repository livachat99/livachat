# 认证凭据传输改造：sha1(auth_token + "LivaChat") 固定盐摘要

> 状态：**已实施**（2026-10-14；服务端 / 客户端 / 测试 / 脚本 / 文档全部落地，本文件为设计基线）。
> 目标读者：实现者（services / Android）、联调与运维。
> 范围：chat server 对外的全部 token 认证 API（§2.2 共 9 个）。lvdata-mcp 的 Bearer 边不在范围（§2.4）。

## 1. 背景与目标

**现状问题**：认证凭据以明文形式在 URL query 传播——`?token=dev-livachat-token`。服务端 uvicorn access log 记录完整 query（本仓 `logs/livachat.log` 历史样本可证），任何中间层（代理/网关/浏览器历史/截图）都会捕获明文 token。`livachat.sh:144-145` 注释与 `docs/setting_server_address.md` §3.2 的字符集限制（`[A-Za-z0-9._-]`）正是为明文入 URL 的拼接安全而设。

**目标**：客户端网络上不再传输明文 `auth_token`，改为传输固定盐摘要 `token = sha1_hex(auth_token + "LivaChat")`（盐为协议常量，非机密）。服务端用存储的 `auth_token` 重算比对。

**阶段假设**：app 与 server 均在开发阶段，**不考虑向后兼容**——服务端与 app 同步升级（硬切换），不设双重接受、不做版本协商。

**与前一版（ts 动态签名）的差异**：原方案每次调用计算 `sha1(secret+ts)` 并携带 `ts` 作时效校验；本版取消 `ts`，摘要为**静态值**（同一 secret 恒定）。收益是两端改动量最小（服务端一行比较、客户端一处派生）；代价见 §3.3 安全限界。

## 2. 现状分析（codebase 事实）

### 2.1 服务端认证单点

`Authenticator`（`services/gateway/auth.py:25-47`）是全仓唯一 token 校验实现，`verify(token, device_id)` 的调用方只有 3 处，全仓 `token` Query 参数也仅出现这 3 处：

| # | 入口 | 位置 | 参数 |
|---|------|------|------|
| 1 | WS `GET /v1/ws/stream` | `services/gateway/endpoint.py:27-45` | `token`、`device_id` 均 Query 必填；accept 前 verify，失败 HTTP 401 |
| 2 | `GET /healthz_token` | `services/app/routes.py:61-77` | 同 1 的语义（预检端点）；失败 401 detail 文案逐字一致 |
| 3 | BFF `get_principal` | `services/bff/common/deps.py:40-53` | `token` Query(default="")；失败 BffError("unauthorized")→401 |

装配：`services/app/application.py:42-43`（`app.state.authenticator`，WS 与 BFF 复用同一实例）。配置：`AuthConfig`（`services/shared/config/models.py:31-35`）；dev 模式校验非空（`validation.py:30-34`）；`configs/dev|test/server.yaml:22-25` 的 `dev_token: "${APP_AUTH_TOKEN}"`。

### 2.2 对外 token 认证 API 全清单（9 个）

| # | 方法+路径 | 位置 | 校验 |
|---|-----------|------|------|
| 1 | WS `/v1/ws/stream` | `gateway/endpoint.py:27` | `verify`；另 device_id 须 UUID v4 |
| 2 | GET `/healthz_token` | `app/routes.py:61` | `verify` + device_id UUID v4 |
| 3 | GET `/api/mylife/overview` | `bff/timeline/router.py:11` | `Depends(get_principal)` |
| 4 | GET `/api/mylife/timeline` | `bff/timeline/router.py:28` | 同上 |
| 5 | GET `/api/mylife/graph` | `bff/graph/router.py:11` | 同上 |
| 6 | GET `/api/mylife/graph/entity/{entity_id}` | `bff/graph/router.py:38` | 同上 |
| 7 | GET `/api/mylife/search` | `bff/search/router.py:11` | 同上 |
| 8 | GET `/api/mylife/media/{device}/{session}/{kind}/{filename}` | `bff/common/media.py:23` | 同上（:29）；响应带 `Cache-Control: public, max-age=31536000, immutable`（:20/:36），Range/ETag 由 starlette FileResponse 提供 |
| 9 | POST `/api/mylife/events/remove` | `bff/remove/router.py:13` | 同上 |

**入口签名全部不变**：仍是 `?token=`（值语义从明文秘钥变为摘要），无新增参数、无新增端点。

**无 token 面**：`/healthz`、`/readyz`（`app/routes.py:18/:23`）、`/webroot` StaticFiles（`application.py:394/:428`）；`/docs` `/redoc` `/openapi.json` 已关（`application.py:376-378`）。无第 10 个 token 入口（已全仓核对）。

### 2.3 客户端（Android）token 传播面

**SDK（LivaChatSDK）— WS 握手**
- 拼串唯一处 `ConnectionManager.kt:137`：`"$serverUrl?token=$token&device_id=$deviceId"`；`token` 是构造器 val（:27），由 `LivaChatSDK.kt:108-111` 从 config 注入（`AppModule.kt:43` 进程启动 runBlocking 读 DataStore）。
- 重连（`ConnectionManager.kt:221-260`）每次重新执行 `connectInternal()` → :137 重建 URL；401/服务端拒绝 → `failTerminal`（:319-324），不重试（终态）。
- SDK 只做传输，不感知 token 语义——**摘要派生放 App 层则 SDK 零改动**。

**App — REST 与媒体**
- `MylifeApi` 是手写 OkHttp（非 Retrofit）：`@Named("mylife")` client 注入（:38-39）；`execute()` 统一附 `token`（:152-165，:158）；`removeEvents` 独立附（:108）；`downloadBytes` 直接 GET 传入 URL（:120-123）。
- `MylifeRepository.absolutize()`（:383-391）：**响应映射时**给相对 `media_url` 附 token → 绝对 URL 进 UI state（overview :140-149 每 60s 轮询重映射、frames :221、audioSegments :301、hotSlot :371）。
- 媒体消费：Coil 2.6.0 `AsyncImage`（`PeriodCard.kt:117`、`FrameGrid.kt:109`、`FrameViewerOverlay.kt:315`、`HotSlotRowItems.kt:75`、`HotSlotFrameViewer.kt:260`，loader 于 `MylifeModule.kt:92-99`）；ExoPlayer media3 1.4.1 + `media3-datasource-okhttp`（`ExoAudioPlayer.kt:42/:145`）；保存相册 `downloadFrame`（`FrameGalleryViewModel.kt:143`、`HotSlotViewModel.kt:312`）。全部经 `@Named("mylife")` client（已逐一核实）。
- `ServiceProbe`（`ServiceProbe.kt:29-70`）：探测地址/token 来自设置页**输入框草稿**（可能 ≠ 已保存配置），`HttpUrl.Builder` 拼参。
- 死代码：`AppModule.kt:77-84` 拼 `/mylife?token=<明文>`，经 `ConversationViewModel.kt:58/:61` 暴露 `timeMachineUrl`——全仓无消费点（服务端 /mylife 已摘除）；遗留 `apps/web/mylife` SPA 不可达。
- 装配事实：`MylifeApi` 与 `MylifeRepository` 注入的 `config` 都是 SDK 的 `LivaChatConfig`（MylifeApi.kt:41、MylifeRepository.kt:68），其 `token` 字段在 `AppModule.kt:52-56` 装配处一次写定——派生放装配处则两者零改动（§4.1）。

**本机存储**：secret 存 DataStore（`PreferencesDataSource.kt:89-90`），进程启动快照（`AppModule.kt:43`），改配置需重启（现状，不变）。

### 2.4 排除项：lvdata-mcp 边

chat server ↔ lvdata-mcp 用独立 token（`configs/dev/lvdata_mcp.yaml:8` / `server.yaml:268` `lvdata_api_token`）经 `Authorization: Bearer` 头（`tools/lvdata-mcp/src/lvdata_mcp/server.py:206-223`）——不同凭据、不同传输、服务端↔服务端，不在本次范围。

### 2.5 泄漏面归纳

| 面 | 现状 | 改造后 |
|----|------|--------|
| uvicorn access log | 记录完整 query（含明文 token） | 记录摘要（非明文；但摘要本身即凭据等价物，见 §3.3） |
| app 自建 access log | 只记 path（`app/access_log.py:22-26`） | 不变 |
| 中间层/浏览器历史/截图 | 明文 | 摘要 |
| 服务端日志 | `logging.py:21` dev_token 属敏感键脱敏 | 不变 |

## 3. 协议设计

### 3.1 凭据构造（规范）

```
secret : APP_AUTH_TOKEN，两端本机持有，永不入网（服务端 config dev_token；客户端 DataStore）
salt   : 协议常量 "LivaChat"（两处硬编码，UTF-8；非机密，仅域分离用途）
token  : 小写 hex(sha1( UTF-8(secret) 串接 UTF-8(salt) ))，恒 40 字符 [0-9a-f]
```

- 计算时机：**客户端**进程启动装配时派生一次并复用（摘要对同一 secret 恒定）；**服务端**启动时由 secret 预计算期望值。两端公式必须逐字节一致。
- 请求形态：完全沿用现有 `?token=`（值即摘要）；**无新增 query 字段、无时效字段**。

Python / Kotlin 参考实现（两端必须逐字节一致）：

```python
import hashlib
SALT = "LivaChat"
token = hashlib.sha1((secret + SALT).encode("utf-8")).hexdigest()
```

```kotlin
const val AUTH_SALT = "LivaChat"
val token = MessageDigest.getInstance("SHA-1")
    .digest((secret + AUTH_SALT).toByteArray(Charsets.UTF_8))
    .joinToString("") { "%02x".format(it) }
```

规范固定向量（两端交叉验证用）：secret = `dev-livachat-token` → token = `1161f736057f8e087b6351a66254f32510d0121c`。

### 3.2 服务端校验算法

`Authenticator.verify(token, device_id)`（签名不变；dev 模式）：

1. 构造时预计算：`self._expected = sha1_hex(dev_token + SALT)`；若 `dev_token` 为空（仅测试夹具会构造此态）→ `self._expected = ""`，维持现状"仅空 token 放行"的夹具语义。
2. 两侧统一 UTF-8 `encode()` 为 bytes 后 `hmac.compare_digest` 比较——`compare_digest` 对含非 ASCII 的 str 会抛 `TypeError`（会变成 500），必须避免；不等 → 401；通过 → `Principal(user_id="dev-user", device_id=...)`（返回结构不变）。

边界语义：
- 缺 `token` 参数 → 422（FastAPI Query 校验，与现状一致）；凭据错误 → 401，detail 文案与现状逐字一致。
- jwt 模式分支维持预留关闭（`auth.mode=jwt` 仍 raise）。

### 3.3 安全限界（如实列出）

**防护点**：
- 明文 secret 不再出现在任何传输/日志/历史——抓包或读日志者**无法反推** `auth_token`。
- 若该 secret 在其他系统复用，泄漏面不跨系统连带（各处摘要不同或不可逆）。
- 固定盐提供域分离（裸 `sha1(secret)` 的预制表不直接可用）；盐是公开常量，不提供保密性。

**残余风险**（本方案接受）：
- **摘要即凭据**：拿到摘要者可无限期、任意端点直接重放（无 ts、无 nonce、无窗口）。本方案是"不传明文"，**不是**抗重放——抗重放需回到 ts/HMAC 动态签名方案（已评估，按简化需求未采纳）。
- **明文仍在本机**：客户端存 secret 原文（DataStore）；本方案解决传输/日志泄漏，不解决终端失窃。
- 摘要不绑定 path/method/device；salt 为常量，轮换 salt 与轮换 secret 均为硬切换。
- trust-all TLS 现状不变（`MylifeModule.kt:75-79` 注释已列为生产收敛项）。
- 与 JWT 预留（`docs/server_design.md` §9.1）的关系：本方案是**传输层去明文**，不引入身份体系；`auth.mode=jwt` 维持预留关闭。

## 4. 客户端改造设计（Android）

**核心：一处派生函数 + 现有拼装路径原样复用。** 摘要静态化后，不再需要请求时签名拦截器——把"派生后的值"喂给所有既有 `token=` 拼装点即可。

### 4.1 派生工具与接入点

- 新增派生函数（App 层，建议放 `domain/model/` 或 `data/remote/` 工具文件）：`wireToken(secret) = sha1Hex(secret + "LivaChat")`。
- **单点接入**：`AppModule.kt:52-56` 装配 `LivaChatConfig` 时 `token = wireToken(serverConfig.token)`——装配处是进程内唯一的配置快照点（:43 runBlocking 读 DataStore），派生一次即覆盖全部网络调用方，与"改配置需重启"的既有语义一致。
- **SDK 零改动**（`ConnectionManager.kt:137` 不改）；**`MylifeApi` / `MylifeRepository` 零改动**——两者注入的就是 `LivaChatConfig`（MylifeApi.kt:41、MylifeRepository.kt:68），其 `token` 字段装配后已含摘要，:108/:158/:389 的拼装原样生效。媒体 URL 进 UI state 携带静态摘要——与现状（携带明文）同为恒定值，Coil cache key 稳定、服务端 `immutable` 语义不受影响。
- `ServiceProbe.kt:41-47`：入参是**输入框草稿 secret**（可能 ≠ 已保存配置，不能复用装配快照）→ 在 `OkHttpServiceProbe.probe` 拼 URL 前对入参派生；`SettingsViewModel` 及调用方不变。
- 设置层语义不变：`SettingsRepository.observeServerConfig`（`ServerConfig`）与设置页持有的仍是 secret 原文——校验/回显/门控（`ConversationViewModel.kt:331` `isServerConfigComplete`）全不动；`ServerOption.kt:46` 正则 `^[A-Za-z0-9._-]+$` 保持不变（secret 不再入 URL，无需放宽——范围最小原则）。
- `LivaChatConfig.kt:14-15` 注释更新（现为 "JWT bearer token; App is responsible for refresh" → 装配后即 wire 凭据）。

### 4.2 死代码清理（已定：删除）

- `AppModule.kt:77-84` `@Named("mylifeUrl")` provider（:83 明文拼接）与 `ConversationViewModel` 构造参数 `mylifeUrl`（:58）+ 属性 `timeMachineUrl`（:61）——全仓无消费点（服务端 /mylife 已摘除）。
- 连带：12 个 ConversationViewModel 测试的构造传参 `mylifeUrl = "https://example.com/mylife"`（`ConversationViewModel*Test.kt` 各一处）同步删除；`ServerLinksTest.kt:13` 注释提及可顺手改述。

### 4.3 客户端测试影响

- **不变**：`MylifeApi` / `MylifeRepository` / 媒体 URL 类断言——这些测试直接构造 `LivaChatConfig`、派生不在被测类内（`MylifeApiTest.kt:57/247/273`、`MylifeRepositoryTest.kt:239/376`、`AudioGalleryScreenTest.kt:216` 均不需改）。
- **需改**：`ServiceProbeTest.kt:68-79`（探测内部派生，URL/token 断言改为摘要值）；`SettingsViewModelTest.kt:159` 视断言内容（断言转发给 fake probe 的草稿值则不变）。
- **不动**：`MockServerHarness`（SDK 与 App 两个，token 仅构造参数、无校验逻辑）；SDK 模块测试零改动。
- **新增**：`wireToken` 单测 + 跨语言固定向量交叉验证（§3.1 的 `dev-livachat-token` 向量，Kotlin 输出须与 Python 一致）。

## 5. 服务端改造设计（services）

| 改动 | 文件 | 说明 |
|------|------|------|
| 校验逻辑 | `services/gateway/auth.py:33-47` | `__init__` 预计算 `_expected = sha1_hex(dev_token + SALT)`（空 secret → `""` 夹具语义）；`verify` 改 bytes 版 `hmac.compare_digest`；`SALT = "LivaChat"` 常量（:11）；`wire_digest()`（:14） |
| 其余 | — | 入口/配置/validation **全部不动**（签名、参数、配置零变化） |

不新增：配置字段、query 字段、nonce 存储、双重接受分支。

**脚本注释**：`livachat.sh:142-159` 字符集门禁的「兼作 query token」理由已失效（摘要恒为 hex）——注释更新；门禁本身**保留不放松**（已定，非空检查仍需要）。

## 6. 测试与验证

**单元**
- `services/tests/unit/gateway/test_auth.py`：用例表——正确摘要通过；错误摘要 401；缺 token 401；非 ASCII token（如 `token=中文`）→ 401（bytes 比较，不得 500）；空 secret 夹具态（仅空 token 放行）；jwt 预留仍 raise。
- `services/tests/unit/app/test_healthz_token.py`：`TOKEN` 常量改为摘要值，其余用例不动。
- `services/tests/unit/bff/`：`conftest.make_client` 增加摘要辅助（`digest(dev_token)`）；`test_auth.py` 7 端点；空 secret 夹具（`test_gzip.py:26`、`test_timeline_api.py:535`）语义保持零改动。

**集成 / e2e**
- `tests/integration/gateway/conftest.py`（:20/:38/:96-97）：`ws_url` fixture 改用摘要（辅助函数就近）；`test_ws_handshake.py:20-21` 同步。
- `tests/e2e_backend/`（conftest.py:16/:25/:78、`client.py:34`、`scenarios/test_auth_failed.py`）：同改。

**脚本与 mock**
- `services/scripts/measure_lecture_switch_latency.py:197/:226`、`measure_e2e_asr_final_latency.py:225`：明文 token 改摘要。
- `tools/local-dev` mock（**仅存在于 main 分支**，本 dev 分支 worktree 无此目录）：其 WS 校验仍比对明文 `dev_token`（`app.py:80-89`）、`POST /v1/auth/token` 返回明文（`:69-73`）——**合入 main 后需同步改造**为摘要比对，否则 QA mock 链路在摘要客户端下全部 401。

**手工验证**
- curl 示例：

```bash
TOKEN=$(python3 -c "import hashlib; print(hashlib.sha1(('dev-livachat-token' + 'LivaChat').encode()).hexdigest())")
curl -k "https://host:8080/healthz_token?token=$TOKEN&device_id=$(uuidgen)"
```

- 真机：设置页填 secret → 检测服务 → 对话（WS）/相册/音频播放/保存 → 全部通；负例：token 用明文旧值 → 全端点 401（硬切换）。
- 交叉验证：Kotlin 派生函数与 Python 公式对同一 secret 输出同一摘要（固定向量单测）。

**文档更新清单**（实现提交时同步）：`docs/server_design.md`（§9.1 改述）、`docs/ws-protocol-design.md`（:18/:23/:28/:722/:862）、`docs/setting_server_address.md`（:34-35/:81/:86/:117）、`docs/bff_service_interfaces.md:36`、`docs/server_dev.md:546`、`docs/runbooks/troubleshooting.md:12-18`（「client token 与 APP_AUTH_PASS 匹配」表述失效）、`docs/install_design.md`（APP_AUTH_PASS 相关段落核查）、mylife/android 相关文档中的 curl/wscat 示例。

## 7. 实现触点清单（汇总）

**服务端**：`services/gateway/auth.py`（唯一核心改动）；`livachat.sh:142-159`（注释）。

**客户端**：新增 `wireToken()` 派生函数（1 处）；接入 2 处——`AppModule.kt:52-56`（装配 LivaChatConfig，单点）、`ServiceProbe.kt:41-47`（草稿派生）；删死代码——`AppModule.kt:77-84`、`ConversationViewModel.kt:58/:61`、12 个 ConversationViewModel 测试的 `mylifeUrl` 传参；`LivaChatConfig.kt:14-15` 注释更新。**SDK / MylifeApi / MylifeRepository 零改动**。

**测试**：§6 清单。**脚本/mock**：§6 清单。

## 8. 决策记录（已定）

- `livachat.sh` 字符集门禁：**不放松**——保留现有校验，仅更新已失效的注释说明（§5）。
- `mylifeUrl/timeMachineUrl` 死代码：**删除**（含明文拼装点及连带测试传参，§4.2）。
