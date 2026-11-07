# MyLife Android Native 前端设计（Phase 1 · Final）

> **Status**: Final（实施契约）
> **Date**: 2026-08-19
> **Supersedes**: `mylife-frontend-android-k3.md`（基线）、`mylife-frontend-android-d4.md`
> （合并评审结论：K3 的事实精度与依赖治理 + D4 的 AudioFocus/下拉刷新/双源一致性
> 风险/insets 等增量；两稿中已被修正的表述以本文为准）
> **Based on**: [`docs/mylife/mylife-design.md`](mylife-design.md)（BFF/API 契约与领域模型，
> 本文不重复，仅引用）+ `apps/web/mylife/`（Web 实现，交互/视觉参照物）。
> 本文全部结论经仓库代码逐条核对，锚点见 §10。
> **Scope**: 在既有 `LivaChatApp` 模块内，用 Jetpack Compose 原生实现 MyLife 三视图
> （Timeline / Graph / Replay），消费既有 BFF `/api/mylife/*`，替代当前 WebView
> "Time Machine" 入口。

---

## 1. 背景与目标

### 1.1 背景

MyLife Phase 1 已交付：BFF 只读查询面（`services/bff/`，挂载 `/api/mylife`）+
React Web SPA（`apps/web/mylife/`，Timeline/Graph/Replay 三视图）。

Android App **当前已通过 WebView 消费该 SPA**：`ConversationScreen` 的 Time Machine
入口 → `WebViewScreen(url = mylifeUrl)`（`mylifeUrl` 由 `AppModule.provideMylifeUrl`
从 `LivaChatConfig.serverUrl` 推导：`wss→https`，拼 `?token=`）。
（**2026-10-14 注**：该链路已作为死代码删除——mylifeUrl/timeMachineUrl 全仓无消费点，随认证改造一并移除，见 docs/server_auth.md §4.2；本段为历史形态记录。）

WebView 方案的问题：移动端触控/惯性滚动/手势体验弱于原生；星图 canvas 在 WebView
内帧率与捏合手感不佳；音频播放受 WebView 媒体策略限制；且深链接（Timeline 事件 →
Replay 定位）无法与 App 原生导航互通。

### 1.2 目标

- 三视图原生实现，**交互语义与 Web 版逐一对齐**（Web 版即 UX 契约）：
  Timeline（Scroll/Explore）、Graph（Explore/Zoom/Connect）、Replay（Play/Pause/Scrub）
- **零 BFF 改动**：完整复用 `/api/mylife/*` 契约与 `?token=` Phase 1 bypass 形态
- 复用 App 既有架构（Compose + Hilt + MVVM + StateFlow + UseCase 分层）与设计语言
- 深链接与 App 导航互通：Timeline 事件卡 → Replay 定位；Graph 实体 → Timeline 日期 /
  Replay 会话

### 1.3 非目标（Phase 1 不做）

- 不改 BFF / 分析管线 / recording（数据生产与理解层保持不变）
- 不做鉴权（沿用 `?token=` bypass，Phase 2 随 BFF `deps.py` 一起落地）
- 不做 Room 离线缓存（仅内存缓存；对齐 Web react-query 内存语义）
- 不做编辑/删除/上传（只读视图）
- 不做平板/折叠屏专项适配（手机竖屏优先；不引入 Web 版 ≥768px 桌面布局概念）
- 不删除 WebView 入口（Time Machine 按钮切换为原生页；WebViewScreen
  保留给聊天链接打开）
- 不做统一音频焦点协调器（Phase 1 用 §6.7 轻量互斥；协调器归 Phase 2）

---

## 2. 现状调研（代码事实，2026-08-19 核对）

### 2.1 Android App 技术栈与分层（已存在）

- 单 App 模块 `apps/android/LivaChatApp/`（+ SDK 模块 `LivaChatSDK`）；
  Kotlin 2.0 / AGP 8.5 / compileSdk 34 / **minSdk 26** / JDK 17
- UI：Jetpack Compose（BOM 2024.06.00）+ Material3 + navigation-compose 2.7.7；
  自定义暗色主题（`ui/theme/Color.kt`：Background `#0F172A` 等）+
  `Spacing`/`Elevation` CompositionLocal；`ModalBottomSheet` 已有使用先例
  （AudioRouteSheet / AvatarCustomizationSheet / LanguagePickerSheet）
- 架构：MVVM + Hilt 2.51（`@HiltViewModel` + `hiltViewModel()`），
  `data/`（Room + DataStore + repository/）→ `domain/`（usecase/ + model）→ `ui/`
  （screens/<feature>/：Screen + ViewModel + UiState，`StateFlow` 暴露）。
  既有四例：Chat/History/Session/Settings，MyLife 同构新增
- JSON：`kotlinx-serialization-json:1.7.1`（已在白名单）
- 测试：JUnit4 + mockk + turbine + robolectric；**mockwebserver 4.12.0 当前仅在
  `androidTestImplementation`**（`src/test` 不可用，§4 补 `testImplementation`）

### 2.2 现有 mylife 入口（已存在）

- `AppModule.provideMylifeUrl`（`@Named("mylifeUrl")`，`AppModule.kt:43-54`）：
  `URI(config.serverUrl)` → `wss→https / ws→http`，`$scheme://$host:$port/mylife?token=${config.token}`；
  serverUrl 当前硬编码 `wss://<lan-host>:8080/v1/ws/stream`（`AppModule.kt:28`）
- `ConversationViewModel.timeMachineUrl`（`ConversationViewModel.kt:46`）透传；
  `ConversationScreen.kt:219` `onNavigateToTimeMachine(viewModel.timeMachineUrl)` →
  `NavGraph` 路由 `Routes.WEBVIEW` → `WebViewScreen`
- **（2026-10-14 注）以上两条（provideMylifeUrl / timeMachineUrl）已删除**——无消费点死代码，
  随认证改造移除（docs/server_auth.md §4.2）；本小节为 2026-08-19 历史核对记录。
- `WebViewScreen` 安全基线：仅放行 http(s)；`onReceivedSslError` **proceed**
  （`WebViewScreen.kt:182-192`，产品决策 2026-08：dev 自签证书忽略 SSL 错误，
  风险已记录在案）
- 设备标识：`LivaChatConfig.deviceId`（`AppModule.kt:30` 注入，源头为
  `AppModule.getOrCreateDeviceId`——**注意该方法是 `private`**（`AppModule.kt:56-64`），
  不可直接复用；取值统一走 `LivaChatConfig.deviceId`）。该值即 WS 握手 `device_id`；
  服务端 recording 以它为 `device_uuid`
  （`services/session/omni_orchestrator.py:171 device_uuid=ctx.device_id`）——
  **App 的 deviceId 就是 lvdata 树中的 device_uuid**，可作为 BFF `device_id`
  过滤参数实现"只看本机数据"（D10）

### 2.3 依赖白名单约束（硬约束）

**仅白名单内依赖可加入 `LivaChatApp/build.gradle.kts`，任何超出清单的依赖必须先经过审批**。当前白名单内
**没有** HTTP 客户端、图片加载、音频播放库（OkHttp 4.12.0 仅以 `implementation`
存在于 SDK 模块内部——**`implementation` 不传递编译 classpath，App 模块对
`okhttp3.*` 编译不可见**，必须自引）。本设计的新增依赖清单见 §4，均需审批。

