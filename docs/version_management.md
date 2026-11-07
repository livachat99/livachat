# 版本管理：server/Android/lvdata-mcp 版本源、client_version 带 versionCode、session.ready 携带 server_version

> 状态：chat server、协议与 lvdata-mcp 单一来源均已实施（未提交，2026-10-17）
> 关联：`docs/ws-protocol-design.md` §2.2.1 / §2.3.1、
> `packages/api-schema/common.json`、`packages/api-schema/c2s/session-start.json`、
> `packages/api-schema/s2c/session-ready.json`、`services/app/application.py`
> 术语：需求中的 `session.read` 按 `session.ready` 理解（S2C 会话就绪消息）。

评审结论（2026-10-17）：

- `client_version` **保留 `android-` 平台前缀**，格式 `android-<versionName>.<versionCode>`
  （用户确认）。
- 其余按本文推荐执行：第 4 段 schema 可选、后端先发且全副本更新后再发 app、
  `server_version` 可选但服务端恒发送、双端只携带不处理。
- 发布必须遵守 §6 的顺序与回滚约束。
- lvdata-mcp 追加统一版本源（§10）：采用 **A 方案（pyproject 为唯一源、运行时读包元数据）**；
  用户已决定 lvdata-mcp 必须以安装形态运行，不再支持源码直跑/未安装。

## 1. 背景与目标

当前协议：

- `session.start.config.client_version`（C2S，必填）由客户端上报；
  服务端解析后仅落 `devices.client_version`，不做兼容性判断。
  Android app 目前上报硬编码的 `"android-1.0.0"`（`AppModule.kt:58`），
  与实际构建的 `versionCode`（写本文时为 130，随提交递增）无关，
  服务端无法区分安装包构建。
- `session.ready.payload`（S2C）只含
  `session_id / session_restored / server_time_ms / keepalive_interval_s`，
  不回传服务端版本。

目标：

| # | 目标 |
|---|------|
| G1 | Android app 的 `client_version` 从构建信息派生（`versionName` + `versionCode`），格式 `android-<versionName>.<versionCode>`，如 `android-1.0.0.130` |
| G2 | 协议 `ClientVersion` 允许第 4 段构建号；服务端原样接收与存储，不解析、不校验版本语义 |
| G3 | `session.ready.payload` 新增 `server_version`，服务端每次 ready 必填（初始会话与 resume 均携带） |
| G4 | 双端对 `server_version` 只「携带」：服务端只写入字段，客户端只解析保留；不做版本比较、拦截、提示等处理逻辑 |
| G5 | 服务端自身版本号保持单一来源（`pyproject.toml`），不在协议层再引入常量 |

非目标（本期不做）：

- 不做客户端/服务端版本兼容性校验、升级提示或灰度分支；
- 服务端不拆解 `client_version` 的第 4 段、不新增 `version_code` 列；
- 不把 `server_version` 暴露到 Android `LivaChatListener` 回调（SDK 内部 DTO 携带即可）；
- 不持久化 `server_version`，不写日志；
- 不引入服务端自动构建号（`setuptools-scm`），版本值仍由 `pyproject.toml` 手工维护；
- 不新增 HTTP `/version` 接口；
- iOS 端本期不实施（`apps/ios` 为空），schema 仅为其预留可选第 4 段。

## 2. Android client_version 统一（G1/G2）

### 2.1 目标格式

```
android-<versionName>.<versionCode>
例：android-1.0.0.130
```

- 保留 `android-` 平台前缀（wire 现有约定，schema 依赖）。
- 第 4 段固定为 Android `versionCode`（单调递增整数），不补零、不加其它修饰。
- `versionName` 维持 `x.y.z` 纯数字形态（当前 `1.0.0`）；若未来引入预发布后缀，
  需另行修订 schema pattern。

### 2.2 单一来源与实现

`apps/android/LivaChatApp/build.gradle.kts` 已是 app 版本的唯一来源
（`versionCode = 130` / `versionName = "1.0.0"`）。改动：

1. 主 app 开启 BuildConfig 生成（AGP 8.5 默认关闭）：

   ```kotlin
   buildFeatures {
       compose = true
       buildConfig = true   // 新增
   }
   ```

2. `AppModule.kt` 不再硬编码，改为由 BuildConfig 派生：

   ```kotlin
   import dev.livachat.app.BuildConfig

   clientVersion = androidClientVersion(BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE),
   ```

3. 为避免同一格式字符串散落多处，在 app 模块内提供单点格式化函数：

   ```kotlin
   // apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/di/ClientVersion.kt
   internal fun androidClientVersion(versionName: String, versionCode: Int): String =
       "android-$versionName.$versionCode"
   ```

   `AppModule` 调用 `androidClientVersion(BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE)`。

4. **格式护栏单测**（`src/test/.../di/ClientVersionTest.kt`）：
   - 示例值：`androidClientVersion("1.0.0", 130) == "android-1.0.0.130"`；
   - 真实构建值：用 `BuildConfig.VERSION_NAME` / `BuildConfig.VERSION_CODE`
     生成一次，断言匹配 `^android-\d+\.\d+\.\d+\.\d+$`。

   第二条是必要的：Android 侧不做 pattern 校验，若未来把 `versionName`
   改成 `1.0.0-beta` 之类，构建和单测示例值都不会失败，直到线上 app 的
   `session.start` 被服务端按 `ClientVersion` 拒掉（error 1006）。
   由此也约束发布约定：`versionName` 必须保持纯数字 `x.y.z`。

5. Mock app（`LivaChatAppMock`）同样改为读取自身 BuildConfig
   （其 BuildConfig 已开启），替换 `ConversationViewModel.kt:87` 的
   `"android-2.0.0"`，保持一致；它不参与真实部署。
6. SDK 不改：`LivaChatConfig.clientVersion` 仍由 app 传入，SDK 原样放进
   `session.start.config.client_version`（`LivaChatSDK.kt:146`）。
7. `LivaChatConfig.clientVersion` 的 KDoc 示例更新为新格式。

### 2.3 Schema 变更

`packages/api-schema/common.json` 的 `ClientVersion`：

```json
"ClientVersion": {
  "type": "string",
  "pattern": "^(ios|android)-\\d+\\.\\d+\\.\\d+(\\.\\d+)?$",
  "description": "Client version with platform prefix. Android: 'android-<versionName>.<versionCode>' (e.g. 'android-1.0.0.130'); the 4th segment is optional for backward compatibility with old clients. iOS may append its build number.",
  "examples": ["android-1.0.0.130", "ios-1.0.0"]
}
```

- 第 4 段**可选**：老版本 app 仍上报 `android-1.0.0`，服务端升级后不得拒绝它们。
- 新 app 始终发送 4 段；可选性只用于兼容存量客户端。
- 重新生成 Python / Kotlin DTO（`ClientVersion` 在 Python 侧是带 pattern 的
  `Annotated[str, StringConstraints(...)]`；Kotlin 侧仍为 `String`，无类型变化）。

### 2.4 服务端行为

- 无代码改动：`session.start` 解析、`SessionContext.client_version`、
  `devices.client_version` 全部按原字符串透传/落库（`orchestrator.py:556` →
  `chat_store.on_device`）。
- 无 DB migration：`devices.client_version` 已是 `TEXT`。
- `services/scripts/measure_*.py` 里的 `android-1.0.0` 仍合法；仅需顺带更新
  pattern 注释（非功能改动）。

## 3. session.ready 携带 server_version（G3/G5）

### 3.1 版本号来源（已实施）

`services/pyproject.toml` 的 `[project].version` 是唯一来源，运行时通过
`importlib.metadata.version("livachat-server")` 读取。

- `services/shared/version.py` 定义 `SERVER_VERSION`，是唯一的运行时读取点；
- `services/app/application.py` 的 `FastAPI(version=...)` 使用该常量；
- `services/gateway/codec.py` 同样从 `shared.version` 导入。不能反向依赖
  `app.application`：`app.application` → `app.routes` → `gateway.endpoint`
  → `gateway.codec` 会形成循环依赖；`shared/` 是两侧都可依赖的中立层；