### 2.4 BFF API 契约（已实现，只读消费）

统一前缀 `/api/mylife`；全部 GET；成功响应带 `schema_version:1`；错误信封
`{"error":{"code","message"}}`（code ∈ not_found/not_analyzed/bad_request/
range_invalid/internal → 400/404/416/500，见 `services/bff/router.py`）。
`token` 与 `device_id` 由 `common/deps.py:get_principal` 统一以 Query 接收
（**所有端点均隐含接受 `?token=` 与 `?device_id=`**；token Phase 1 不校验）：

| 端点 | 用途 | 关键参数 |
|------|------|---------|
| `GET /overview` | 按本地日倒序的天 + session 卡片（含 stats/topics/summary/analysis 状态） | `timezone`（+ principal 的 `device_id`） |
| `GET /timeline` | 跨 session 合并事件流（5 类事件，按本地日分组） | `date_from/to, session_id, types(csv), limit≤500, cursor, timezone` |
| `GET /graph` | 实体关系图（6 类节点 + session_cooccur 边） | `date_from/to, types, focus, depth≤3, min_weight` |
| `GET /graph/entity/{id}` | 实体详情 + appearances（≤50，时间倒序） | — |
| `GET /replay/{session_id}` | 回放 cue 序列（chat/audio/frame/pause/job，`t_ms` 压缩时间轴） | `speed`（默认 8.0，1≤speed≤64；Web 未用，Android 同用默认值） |
| `GET /media/{device}/{session}/{kind}/{filename}` | OGG/JPEG 媒体（FileResponse 原生 Range；audio 支持 `?format=m4a`） | `format` |

DTO 字段权威定义：`services/bff/common/schemas.py`；TS 镜像：
`apps/web/mylife/src/api/types.ts`（注释"与 schemas.py"对齐）。**Android DTO 以
types.ts 字段名为准逐字段映射**（§5.2）。

契约注意点（来自 mylife-design.md 与代码）：

- `media_url` 是**相对路径**（`/api/mylife/media/...`，`replay/service.py:123`）——
  Web 同源直接用；**Android 必须拼绝对 URL**（§5.5）
- `timeline.json` 缺失 → replay 返回 404 `not_analyzed`；overview 中对应 session 以
  `analysis.timeline = pending/missing` 呈现"分析中"（前端轮询消化，Web 为 60s）
- agent_job 事件内容可变（镜像表单行最新状态），列表须容忍同一 key 内容更新
- emotion 英文枚举（happy/sad/angry/surprised/frustrated/neutral/other）、scene 小写
  枚举（desktop/browser/ide/terminal/chat/document/video/other）——直接展示原文，
  不做本地枚举硬编码
- `apps/ocr/actions` 等附字段在 types.ts 中为 `unknown[]`——宽松解析（§5.2）

### 2.5 Web mylife 实现要点（交互契约参照物）

`apps/web/mylife/`（React 19 + react-router HashRouter + @tanstack/react-query +
Tailwind + d3-force/zoom 自绘 canvas + framer-motion）：

- **API 封装**：`api/client.ts:15-28`——BASE=`/api/mylife`，token 从 sessionStorage
  读取并自动附加 `?token=`；错误解析为 `ApiError(status, code, message)`
- **Timeline**：顶部类型过滤 chips（全部/对话/语音/画面/窗口/任务）；HeatStrip 每日
  密度条（点击定位）；overview 按月分组章节（大字年月分隔 + 日卡片：stats 徽章 +
  summary + topics，pending 显示"分析中…"脉冲点，点击跳 Replay）；事件流无限滚动
  （IntersectionObserver sentinel `rootMargin: 600px` + cursor），月大字分隔 +
  **日粘性头**；5 类事件卡片（chat 气泡左右对齐 / audio 卡含内联 `<audio>` + 情绪
  chip / video 卡含帧图 + scene chip / window 卡静态·活跃 / agent_job 状态徽章卡）；
  事件 key 为 `${ts_ms}-${i}`（容忍 agent_job 内容可变）；事件点击 →
  `#/replay?session=<id>&t=<ts_ms>`
- **Graph**：d3-force 自绘 canvas 星图（节点色按 kind：topic=金/emotion=粉/app=蓝/
  event=白/object=紫/scene=青；半径 `scaleSqrt(weight)` 4→22px；link distance 70
  strength 0.4；charge `-60-weight*6`；collide `r+6`；alphaDecay 0.05）；滚轮/捏合
  zoom（k∈[0.2,4]）；拖拽节点（fx/fy）；位移 <6px 判定点击；点击 → ego 聚焦
  （邻居高亮其余淡出）+ 底部实体详情抽屉（appearances → 跳 Timeline 日期；
  event 节点 → 直接跳 Replay）；顶部 kind 图例开关 + 日期范围 + min_weight +
  移动端"列表/星图"切换（Top 50 权重列表）
- **Replay**：无 `?session=` 时为会话选择列表（数据同 overview，pending 不可点）；
  播放器：`buildSegments(cues, skipPause)`（段边界=后端 `t_ms`，时长取自身
  `duration_ms` 与下一段起点较小值，缺省 2500ms；skipPause 时 pause 段时长置 0）、
  `cueIndexAt`、`locateCueTs`（原始 ts_ms → 最近 cue 的 `t_ms`）；50ms tick 推进 ×
  rate（1/2/4x）；Stage：frame cue Ken Burns（1.15→1，6s easeOut）+ caption、chat
  cue 气泡浮现、audio cue 播 `media_url` + 转写字幕 + 情绪 chip（audioError 时单段
  重置重试语义）、pause cue 呼吸动画（🌙 沉默期）、job cue 状态卡；Stage 内容
  `key="${type}-${t_ms}"`（cue 切换动画重置）；Player：进度条 scrub / 变速 /
  跳过静默开关 / 当前 cue 原始时刻时钟（`ts_utc` 本地时 HH:MM:SS）
- **数据获取**：`useOverview` 在任一 `analysis.timeline==pending` 时 60s 轮询；
  `useTimeline` infinite query（limit=200）；`useGraph` keepPreviousData

### 2.6 平台媒体能力事实（minSdk 26）

- **OGG Opus**：Opus 解码 Android 5.0+（平台 MediaCodec，官方支持表容器含
  Ogg (.ogg)）；`.opus` 扩展名 Android 10 起被系统识别。minSdk 26（Android 8.0）
  ≥ 所需基线，平台解码链路在全部目标设备可用。但 dev 后端为**自签 HTTPS**：
  平台 `MediaPlayer` 无法自定义 TLS 校验（直连自签证书失败），需走可注入
  OkHttp 的播放器（§4 D3）
- **JPEG**：平台原生（BitmapFactory），任意 HTTP 客户端下载后解码即可

### 2.7 结论

- BFF 契约稳定、字段齐全，Android 端是**纯客户端工作**，无服务端改动
- 唯一硬缺口：App 模块无 HTTP/图片/音频播放依赖（白名单约束）→ §4 审批清单
- Web 版交互可直接翻译为 Compose 语义；力导向图需自绘（无现成 Compose 库）

---

## 3. 总体架构