- `services/tests/unit/app/test_app_version.py` 锁定 `SERVER_VERSION`、
  `services/pyproject.toml [project].version` 与安装元数据三者一致，并断言
  FastAPI `app.version == SERVER_VERSION`。

```python
# services/shared/version.py
from importlib.metadata import version

SERVER_VERSION = version("livachat-server")
```

已知取舍（与 A 方案相同）：editable 安装的 dist-info 版本在安装时固化，
修改 `pyproject.toml` 版本后需重跑 `pip install -e services`（或
`./livachat.sh install`）才会反映到 `SERVER_VERSION`。

### 3.2 Schema

`packages/api-schema/s2c/session-ready.json` 的 `payload.properties` 增加：

```json
"server_version": {
  "type": "string",
  "minLength": 1,
  "description": "Server package version (PEP 440, e.g. \"0.1.0\" or \"0.1.0+20261017.ad2fa88\"). Informational only; clients must not gate behavior on it."
}
```

**不加入 `required`**：

- 新客户端 ↔ 旧服务端：字段缺失解析为 `None` / `null`，不会失败；
- 新服务端始终显式赋值（见 §5），由于序列化使用
  `model_dump_json(exclude_unset=True)`，显式传入的字段一定会出现在 wire 上；
- 与 `docs/ws-protocol-design.md` §5.3「新增字段必须向后兼容」一致。

不设正则 pattern：版本值允许 PEP 440 的 `+build` 后缀（如
`0.1.0+20261017.ad2fa88`），过严的 pattern 会限制后续构建号方案。

### 3.3 Wire 示例

```json
{
  "type": "session.ready",
  "seq": 1,
  "timestamp": 1712345678200,
  "payload": {
    "session_id": "sess_abc123def456ghijkl",
    "session_restored": false,
    "server_time_ms": 1712345678200,
    "keepalive_interval_s": 15,
    "server_version": "0.1.0"
  }
}
```

## 4. 代码改动清单

| 区域 | 文件 | 改动 |
|------|------|------|
| App 构建 | `apps/android/LivaChatApp/build.gradle.kts` | `buildFeatures` 增加 `buildConfig = true` |
| App 装配 | `apps/android/LivaChatApp/.../di/AppModule.kt:58` | `clientVersion` 改为 `androidClientVersion(BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE)` |
| 格式化函数 | `apps/android/LivaChatApp/.../di/ClientVersion.kt`（新增） | `androidClientVersion(versionName, versionCode)` 单点拼装 |
| App 单测 | `apps/android/LivaChatApp/src/test/.../di/ClientVersionTest.kt`（新增） | 示例值断言 + **真实 BuildConfig 值必须匹配 wire pattern**（防 `versionName` 非纯数字） |
| Mock app | `apps/android/LivaChatAppMock/.../ConversationViewModel.kt:87` | 同样读取自身 BuildConfig |
| SDK 注释 | `apps/android/LivaChatSDK/.../LivaChatConfig.kt:21` | KDoc 示例更新为 `android-1.0.0.130` |
| 公共 Schema | `packages/api-schema/common.json` | `ClientVersion` pattern 允许可选第 4 段，更新描述/示例 |
| S2C Schema | `packages/api-schema/s2c/session-ready.json` | payload 增加 `server_version`（可选 string），同步 examples |
| Python DTO | `packages/sdk-python/src/livachat_protocol/generated/` | 重新生成：`ClientVersion` pattern、`ServerSessionReadyPayload.server_version` |
| Kotlin DTO | `packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated/` | 重新生成：`ServerSessionReadyPayload.serverVersion` |
| 版本源 | `services/shared/version.py`（新增） | `SERVER_VERSION = importlib.metadata.version("livachat-server")` |
| 服务端装配 | `services/app/application.py` | 改为 `from shared.version import SERVER_VERSION`，删除本地 `APP_VERSION` |
| 协议编码 | `services/gateway/codec.py:104` | `make_session_ready` 构造 payload 时固定传入 `server_version=SERVER_VERSION`；调用方签名不变 |
| 会话编排 | `services/session/orchestrator.py:575` | 不改：仍调用 `make_session_ready(session_id=..., restored=..., ...)` |
| Mock server | `tools/local-dev/mock-services/livachat_mock_server/src/livachat_mock_server/protocol.py:76` | `build_session_ready` 填入 mock 版本（如 `"0.1.0"`） |
| Android 测试桩 | `LivaChatSDK/.../e2e/MockServerHarness.kt`、`LivaChatApp/.../e2e/MockServerHarness.kt`、`SessionManagerTest.kt:397`、`protocol-fixtures/session_ready.json` | 伪造的 `session.ready` JSON 增加 `server_version` |
| Android 解析测试 | `LivaChatSDK/src/test/.../protocol/MessageCodecTest.kt:129` | `decode SessionReady maps all fields` 增加 `payload.serverVersion == "0.1.0"` 断言（fixture 同步） |
| Android 运行时代码 | `SessionManager.handleSessionReady` | **不改**：字段已由生成的 `msg.payload.serverVersion` 携带，不做任何消费 |
| 协议文档 | `docs/ws-protocol-design.md` §2.2.1 / §2.3.1 | `client_version` 格式说明、`session.ready` 示例与字段表补 `server_version` |
| 协议版本 | `docs/ws-protocol-design.md` 头部、`packages/api-schema/README.md:5`、`common.json:5` | v1.1 → **v1.2**，补变更摘要（`client_version` 增加可选构建段、`session.ready` 增加 `server_version`）；本次为向后兼容变更，**不 bump `$id` URL** |
| 兼容注释 | `services/scripts/measure_*.py` | 更新 `ClientVersion` pattern 注释（`android-1.0.0` 仍合法） |