```
┌──────────────────────────── LivaChatApp (既有模块内新增) ───────────────────────────┐
│ UI (Compose)                                                                      │
│  ui/screens/mylife/                                                               │
│   MylifeHomeScreen (Scaffold + 底部 NavigationBar: Timeline/Graph/Replay 列表)     │
│   ├─ timeline/  TimelineScreen + TimelineViewModel + 组件(HeatStrip/DaySection/    │
│   │             EventCard×5)                                                      │
│   ├─ graph/     GraphScreen + GraphViewModel + LifeGraphCanvas(自绘力导向)          │
│   │             + EntityDetailSheet + EntityTopList                               │
│   └─ replay/    ReplaySessionListScreen（tab 内）/ ReplayScreen（独立路由）          │
│                 + ReplayViewModel + ReplayStage + ReplayPlayerBar                  │
│                 + ReplayTimeline(纯逻辑) + ReplayAudioPlayer                       │
│        │ uiState: StateFlow                                                       │
│ Domain                                                                            │
│  domain/usecase/MylifeUseCase (薄封装, 对齐 HistoryUseCase 模式)                    │
│        │                                                                          │
│ Data                                                                              │
│  data/repository/MylifeRepository (内存缓存 + pending 60s 轮询 + 媒体 URL 绝对化)    │
│  data/remote/mylife/ MylifeApi (OkHttp + kotlinx.serialization DTO)               │
│        │ HTTPS (dev 自签 → trust-all client, §4 D4)                                │
└────────┼──────────────────────────────────────────────────────────────────────────┘
         ↓
   services/bff  /api/mylife/*（既有，零改动）
```

依赖方向：`ui → domain → data`，与既有 `HistoryFeature` 完全同构；
`ui/screens/mylife/*` 不 import `ui/screens/conversation` 内部。

### 核心决策

| # | 决策 | 理由 |
|---|------|------|
| D1 | **原生 Compose 三视图，WebView 入口降级为备选** | 原生触控/手势/导航深链体验；WebViewScreen 保留给聊天消息链接（既有职责不变） |
| D2 | **零 BFF 改动，DTO 逐字段对齐 `api/types.ts`** | BFF 契约已定稿并经 Web 验证；Android 是第二个消费方，反向校验契约 |
| D3 | **OkHttp + kotlinx.serialization，不引 Retrofit** | 6 个 GET 端点，suspend fun + `HttpUrl` 构造即可；白名单外依赖越少审批成本越低（KISS）。注意 SDK 的 OkHttp 是 `implementation`，App 必须自引（§2.3） |
| D4 | **dev 自签证书：独立 `MylifeOkHttpClient`（trust-all + hostnameVerifier 放行），同一实例注入 API / Coil / ExoPlayer** | 自签 TLS 下，**Coil 加载图片与 ExoPlayer 拉流必须用同一 trust-all client**，否则媒体全部握手失败；与 SDK `ConnectionManager`（WS）及 `WebViewScreen`（SSL proceed）的既有产品决策一致；生产环境收敛为系统校验（§9 风险 2） |
| D5 | **图片加载用 Coil（coil-compose 2.x）** | 帧图懒加载/内存+磁盘缓存/占位图是标配需求；coil-base 传递依赖 okhttp 4.12.0 与 D4 钉版一致，自建 client 经 `ImageLoader.Builder.callFactory` 注入共享；自研加载器是重复造轮 |
| D6 | **音频播放用 Media3 ExoPlayer + media3-datasource-okhttp** | OGG Opus 平台解码 minSdk 26 可用（§2.6），但需复用 D4 client 过自签 TLS；ExoPlayer 自带 Ogg extractor，seek/变速语义完备；`setAudioAttributes(handleAudioFocus=true)` 自带焦点管理（§6.7） |
| D7 | **Graph 自绘 Compose Canvas + 轻量 Kotlin 力导向模拟（不引库）** | 对齐 Web 版"d3-force 自绘 canvas"决策（移动端性能）；d3 语义（link/charge/center/collide）~200 行 Kotlin 可复刻，无白名单外依赖 |
| D8 | **仅内存缓存（MylifeRepository 内 LruCache/Map），不落地 Room** | 对齐 Web react-query 内存语义；BFF 响应只读且廉价（文件+JSON 映射）；离线缓存归 Phase 2 |
| D9 | **Replay 播放器逻辑为纯 Kotlin 类 `ReplayTimeline`（VM 外可测）** | 直接移植 Web `playerTimeline.ts`（buildSegments/cueIndexAt/locateCueTs），对齐段边界语义；tick 由 ViewModel `viewModelScope` 驱动 |
| D10 | **`device_id` 过滤默认关闭，设置项预留** | App `LivaChatConfig.deviceId` == recording device_uuid（§2.2），BFF `get_principal` 已接受 `?device_id=`；但单用户 dev 场景跨设备查看更有用；`MylifeRepository` 支持注入可选 deviceId |
| D11 | **单 NavHost、两条 App 级路由；不嵌套 NavHost** | 深链（Replay 定位 / Timeline 日期定位）用 App 级路由显式表达；三 tab 状态 `rememberSaveable` + ViewModel 挂宿主 backstack entry，避免嵌套 NavHost 的回退栈/状态恢复复杂度（合并 K3 深链显式性与 D4 KISS） |
| D12 | **m4a 降级内建于 P3（播放失败重试一次），不做探测式预降级** | 平台 OGG 链路 minSdk 26 全覆盖（§2.6），无需预判；但厂商 ROM extractor 差异真实存在，失败时 `?format=m4a` 重试一次成本极低（仅换 URL），对齐 Web `Stage.tsx` audioError 单段重置语义 |

---

## 4. 新增依赖审批清单（`LivaChatApp/build.gradle.kts`）

> 以下均超出现行白名单（见 `apps/android/LivaChatApp/build.gradle.kts`），**实施前需审批**。
> 版本钉 minor（与仓库钉版惯例一致）。

```kotlin
// HTTP（版本与 LivaChatSDK 内部 okhttp 4.12.0 对齐，避免双版本）
implementation("com.squareup.okhttp3:okhttp:4.12.0")

// 图片加载（Coil 2.x。注意：coil-network-okhttp 是 Coil 3.x 产物，2.x 不存在；
// Coil 2.x 的 coil-base 直接传递依赖 okhttp 4.12.0，与上方钉版一致；
// 共享 client 经 ImageLoader.Builder.callFactory 注入，见 §5.4）
implementation("io.coil-kt:coil-compose:2.6.0")

// 音频播放（Media3，OGG Opus + Range seek；datasource 复用自定义 OkHttpClient）
implementation("androidx.media3:media3-exoplayer:1.4.1")
implementation("androidx.media3:media3-datasource-okhttp:1.4.1")

// 单元测试：mockwebserver 当前仅 androidTestImplementation（§2.1），
// src/test 契约测试需要显式补齐
testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
```

**版本一致性（已核对 Maven POM，回应"多份 OkHttp 是否冲突"的审批疑问）**：
`coil-base:2.6.0` 与 `media3-datasource-okhttp:1.4.1` 均以 compile-scope 依赖
`okhttp:4.12.0`，与 App 显式钉版、SDK 内部版本完全一致——这四个声明指向**同一
group:artifact 坐标**，Gradle 全依赖图只解析一个版本（默认 highest wins），最终
APK 内仅一份 OkHttp；桥接 artifact 是适配器而非 OkHttp 副本。okio 同理（解析为
3.8.0 一份）。即使未来传递版本漂移，OkHttp 4.x 主版本内二进制兼容，升级解析安全。
落地验证：`./gradlew :LivaChatApp:dependencies --configuration debugRuntimeClasspath`
中 okhttp 应仅出现 4.12.0 一次，且无 duplicate class。

**审批被拒时的兜底（不引白名单外依赖）**：OkHttp 不可省（无它则无 HTTPS API 通道；
SDK 的 `implementation` 不传递编译 classpath）。图片可自研
`RemoteImage`（OkHttp 下载 + BitmapFactory + LruCache + Compose `Image`，~120 行）；
音频可 OkHttp 下载到 `cacheDir` 后 `MediaPlayer` 播本地文件（牺牲流式 seek）。
兜底方案体验明显劣化，仅作备案。

---

## 5. 数据层设计

### 5.1 `MylifeApi`（data/remote/mylife/）

```kotlin
class MylifeApi @Inject constructor(
    private val client: OkHttpClient,          // @Named("mylife") trust-all client (D4)
    @Named("mylifeApiBase") private val baseUrl: HttpUrl,  // https://host:port/api/mylife
    private val tokenProvider: () -> String,   // LivaChatConfig.token
) {
    suspend fun overview(timezone: String? = null, deviceId: String? = null): OverviewDto
    suspend fun timeline(
        dateFrom: String? = null, dateTo: String? = null,
        sessionId: String? = null, types: String? = null,
        limit: Int = 200, cursor: String? = null, timezone: String? = null,
    ): TimelineDto
    suspend fun graph(
        dateFrom: String? = null, dateTo: String? = null, types: String? = null,
        focus: String? = null, depth: Int = 1, minWeight: Int = 1,
    ): GraphDto
    suspend fun entity(id: String): EntityDto
    suspend fun replay(sessionId: String, speed: Float? = null): ReplayDto
}
```

- 每个方法：`baseUrl.newBuilder()` 拼 path + `addQueryParameter("token", …)` +
  可选参数；`client.newCall(request).await()`——手写 `suspendCancellableCoroutine`
  封装 enqueue（~15 行），避免再加 `okhttp-coroutines` artifact
- 错误处理：非 2xx → 解析错误信封 → `MylifeApiException(status, code, message)`；
  JSON 解析失败同埋（对齐 Web `ApiError`）
- `Json { ignoreUnknownKeys = true }`（schema 演进容错，对齐 BFF"单行失败跳过"
  的宽松语义）。**`schema_version` 仅记录日志不做客户端硬校验**——Phase 1 宽松
  消费，版本错位由 BFF 保证向后兼容，Phase 2 再评估

### 5.2 DTO（@Serializable，逐字段对齐 types.ts / schemas.py）

`data/remote/mylife/dto/`：`OverviewDto / SessionItemDto / SessionStatsDto /
TopicItemDto / AnalysisStatusDto`、`TimelineDto / TimelineDayDto / TimelineEventDto`、
`GraphDto / GraphNodeDto / GraphEdgeDto / GraphStatsDto`、`EntityDto / EntityAppearanceDto`、
`ReplayDto / ReplaySessionInfoDto / ReplayCueDto`。

- 字段命名用 `@SerialName("ts_ms")` 等蛇形映射；可空性与 types.ts 一致
  （`summary: String?`、`media_url: String?` 等）
- 类型专属字段（chat.role/audio.emotions/video.caption/job.status…）全部可空，
  UI 按 `type` 分派读取（对齐 Web `TimelineEvent` 单 interface 形态，**不建
  sealed class**——BFF 直通映射字段集合稳定，sealed 反增解析复杂度）
- `apps/ocr/actions` 等 types.ts 中为 `unknown[]` 的附字段 →
  `List<JsonElement>? = null` 宽松保留，渲染时安全读取；**单字段解析失败不整包
  报错**（对齐 `LvdataStore.read_derived` 容错语义，`common/store.py:100-113`）
- domain 层直接复用 DTO（只读展示模型，无业务转换）——对齐
  mylife-design.md §4.2"DTO 直通映射"；若未来需本地加工再拆 domain model

### 5.3 `MylifeRepository`

```kotlin
@Singleton
class MylifeRepository @Inject constructor(private val api: MylifeApi) {
    suspend fun overview(refresh: Boolean = false): OverviewDto
    fun timelinePages(types: String?, sessionId: String?,
                      dateTo: String? = null): Flow<TimelinePageResult>  // 游标累页
    suspend fun graph(...): GraphDto          // LruCache(key=参数串, 5min)
    suspend fun entity(id: String): EntityDto
    suspend fun replay(sessionId: String): ReplayDto
}
```

- overview：内存缓存 + `hasPending` 时由调用方（VM）60s 轮询刷新（对齐 Web
  `useOverview.refetchInterval`）
- timeline：`dateTo` 入参支持"定位模式"（§6.4：HeatStrip/深链目标日超出已加载
  分页时，以 `date_to=目标日` 重置分页定向拉取）
- graph：`keepPreviousData` 语义由 VM 层保留上一份成功结果实现（§6.5）
- 媒体 URL 在 Repository 出口统一转绝对（§5.5），UI 零感知

### 5.4 baseUrl / token / deviceId 来源（复用既有推导）

`AppModule` 已有 `provideMylifeUrl`（`https://host:port/mylife?token=…`）。
新增 `di/MylifeModule.kt`：

```kotlin
@Provides @Singleton @Named("mylifeApiBase")
fun provideMylifeApiBase(config: LivaChatConfig): HttpUrl {
    // 与 provideMylifeUrl 同一推导规则（wss→https / ws→http / 端口保留）
    // $scheme://$host:$port/api/mylife
}

@Provides @Singleton @Named("mylife")
fun provideMylifeOkHttpClient(): OkHttpClient  // D4 trust-all + 10s timeout

@Provides @Singleton
fun provideCoilImageLoader(@ApplicationContext ctx: Context,
                           @Named("mylife") client: OkHttpClient): ImageLoader
// 实现：ImageLoader.Builder(ctx).callFactory { client }.build()
// （Coil 2.x API；OkHttpClient 实现 okhttp3.Call.Factory）
```

token = `LivaChatConfig.token`（dev 环境 `"dev-livachat-token"`）；deviceId =
`LivaChatConfig.deviceId`（§2.2：与 WS device_id / recording device_uuid 同值；
**不走 `AppModule.getOrCreateDeviceId`，该方法为 private**）（D10 预留，默认不带）。

### 5.5 媒体 URL 处理（Android 特有）

BFF 返回相对 `media_url`（`/api/mylife/media/<dev>/<sess>/<kind>/<file>`）。
Repository 出口统一 `baseUrl.resolve(media_url)` 转绝对并附带 `?token=`——
**图片（Coil）与音频（ExoPlayer）都经 D4 client 访问媒体**，同域同证书。
m4a 降级（`?format=m4a`）：默认直用 `.ogg`（§2.6）；播放失败时换 URL 重试一次
（D12，P3 内建于 `ReplayAudioPlayer`）。

### 5.6 SSL（dev）

D4 client 复刻 SDK `ConnectionManager.connectInternal` 的 trust-all 配置
（`SSLContext` + 全放行 `X509TrustManager` + `hostnameVerifier{true}`）。
该 client **仅**用于 mylife API/media 与 Coil/ExoPlayer，不进入 SDK WS 链路。
生产收敛见 §9 风险 2。

---

## 6. UI 层设计

### 6.1 目录结构