生成命令（产物禁止手改）：

```bash
python3 scripts/generate_python_api.py \
  --schema packages/api-schema \
  --out packages/sdk-python/src/livachat_protocol/generated

make generate-android-api
```

## 5. 双端行为

- Android app：`client_version` 完全由构建信息派生；增量构建时 `versionCode`
  变化自动反映，无需人工同步字符串。
- 服务端：`client_version` 原样存 `devices.client_version`；每次 `session.ready`
  （新会话与 resume）由 `WireCodec` 写入 `server_version`，值在本进程内恒定。
- Android SDK：生成的 DTO 解析 `server_version`；`SessionManager` 与
  `LivaChatListener` 不读取、不下发，行为与现状完全一致。
- Python SDK / e2e client / mock server：DTO 中可读 `server_version`，无处理逻辑；
  mock server 收到新格式 `client_version` 依赖重新生成后的 Python DTO 通过校验。

## 6. 兼容性与发布顺序

| 组合 | 结果 |
|------|------|
| 新 app + 旧服务端 | **不兼容**：旧服务端 `ClientVersion` pattern 只允许 3 段，会拒绝 `android-1.0.0.130`，`session.start` 解析失败 |
| 旧 app + 新服务端 | 兼容：第 4 段可选，`android-1.0.0` 仍合法 |
| 新服务端 + 旧 Android SDK | Kotlin Json 配置 `ignoreUnknownKeys = true`，未知字段忽略，正常 |
| 新服务端 + 旧 Python SDK | `ServerSessionReadyPayload` 为 `extra="forbid"`，会因未知字段解析失败。Python SDK 在本仓库内与服务端同批更新；若存在仓库外消费者，需先发布 SDK 再上服务端 |
| 新客户端 + 旧服务端（server_version） | schema 中字段可选，解析为 `null`，正常 |

**发布顺序（必须）**：先发布后端（schema + 生成的 Python DTO + server_version），
再发布 Android app。禁止 app 先于服务端上线，否则新 `client_version` 会被旧
pattern 判为非法消息（error 1006 `invalid JSON`）导致会话无法建立。

发布粒度要求：

- 「后端已发布」指**全部副本**完成滚动更新。K8s 滚动期间新旧 pod 并存，
  新 app 命中旧 pod 时同样拿不到 `session.ready`；因此 app 发布必须等待
  后端 rollout 完全结束，而不是后端发布流水线一结束就放行。
- 后端镜像必须包含同一次提交生成的 `livachat-protocol` wheel
  （`infra/docker/server.Dockerfile` 从仓库源码构建，正常满足）；
  若走其它部署方式，需确认没有沿用旧 protocol 包。