```
ui/screens/mylife/
├── MylifeHomeScreen.kt          # Scaffold + NavigationBar(3 tab)；tab 状态 rememberSaveable；
│                                # 三个 ViewModel 挂宿主 backstack entry（tab 切换不丢状态）
├── MylifeTheme.kt               # MylifeColors（§6.2）+ 局部 CompositionLocal
├── timeline/
│   ├── TimelineScreen.kt        # 过滤 chips + HeatStrip + overview 章节 + 事件流 + 下拉刷新
│   ├── TimelineViewModel.kt     # overview/timeline 加载、分页、pending 轮询、date 定位
│   ├── TimelineUiState.kt
│   └── components/
│       ├── HeatStrip.kt         # 每日密度条（Row + weight + 高度映射）
│       ├── OverviewSection.kt   # 年月大字分隔 + 日卡片（stats 徽章/topics/分析中）
│       ├── EventCard.kt         # 5 类事件卡片（when(type) 分派）
│       └── TypeFilterChips.kt
├── graph/
│   ├── GraphScreen.kt           # 控件条 + Canvas/列表切换 + 详情 BottomSheet
│   ├── GraphViewModel.kt        # 图数据/kind 过滤/日期范围/focus/keepPreviousData
│   ├── GraphUiState.kt
│   ├── LifeGraphCanvas.kt       # 自绘力导向星图（Canvas + 手势）
│   ├── ForceLayout.kt           # 纯 Kotlin 力导向模拟（D7，可单测）
│   └── EntityDetailSheet.kt     # ModalBottomSheet：属性 + appearances 列表
└── replay/
    ├── ReplaySessionListScreen.kt  # 会话选择（数据同 overview；host 内 Replay tab）
    ├── ReplayScreen.kt             # Stage + PlayerBar（独立路由 mylife/replay）
    ├── ReplayViewModel.kt          # 播放器状态机（tick/seek/rate/skipPause/定位）
    ├── ReplayUiState.kt
    ├── ReplayTimeline.kt           # 纯逻辑：buildSegments/cueIndexAt/locateCueTs（D9）
    ├── ReplayStage.kt              # cue 渲染（frame/chat/audio/pause/job）
    ├── ReplayPlayerBar.kt          # Play/Pause + Slider + 变速 + 跳静默 + 时钟
    └── ReplayAudioPlayer.kt        # ExoPlayer 封装（audio cue 联动 + m4a 重试）
```

### 6.2 主题与系统适配（Web 星空基调 → Compose）

Web `theme.css` 色板逐值移植为 `MylifeColors`（与 App 全局 `BrandIndigo` 体系
**并存不冲突**：MyLife 是独立"人生世界"子空间，沿用 Web 视觉契约）：

| token | 值 | 用途 |
|-------|----|------|
| abyss | `#0A0E1A` | 页面底色 |
| void | `#0D1220` | 顶栏/底栏/抽屉 |
| panel | `#141A2E` | 卡片 |
| line | `#232C47` | 描边/分隔 |
| star | `#E8ECFF` | 主文本 |
| dust | `#8B93B8` | 次文本 |
| gold | `#F5C86E` | 强调/topic 节点/粘性头 |
| pink | `#F28BB0` | emotion 节点/情绪 chip |
| blue | `#7DB4F5` | app 节点 |
| purple | `#B58CF5` | object 节点 |
| cyan | `#6FD6D0` | scene 节点/跳静默开关 |
| red/green | `#F56E6E`/`#6EF5A8` | job failed/done 状态 |

- `MylifeTheme` 内嵌 `LivaChatTheme`（复用 Spacing/Typography/Shapes），
  仅覆盖色彩；深空星点背景以 `BoxWithConstraints` + 少量 `Canvas` 圆点实现
  （对齐 Web `body::before` radial-gradient 星点，静态无动画）
- **触控目标 ≥44dp**（对齐 mylife-design.md §7.1 移动优先约束）；Graph 节点命中
  半径按 44dp 触摸目标扩展（§6.5）
- **`WindowInsets` 适配**：底部 NavigationBar 与 ReplayPlayerBar 处理
  `navigationBars` 安全区；顶栏处理 `statusBars`

### 6.3 导航与深链（D11：单 NavHost，两条 App 级路由）

`Routes.kt` 新增：

```kotlin
// 宿主：3 tab；可选 tab/date/session 参数用于 Timeline 定位深链
const val MYLIFE = "mylife?tab={tab}&date={date}&session={session}"
// 播放器：独立全屏路由（push 在宿主之上，返回即 pop）
const val MYLIFE_REPLAY = "mylife/replay?session={session}&t={t}"
```

> **2026-09-22 已变更（见 docs/android_timeline_hotslot.md §14 R1/R2/R3=C）**：`MYLIFE` 收敛为**无参** `"mylife"`、
> `MYLIFE_REPLAY` 删除；宿主为**单视图**（无 3 tab/底栏，标题「时光机」）。以下 3-tab 与深链描述为历史记录。

- `MylifeHomeScreen`：tab 状态 `rememberSaveable`；三个 tab 的 ViewModel 均以宿主
  backstack entry 为 owner 获取（`hiltViewModel(parentEntry)`），tab 切换不丢
  滚动位置/分页/图状态；离开 `mylife` 路由即整体释放
- 深链走向：
  - Timeline 事件卡 / overview 日卡片 → `navigate("mylife/replay?session=<id>&t=<ts_ms>")`
  - Graph event 节点 → `navigate("mylife/replay?session=<session_id>")`
  - Graph appearance → `navigate("mylife?tab=timeline&date=<session_date>&session=<id>")`
- Replay 会话选择列表是宿主内 Replay tab（无 session 时）；选中后 push
  `MYLIFE_REPLAY` 全屏播放页——返回键回到宿主，符合"播放器是临时全屏"的心智
- 入口切换：`ConversationScreen` Time Machine 按钮 → `navigate(Routes.MYLIFE)`
  （替代 `onNavigateToTimeMachine(webviewUrl)`；`provideMylifeUrl`/WebView 路径保留，
  设置页可加"在浏览器打开"调试入口——非必须）

### 6.4 Timeline 视图（对齐 Web TimelineView）

- **加载**：`TimelineViewModel` 并行 `overview()` + 首页 timeline；UiState：
  `overviewState(Loading/Error/Data) + pages: List<TimelineDay> + nextCursor +
  isLoadingMore + typeFilter`
- **过滤 chips**：全部/对话/语音/画面/窗口/任务（`types` csv 单选；切换即重置分页）
- **下拉刷新**：material3 `PullToRefreshContainer`（ExperimentalMaterial3Api，
  BOM 2024.06 可用）——刷新 overview + 事件流首页并重置分页
- **HeatStrip**：`Row` + 每天一个 `Box(weight=1f)`，高度 `max(6dp, n/total*48dp)`，
  `gold/25%` 底色，点击 → 日期定位（见下）
- **日期定位（HeatStrip 点击 / `?date=` 深链）**：
  1. 目标日 section 已在已加载页内 → `LazyListState.animateScrollToItem`
     （日 section 索引预计算）
  2. **目标日超出已加载分页**（HeatStrip 源自 overview 全量天，事件流仅加载前
     N 页——久远日期必然落空）→ 进入"定位模式"：以 `date_to=目标日` 调
     `timelinePages(dateTo=…)` 重置分页定向拉取，首屏即为目标日附近；清空定位
     模式即恢复默认流（§5.3）
- **overview 章节**（首屏上半）：按 `YYYY-MM` 分组（保持输入顺序），年月大字
  （`headlineMedium`, gold/70%）；日卡片 = stats 徽章行（🎙️ N 段 / 🖼️ N 帧 /
  💬 N 条 / ⏱ N 分）+ summary（2 行截断）+ topics（≤3 gold chip）；
  `analysis.timeline == pending` → "分析中…"脉冲点 + 禁点；否则点击 →
  Replay 深链
- **事件流**：`LazyColumn`；月大字分隔 item；日 section 以普通 item
  呈现日头（~~stickyHeader~~ —— **勘误：foundation 1.6.x（BOM 2024.06.00）
  无 stickyHeader API，1.7+ 才有**；BOM 升级后直接替换即可）；
  事件卡 5 类（`when (e.type)`）：
  - chat：气泡（user 右 gold/20、AI 左 panel；时间 + 我/AI 标签；长按复制）
  - audio：🎙️ + 时间 + 时长 + 情绪 chip（≤3）+ 内联播放（见下）+ summary/transcript
  - video：🖼️ + 时间 + scene chip + Coil `AsyncImage`（`ContentScale.Crop`，
    高 ≤224dp，懒加载）+ caption
  - window：🗔 + caption + 静态/活跃 chip
  - agent_job：🤖 + status chip（done=green/failed=red/其他=gold）+ summary +
    fail_reason（red）；**容忍内容可变**：`items` key 用 `ts_ms`+index，
    不用 job_key（对齐 Web `key={ts_ms}-{i}`）
  - 点击任意卡 → Replay 深链带 `t=ts_ms`
- **audio 卡内联播放**：轻量方案——卡片内"播放"按钮 → `ReplayAudioPlayer`
  （ExoPlayer 单例）播 `media_url`，卡内进度条；**不引复杂 UI**（对齐 Web
  `<audio controls preload="none">` 语义：点击才加载）
- **无限滚动**：`derivedStateOf { lastVisibleIndex >= itemCount - 5 }` →
  `loadMore(cursor)`（对齐 Web sentinel rootMargin 600px 的提前量语义）；
  多页 `days` 按 date 合并去重（对齐 `TimelineView.tsx` 的 Map 合并 + 倒序）
- **pending 轮询**：overview 任一 session `analysis.timeline==pending` → VM
  `while(isActive) { delay(60_000); refresh() }`；无 pending 即停
- **双源展示约定**：overview 章节与事件流来自两个端点，同一天两处可能短暂不一致
  （BFF 本身如此，Web 同样存在）——不做本地合并，以事件流为事件权威（§9 风险 9）

### 6.5 Graph 视图（对齐 Web GraphView + LifeGraph）

**`ForceLayout`（纯 Kotlin，可单测，D7）**——d3-force 语义最小复刻：

```kotlin
class ForceLayout(nodes: List<GraphNodeDto>, edges: List<GraphEdgeDto>) {
    data class SimNode(val id: String, val kind: String, val weight: Int,
                       var x: Float, var y: Float, var vx: Float = 0f, var vy: Float = 0f,
                       var fx: Float? = null, var fy: Float? = null)
    fun tick()          // velocity-verlet：link(distance=70,strength=0.4) +
                        // manyBody(-60-weight*6, Barnes-Hut 可省, 节点<500) +
                        // center + collide(r+6)；alphaDecay 0.05
    fun nodeAt(wx: Float, wy: Float, hitRadius: Float): SimNode?
}
```

- 初始位置环形散布；`LaunchedEffect(nodes)` 内 `withFrameMillis` 驱动 tick 直至
  alpha 冷却（收敛后停止迭代，对齐 d3 停止条件语义）；节点数 <500（BFF 图规模）
  O(n²) manyBody 可接受；>500 时边绘制降采样（§9 风险 4）
- 节点半径：`sqrt` 比例映射 weight→`[4dp, 22dp]`（对齐 Web `scaleSqrt`）

**`LifeGraphCanvas`**（Compose `Canvas` + `pointerInput`）：

- 变换：`scale/offset` 状态（k∈[0.2,4]）；`detectTransformGestures` → 捏合 zoom +
  双指/单指空白 pan；单指按下命中节点 → 拖拽（fx/fy），抬起位移 <6dp 判定点击
  （对齐 Web moved<6 阈值）
- **命中检测**：`nodeAt` 的 `hitRadius = max(节点半径×scale, 22dp)`——保证 44dp
  触摸目标（§6.2）
- 绘制：边（dust/25%，focus 时非邻居 5%）、节点圆（kind 色 + focus 白环）、
  hover/pressed 节点 label 气泡（`drawText`）
- focus 语义：点击非 event 节点 → `focusId`（邻居集高亮其余 alpha 0.1）+
  打开详情 sheet；再点同节点退出；event 节点 → 直接 Replay 深链

**控件条**（顶部，`void/80%` 半透明）：kind 图例 chips（选中=删除线+淡化，
对齐 Web 反选语义）｜日期范围（两个 `DatePickerDialog` 触发）｜min_weight
（`OutlinedTextField` 数字）｜"列表/星图"切换（移动端默认星图）｜focus 中显示
"✕ 退出聚焦"。

**列表副视图**：weight Top 50 `LazyColumn`（kind 色点 + label + kind·weight）。

**`EntityDetailSheet`**（`ModalBottomSheet`，复用 ConversationScreen 既有先例）：
label + kind + 权重/出现次数 + "聚焦关系/退出聚焦"按钮 + appearances 列表
（日期 gold + snippet 2 行）→ 点击跳 Timeline 深链（§6.3）。数据：`entity(id)`；
加载中 skeleton。

**keepPreviousData**：VM 内 `GraphUiState.data` 仅在成功时替换，参数变更期间保留
旧图（避免闪烁，对齐 Web `placeholderData: keepPreviousData`）。

### 6.6 Replay 视图（对齐 Web ReplayView + Stage + Player）

**`ReplayTimeline`（纯 Kotlin，D9）**——逐行移植 Web `playerTimeline.ts`：

```kotlin
data class Segment(val cue: ReplayCueDto, val start: Long, val end: Long)
fun buildSegments(cues: List<ReplayCueDto>, skipPause: Boolean,
                  defaultCueMs: Long = 2500): List<Segment>
fun cueIndexAt(segments: List<Segment>, pos: Long): Int
fun locateCueTs(cues: List<ReplayCueDto>, tsMs: Long): Long
```

**`ReplayViewModel`**：

- `uiState`：`loading / error(not_analyzed 文案:"该会话尚未分析完成") / data`
- 播放器状态：`playing, pos(0..duration_ms), rate(1/2/4), skipPause,
  currentCue(derive: cueIndexAt(segments,pos))`
- tick：`playing && duration>0` 时 `while(isActive) { delay(50); pos += 50*rate }`
  （与 Web 50ms interval × rate 逐字对齐）；`pos >= duration` 自动停（pos 钳位）
- `?t=<ts_ms>` 定位：数据到位一次性 `locateCueTs`（对齐 Web `locatedRef`），
  定位后自动播放
- 会话列表模式（宿主内 Replay tab）：复用 overview 数据，
  `analysis.timeline=="ok"` 才可点（对齐 Web）

**`ReplayStage`**（cue 渲染，`key("${cue.type}-${cue.t_ms}")` 包住内容 +
`AnimatedContent` fade/slide 300ms 过渡，对齐 Web `AnimatePresence mode="wait"`
与 key 重置语义）：