- 新 app 上线前可用新格式跑一次真实握手（§7），不要只依赖单测。
- **回滚约束**：后端回滚到旧版本会使已发布的 4 段 `client_version` app
  全部无法建立会话；回滚必须连带回滚 app（或先停止新版本分发），
  不能只回滚后端。

## 7. 测试计划

服务端：

- `services/tests/unit/gateway/test_codec.py`：`make_session_ready` 输出 JSON
  包含 `payload.server_version == SERVER_VERSION`。
- `services/tests/integration/gateway/test_ws_handshake.py`：真实握手收到的
  `session.ready` 携带非空 `server_version`；**同时保留 `android-1.0.0`
  （老客户端格式）用例**，并新增 `android-1.0.0.130` 用例——两者都必须
  建立会话。这是第 4 段「可选」的实际回归保障，不能把老用例直接改成新格式。
- `services/tests/unit/app/test_app_version.py`：
  `SERVER_VERSION == pyproject.toml [project].version == importlib.metadata.version("livachat-server")`，
  且 `create_app().version == SERVER_VERSION`。
  只比较前两者会退化为恒真（`SERVER_VERSION` 本身就取自安装元数据），
  必须读 pyproject 对照，才能发现“改了版本但忘了重装 editable”。
- e2e resume 场景（`services/tests/e2e_backend/scenarios/test_resume_success.py`）：
  resume 后的 `session.ready` 仍携带非空 `server_version`——覆盖「初始会话与
  resume 都携带」的实现承诺，不能只测首次握手。

协议 / 生成产物：

- `packages/sdk-python/tests/test_protocol_models.py` 增加三条：
  `android-1.0.0`（存量格式）通过；`android-1.0.0.130` 通过；
  `android-1.0.0.130.7`（5 段）被拒绝。
```bash
python3 scripts/generate_python_api.py --check \
  --schema packages/api-schema \
  --out packages/sdk-python/src/livachat_protocol/generated
make check-android-api-generated
```

客户端 / mock：

- `cd apps/android && ./gradlew :LivaChatApp:testDebugUnitTest`：
  `ClientVersionTest` 的示例值断言 + 真实 BuildConfig 值 pattern 断言。
- `./gradlew :LivaChatApp:assembleDebug`：确认 BuildConfig 生成可用。
- `make mock-server-test`：mock server 的 ready 消息包含非空 `server_version`，
  且能接受 `android-1.0.0.130` 的 `session.start`。
- `cd apps/android && ./gradlew :LivaChatSDK:testDebugUnitTest`：
  `MessageCodecTest` 的 fixture 断言 `payload.serverVersion == "0.1.0"`。

手工 e2e：

1. 启动本地 server，安装新 app，确认会话建立；
2. 抓 `session.ready` 原始帧确认 `server_version` 存在；
3. 查 chat store `devices.client_version`，应为
   `android-<versionName>.<该次构建的 versionCode>`（写本文时为
   `android-1.0.0.130`；实施提交时按约定递增，以实际构建为准）；
4. 下次提交递增 `versionCode` 后重新构建安装，确认上报值随之变化（该验证动作按
   「versionCode 仅在提交时递增」的约定安排，不在未提交迭代中改动 versionCode）。

## 8. 实施顺序

1. 修改两份 schema（`common.json`、`session-ready.json`），重新生成 Python/Kotlin DTO，
   跑双端 `--check`；
2. 新增 `services/shared/version.py`，迁移 `application.py` 与其测试；
3. 修改 `gateway/codec.py` 填充 `server_version`；
4. 更新 mock server 与 Android 测试桩/fixture；
5. 主 app 开启 BuildConfig、改为派生 `client_version`，补格式化单测；mock app 同步；
6. 补服务端单测、握手集成断言与 Python pattern 测试；
7. 更新 `docs/ws-protocol-design.md` §2.2.1 / §2.3.1，并把协议版本
   v1.1 → v1.2（同步 `packages/api-schema/README.md` 与 `common.json` 的
   “Synchronized with ... v1.x”描述）；
8. 运行服务端测试、mock server 测试、Android 单测；
9. 按「后端先、app 后」发布。

## 9. 备选方案与结论

- **client_version 不保留平台前缀（直接 `1.0.0.130`）**：否决，且已由用户
  于 2026-10-17 确认保留前缀。`android-`/`ios-` 是现有 wire 约定，且
  `devices.platform` 当前未填充（恒为 NULL）——`client_version` 前缀是
  服务端唯一可得的平台信号。需求中的 `1.0.0.xxx` 理解为
  `<versionName>.<versionCode>`，平台前缀保留。
- **第 4 段设为必填**：拒绝。老 app 存量用户会被新服务端拒绝，无法滚动升级。
- **SDK 内部读取 PackageManager 自动生成版本**：拒绝。SDK 应保持传输层职责，
  版本信息由 app 装配点注入；同时避免为测试/多 flavor 引入隐式依赖。
- **服务端拆分第 4 段单独存列**：拒绝。本次只要求传递；需要统计时再解析。
- **server_version 设为 required**：拒绝。会让新客户端在旧服务端上解析失败。
- **codec 每次直接调 `importlib.metadata`**：拒绝，集中在 `shared/version.py`。
- **`/healthz` 顺带返回版本**：超出本次需求（仅要求 session.ready 携带），拒绝。

## 10. 追加：lvdata-mcp 版本单一来源（已实施）

### 10.1 现状与决策

**改造前**，lvdata-mcp 有两处版本定义，需要手工保持同步：

| 位置 | 值 | 用途 |
|------|----|------|
| `tools/lvdata-mcp/pyproject.toml:7` | `0.1.0` | 包元数据（安装/构建） |
| `tools/lvdata-mcp/src/lvdata_mcp/__init__.py:3` | `VERSION = "0.1.0"` | 运行时版本 |

运行时全部取自 `VERSION`：

- MCP `initialize` 的 `serverInfo.version`：`server.py:63`（`mcp._mcp_server.version = VERSION`）
- 运维接口 `/api/status` 的 `service.version`：`store.py:333`

一致性由 `tests/test_tools.py::test_version_single_source` 锁定；改造前该测试在
未安装时 `skip`，即允许“源码直跑”。

**决策（2026-10-17）**：lvdata-mcp 必须以**安装形态**运行，不再支持“未安装/源码直跑”。
因此采用与 chat server 相同的 **A 方案**：`pyproject.toml` 是唯一版本源，运行时读取
包元数据；未安装时 fail-fast，不做兜底。

**改造后（已实施）**：

- `pyproject.toml [project].version` 仍是唯一源（当前 `0.1.0`）；
- `__init__.py` 不再保留版本字面量：`VERSION = importlib.metadata.version("lvdata-mcp")`（现位于该文件第 5 行）；
- `serverInfo.version` 与 `/api/status` 的取值路径不变，仍统一使用 `VERSION`；
- `test_version_single_source` 同时对照 `pyproject.toml [project].version` 与安装元数据，且不再 skip；
- 未安装时 `import lvdata_mcp` 抛 `PackageNotFoundError`。

### 10.2 目标

- 消除 `pyproject.toml` 与 `__init__.py` 的手工同步；
- 运行时 `/api/status`、MCP `serverInfo.version` 的值与行为不变；
- 安装成为运行/测试的硬前提；未安装时必须明确失败，而不是静默回退。

### 10.3 方案 A（已确认）：pyproject 为唯一源，运行时读包元数据

```python
# tools/lvdata-mcp/src/lvdata_mcp/__init__.py
from importlib.metadata import version

VERSION = version("lvdata-mcp")
```

- `pyproject.toml` 的 `version = "0.1.0"` 保持不变，成为唯一版本源；
- `__init__.py` 删除字面量，不再维护第二份版本；
- `server.py` / `store.py` / `/api/status` / MCP `serverInfo` **零改动**；
- **不加 fallback**：未安装时 `import lvdata_mcp` 直接抛 `PackageNotFoundError`，
  与“必须安装”的决策一致；