- 顶部居中：当前 cue 原始时刻时钟（`ts_utc` → 本地 HH:MM:SS，`Mono` 字重）
- **frame**：Coil `AsyncImage`（media_url）+ Ken Burns（`animateFloat` 1.15f→1f，
  6s easeOut，`graphicsLayer{scaleX/scaleY}`）；无图 → caption 面板（🗔）
- **chat**：气泡缩放浮现（user 右 gold、AI 左 panel，同 Timeline 气泡样式）
- **audio**：🎙️ + 时长 + 转写字幕卡 + 情绪 chip；`ReplayAudioPlayer` 随 cue 切换
  `setMediaItem(media_url)` + `playWhenReady=playing`；`seekTo` 仅在 scrub 跨越
  同 cue 内偏移时（段内偏移 = pos - cue.t_ms）；seek 由 BFF FileResponse 原生
  Range 支持（无需自实现）
- **pause**：🌙 + hint 呼吸动画（`rememberInfiniteTransition` alpha 0.4↔1，1.6s）
- **job**：🤖 + status chip + text 卡

**`ReplayPlayerBar`**：`Slider`(0..duration, onValueChange=seek) + Play/Pause 圆钮
（gold）+ 1x/2x/4x 变速 chips + "跳过静默"开关（cyan）+ `mm:ss / mm:ss` +
cue 时刻时钟。`skipPause` 变化 → `buildSegments` 重算（segments 是
`remember(cues, skipPause)` 派生）。

**`ReplayAudioPlayer`**（ExoPlayer 封装）：

```kotlin
val player = remember {
    ExoPlayer.Builder(ctx)
        .setMediaSourceFactory(DefaultMediaSourceFactory(OkHttpDataSource.Factory(okHttpClient)))
        .build()
        .apply { setAudioAttributes(audioAttrs, /* handleAudioFocus = */ true) }  // §6.7
}
DisposableEffect(Unit) { onDispose { player.release() } }
```

- **m4a 降级（D12）**：`onPlayerError` 且当前媒体为 `.ogg` → 换
  `?format=m4a` URL 重试一次（仅一次；对齐 Web audioError 单段重置语义）
- 离开 Replay 页即释放（`DisposableEffect`）

### 6.7 音频焦点与 SDK 会话互斥（Phase 1 轻量方案）

- **焦点管理**：`ReplayAudioPlayer` 以 `setAudioAttributes(…, handleAudioFocus=true)`
  托管焦点——audio cue 播放期间自动持有 `AUDIOFOCUS_GAIN_TRANSIENT`，来电/其他
  App 抢焦点时 ExoPlayer 自动暂停，VM 监听 `isPlaying` 变化同步 UI；暂停/离开页
  自动释放
- **与 SDK 实时会话互斥**：进入 Replay 路由与每次按下播放前，检查 SDK 会话状态；
  会话进行中 → 提示"通话中，请先结束会话"且不启动播放（避免与录音/实时音频
  链路争抢）。对话页可见时 Replay 不可见（独立路由），无需反向暂停
- **Phase 2 目标态**：统一 `AudioPlaybackController` 式焦点协调器，SDK 音频与
  MyLife 播放统一调度（本文不实现）

---

## 7. 实施计划（分阶段，每阶段独立可交付）

| 阶段 | 内容 | 交付物 | 验收标准 |
|------|------|--------|----------|
| **P1 地基 + Timeline** | 依赖审批与引入（§4 含 mockwebserver testImplementation）；MylifeModule（client/apiBase/ImageLoader）；MylifeApi + DTO；MylifeRepository；路由与入口切换（Time Machine → 原生宿主）；MylifeHome 骨架 + Timeline 全量（chips/HeatStrip/overview 章节/5 类卡/无限滚动/轮询/下拉刷新/日期定位含定位模式） | api/dto/repository 单测（mockwebserver 契约用例：200/404 信封/游标/相对 media_url 转绝对/JsonElement 宽松字段）；Timeline UI + VM 单测 | 真实 dev 数据滚动流畅；类型过滤/分页/pending 轮询/下拉刷新正确；HeatStrip 久远日期走定位模式；事件点击进 Replay 深链（Replay 占位页即可）；`./gradlew :LivaChatApp:testDebugUnitTest` 过 |
| **P2 Graph** | ForceLayout + LifeGraphCanvas + 控件条 + 列表副视图 + EntityDetailSheet + 深链 | ForceLayout 单测（link/charge/collide 收敛、hitTest 44dp 半径、focus 邻居集）；GraphScreen/VM 单测 | 捏合/拖拽/聚焦/图例/日期过滤正确；appearance → Timeline 日期定位；event 节点 → Replay；节点 300+ 帧率 ≥50fps（中端机实测） |
| **P3 Replay + 收尾** | ReplayTimeline + ReplayViewModel + Stage + PlayerBar + ReplayAudioPlayer（ExoPlayer + AudioFocus + m4a 一次重试）；会话列表 tab；README/AGENTS 更新 | ReplayTimeline 单测（段边界/skipPause/locateCueTs，移植 Web playerTimeline.test.ts 用例）；VM 状态机单测（turbine）；手动冒烟三视图 | cue 有序呈现；audio cue 随播放头自动播放且可 seek；变速/跳静默生效；`?t=` 定位正确；退出页音频即停；SDK 通话中禁止播放提示生效 |

## 8. 测试计划

- **单测（test/，JUnit4 + mockk + turbine + mockwebserver——§4 补齐后可用）**：
  - `MylifeApiTest`：URL 拼装（token/可选参数省略/speed 缺省）、错误信封解析
    （not_analyzed→404）、`ignoreUnknownKeys` 容错
  - `MylifeRepositoryTest`：媒体 URL 相对→绝对、graph 缓存键、overview 缓存失效、
    定位模式 dateTo 传递
  - DTO 序列化测试：真实 BFF 响应样本（`/opt/lvdata` 抽样）反序列化成功；
    `apps/ocr/actions` 等字段缺/坏时容错不整包失败
  - `ForceLayoutTest`：收敛（tick N 次后位移趋稳）、hitTest 半径、focus 邻居集
  - `ReplayTimelineTest`：移植 `apps/web/mylife/src/lib/playerTimeline.test.ts`
    全部用例（段单调、skipPause 置零、空隙归并、locateCueTs 最近邻）
  - `TimelineViewModelTest` / `ReplayViewModelTest`：分页累加、pending 轮询启停、
    tick 推进/rate/seek 边界（pos 钳位 duration）
- **UI 冒烟（androidTest/，compose-ui-test）**：三屏各一条 happy path
  （mockwebserver 供数据，点击事件卡断言导航调用）；ReplayView 播放/暂停/seek
  （假时钟）
- **手工验收**：dev 环境真实 `/opt/lvdata` 数据走完三视图；**OGG 音频直放 +
  m4a 降级各一次**（API 26 模拟器 + 真机 LIO-AN00 + 至少一台旧设备）；Graph
  中端机帧率；深链三向互通；返回键/焦点行为

---

## 9. 风险与开放问题

1. **依赖审批**：§4 五项全部在白名单外（见 `apps/android/LivaChatApp/build.gradle.kts`）。OkHttp 是
   硬必需；Coil/Media3 被拒时按 §4 兜底降级（自研 RemoteImage / 下载后
   MediaPlayer），Timeline audio 卡与 Replay 音频体验降档；mockwebserver 为
   test-only 依赖，预期审批阻力最小