- 升版本流程：只改 `pyproject.toml`，然后重跑 `pip install -e tools/lvdata-mcp`
  刷新 editable 安装的 dist-info（与 chat server 相同）。

### 10.4 对源码直跑的影响与处理

- `tests/test_tools.py::test_version_single_source`：删除 `PackageNotFoundError`
  的 skip 分支，改为无条件断言
  `VERSION == pyproject 声明版本 == importlib.metadata.version("lvdata-mcp")`。
  只比较后两者会退化为恒真（`VERSION` 本身就取自安装元数据），必须读
  `pyproject.toml` 对照，才能发现“改了版本但忘了重装 editable”。
  未安装时整个测试进程会在 import 阶段失败，这符合新前提。
- 同步清理历史文档中的“未安装则 skip”表述：
  `docs/data/lvdata_mcp_tools.md:358`（§7 实施记录）与 `:1026/:1035`（§14 D3）
  已改为“安装是前提；测试无条件断言”；原 `test_tools.py` 的 skip 分支已删除。
- 若仍有文档/流程使用 `PYTHONPATH=src` 直接运行，需要改为先安装
  （`.venv/bin/pip install -e tools/lvdata-mcp`）再运行；检查
  `tools/lvdata-mcp/README.md` 与 `docs/data/lvdata_mcp_tools.md` 中的相关表述。
- 现有正式入口均已要求安装：`livachat.sh` 安装 editable、systemd 使用
  `/opt/lvdata-mcp/.venv/bin/lvdata-mcp`、README 使用 `.venv/bin/lvdata-mcp`。

### 10.5 改动清单

| 文件 | 改动 |
|------|------|
| `tools/lvdata-mcp/src/lvdata_mcp/__init__.py` | 删除 `VERSION = "0.1.0"`，改为 `VERSION = importlib.metadata.version("lvdata-mcp")` |
| `tools/lvdata-mcp/pyproject.toml` | 不改，保持 `version = "0.1.0"` 作为唯一版本源 |
| `tools/lvdata-mcp/tests/test_tools.py` | `test_version_single_source` 删除未安装 skip，改为硬断言；额外读取 `pyproject.toml` 对照声明版本，避免运行时常量与元数据自比恒真；更新 docstring（安装是前提） |
| `tools/lvdata-mcp/README.md` / `docs/data/lvdata_mcp_tools.md` | 如出现“源码直跑”或版本维护说明，改为“先安装 editable / 改 pyproject 后重装” |
| `tools/lvdata-mcp/src/lvdata_mcp/server.py` / `store.py` | 不改 |

### 10.6 兼容性与发布

- 运行时行为、端口、接口、`serverInfo.version` 值均不变；本轮版本仍为 `0.1.0`。
- 不涉及 chat server/Android 的发布顺序；可独立实施、独立提交。
- 部署/本地启动链路已经安装包，无迁移动作；仅失去“未安装直接 import”的开发方式。
- wheel 的 METADATA 版本直接来自 pyproject，无构建期动态解析。

### 10.7 验证

```bash
# 安装是前提
.venv/bin/pip install -e tools/lvdata-mcp
.venv/bin/python -c "from lvdata_mcp import VERSION; print(VERSION)"  # 0.1.0
.venv/bin/pip show lvdata-mcp | grep Version                         # 0.1.0

# 单一来源与 MCP 元数据测试
.venv/bin/python -m pytest tools/lvdata-mcp/tests/test_tools.py \
  -k "version_single_source or initialize_metadata" -q
```

负向检查（可选，确认 fail-fast）：在没有安装元数据、也没有本地
`*.egg-info` 的干净路径下，
`PYTHONPATH=tools/lvdata-mcp/src .venv/bin/python -S -c "import lvdata_mcp"`
应抛出 `PackageNotFoundError`（`-S` 跳过 site-packages；若源码树残留
`egg-info`，`importlib.metadata` 仍可能发现它，需在临时副本中验证）。
正常 venv（已安装）下不应触发。

构建校验：`uv build --wheel --out-dir /tmp/lvdata-mcp-wheel tools/lvdata-mcp`，
METADATA 的 `Version` 来自 pyproject，应为 `0.1.0`。