2. **trust-all TLS 仅 dev 合理**：与 SDK WS / WebViewScreen 的既有决策同险
   （中间人可注入）。生产发布前必须收敛：正式 CA 证书 + 移除 trust-all
   （收敛点集中：`MylifeModule` 单处）
3. **OGG Opus 兼容性边界**：平台 Ogg/Opus 链路 minSdk 26 全覆盖（§2.6），但个别
   厂商 ROM extractor 实现差异需真机抽检（验收含 API 26 模拟器 + LIO-AN00 +
   旧设备）。D12 的 m4a 一次重试覆盖单点失败；仍失败则展示卡内错误（不重试
   风暴）
4. **图规模**：ForceLayout O(n²)；BFF 全量图节点随数据增长（每 session 一个
   event 节点 + 实体）。>500 节点时边绘制降采样；再增长需 Barnes-Hut 或服务端
   min_weight 默认值上调（Phase 1 实测验收，超阈值再优化）
5. **ExoPlayer 与 SDK 音频焦点**：方案见 §6.7（handleAudioFocus + 会话互斥
   检查）；Phase 2 统一协调器。需在验收中验证对话录音质量不受 Replay 影响
6. **agent_job 内容可变**：列表/回放须容忍（key 不含内容字段）——已内建设计
7. **device_id 过滤的产品语义**：App `LivaChatConfig.deviceId` == recording
   device_uuid（§2.2），BFF `get_principal` 已支持 `?device_id=`；"只看本机"是
   合理的 Phase 2 设置项；Phase 1 不过滤（D10）
8. **token 在 URL**：沿用 BFF Phase 1 契约（`?token=`），日志/截屏可能泄露；
   Phase 2 随 BFF 鉴权统一升级（header 化）。token 注入点已隔离在
   `MylifeApi`/`MylifeModule`，改动收敛
9. **概览/事件流双数据源短暂不一致**：overview 章节与事件流分别来自
   `/overview` 与 `/timeline`，同一天两处展示可能短暂不一致（BFF 本身如此，
   Web 同样存在）——不做本地合并，保持简单（§6.4 约定）
10. **HeatStrip/深链定位超出已加载分页**：overview 覆盖全量天而事件流按页
    加载，久远日期定位必然落空——已内建"定位模式"（`date_to=目标日` 重置
    分页，§6.4）；需在 VM 单测覆盖该分支

---

## 10. 事实基线锚点（2026-08-19 核对）

| 事实 | 锚点 |
|------|------|
| App 技术栈/白名单/钉版约束 | `apps/android/LivaChatApp/build.gradle.kts`（依赖白名单/钉版） |
| OkHttp 在 SDK 为 `implementation`（App 编译不可见） | `apps/android/LivaChatSDK/build.gradle.kts` dependencies |
| mockwebserver 仅在 androidTestImplementation | `apps/android/LivaChatApp/build.gradle.kts` dependencies |
| App 分层与导航模式 | `docs/android_app_arch.md` §1/§2；`domain/usecase/{Chat,History,Session,Settings}UseCase.kt`；`ui/navigation/NavGraph.kt`、`Routes.kt` |
| 既有 mylife WebView 入口与 URL 推导 | `di/AppModule.kt:43-54 provideMylifeUrl`（serverUrl 硬编码 :28）、`ConversationViewModel.kt:46`、`ConversationScreen.kt:219`、`ui/screens/webview/WebViewScreen.kt` |
| deviceId 注入点（config.deviceId；getOrCreateDeviceId 为 private） | `di/AppModule.kt:30`、`:56-64` |
| WebView SSL proceed 产品决策 | `WebViewScreen.kt:182-192`（注释 2026-08） |
| SDK WS trust-all 先例 | `LivaChatSDK/.../ConnectionManager.kt:101-118` |
| App deviceId == recording device_uuid | `services/session/omni_orchestrator.py:171 device_uuid=ctx.device_id` |
| BFF API 契约与错误信封 | `services/bff/router.py`、`services/bff/{timeline,graph,replay}/router.py`（replay `speed` 默认 8.0）、`docs/mylife/mylife-design.md` §6 |
| token/device_id 统一 Query 入口 | `services/bff/common/deps.py:get_principal` |
| DTO 字段（TS 镜像） | `apps/web/mylife/src/api/types.ts`（头注"与 bff/common/schemas.py 对齐"） |
| derived 容错语义（unknown 字段宽松解析依据） | `services/bff/common/store.py:100-113` |
| media_url 相对路径 | `services/bff/replay/service.py:123` |
| Web token 读取（sessionStorage）与自动附加 | `apps/web/mylife/src/api/client.ts:15-28`、`src/token.ts` |
| pending 轮询 60s / infinite query(limit=200) / keepPreviousData | `apps/web/mylife/src/api/hooks.ts:11-34,46` |
| Timeline 交互（chips/HeatStrip/粘性头/rootMargin 600px/事件 key `${ts_ms}-${i}`/Map 合并去重） | `apps/web/mylife/src/views/TimelineView.tsx` |
| 播放器段语义（移植源） | `apps/web/mylife/src/lib/playerTimeline.ts`（DEFAULT_CUE_MS=2500）+ `playerTimeline.test.ts` |
| Graph 力学参数/配色/交互阈值 | `apps/web/mylife/src/components/LifeGraph.tsx:94-98,230`（link 70/0.4、charge -60-6w、collide r+6、alphaDecay 0.05、半径 4→22、k∈[0.2,4]、moved<6 点击） |
| Stage/Player 交互（Ken Burns 6s、key `${type}-${t_ms}`、audioError 重置、变速/跳静默/时钟） | `apps/web/mylife/src/components/{Stage,Player}.tsx` |
| Web 色板 | `apps/web/mylife/src/theme.css`（abyss…green 13 色） |
| OGG Opus 平台支持（Opus 解码 Android 5.0+ 含 Ogg 容器；.opus 扩展名 Android 10 识别） | developer.android.com/media/platform/supported-formats |
| emotion/scene 枚举值 | mylife-design.md §2.2（`lvdata_analyze_audio.py:173`、`lvdata_analyze_video.py:127`） |
| Compose BOM 2024.06.00 含 PullToRefreshContainer；**不含 stickyHeader**（勘误：foundation 1.6.x 无该 API，1.7+ 才有） | `build.gradle.kts` BOM 版本；foundation-release.aar 1.6.8 `androidx/compose/foundation/lazy/` 无 Sticky 类（2026-08-19 核）；material3 PullToRefreshContainer（ExperimentalMaterial3Api） |
| ModalBottomSheet 既有先例 | `ConversationScreen.kt`（AudioRouteSheet:373 / AvatarCustomizationSheet:381 / LanguagePickerSheet:413） |
| 管线延迟与 pending 语义 | `docs/mylife/mylife-design.md` §8.3（60s 轮询 + 600s 静默） |
| 验收真机型号 | LIO-AN00（Huawei Mate 30 Pro）+ API 26 模拟器 + 一台旧设备 |

---

*Design version: 2.0（Final 合并稿）| Created: 2026-08-19 | 合并
`mylife-frontend-android-k3.md`（基线）与 `mylife-frontend-android-d4.md` 评审结论，
全部锚点经仓库代码逐条核对*
