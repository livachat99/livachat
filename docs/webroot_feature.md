# Webroot — Agent 开发 Web 页面（设计定稿）

> **状态**：**已实施**（2026-10-01：server / prompts / App 三块落地并通过测试；真机 E2E 已跑通——
> job 送达结果链接、App 点击打开 WebView、`playwright-dev` 冒烟过，见 §10）；剩余反向用例与生产开启（§9 步骤 4/5）
> **2026-10-01 二次修订（D4'，已实施）**：转换时机改为 **App 渲染前**（`MarkdownContent` 内转换；
> Room / server 全链路保持原文）——见 §3.4/§4/§10；上一版点击期实现（commit 2a64cbe）已被取代
> **Date**: 2026-10-01
> **版本来源**：合并自两份独立设计稿——基线 `docs/webroot_feature-d4.md`（含 D1–D6 拍板、review 修订、
> N6 验收门禁），吸收 `docs/webroot_feature-k3.md` 的决策表/里程碑/测试矩阵表述。两稿差异经 codebase 实测核对
> （安装版 Starlette 1.6.0、仓库真实锚点）；k3 稿中与实测不符的表述（尾斜杠重定向机制、截断影响面、默认开启
> 语义）已按实纠正，不落入本文。本文承继 d4 的 file:line 实证纪律（§2 全部锚点为 2026-10-01 直读核实）。
> **需求来源**：用户口述 6 条（见 §1）
> **Scope**：`services/`（静态挂载 + hermes prompts）· `apps/android/LivaChatApp`（链接转换 + WebView 打开）·
> 部署项（Hermes 侧 `playwright-dev` MCP、目录 provision）
> **关联**：[docs/hermes_adaptor_design_v2.md](./hermes_adaptor_design_v2.md)（agent relay / hermes 架构）·
> [docs/agent_relay_prompts.md](./agent_relay_prompts.md)（prompt 注入先例与同步纪律）·
> [docs/fd_markdown_view.md](./fd_markdown_view.md)（App Markdown / WebView 设计）·
> [docs/android_agent_job_view.md](./android_agent_job_view.md)（job 结果展示）·
> [docs/data/lvdata_mcp_tools.md](./data/lvdata_mcp_tools.md)（Hermes 工具集先例）

---

## 1. 需求（原文语义，逐条对照）

| # | 需求 | 本文对应设计 |
|---|------|-------------|
| N1 | server 挂新静态目录：`webroot` → `/opt/lvdata/webroot` | §3.1 静态挂载 |
| N2 | agent 开发的每个 web page 单独建子目录，目录名 uuidv7（如 `01a0f54b-4fe4-7c4f-80a2-88e711556d69`）；每个新页面新目录 | §3.2 目录规范 + §3.3 prompt |
| N3 | 页面开发按用户需求选最佳实践技术栈；可用 Playwright MCP 工具做测试 | §3.3 prompt 指引（Hermes 侧部署项） |
| N4 | agent_job result 仍为 markdown 文本，内嵌页面链接；链接格式 `livachat_url://webroot/<uuid>/`（或更深路径如 `.../static/`）；App 把链接转成实际对外地址 `https://server_public_address:8080/webroot/xxxx`，点击可用 web browser 查看 | §3.4 链接协议与 App 渲染期转换 |
| N5 | 以 codebase 为基础，不猜测；分析与设计计划落本文档 | 本文（全文 file:line 均为仓库实证） |
| N6 | （2026-10-01 补充）agent 开发测试**必须经 livachat server 访问验证**：如 `https://127.0.0.1:8080/webroot/xxxxx/` 可访问且渲染/交互正常，**才算通过**；通过后才允许在 agent_job result（markdown）中给出 url 链接 | §3.3 验收门禁（prompt 强制）+ §5 矩阵 + §8 R7 |

**固定项（用户已拍板，设计不另立选项）**：静态根路径名 `webroot`、目录根 `/opt/lvdata/webroot`、
子目录 uuidv7、URL scheme `livachat_url://`、App 端转换为 `https://<server>:<port>/webroot/<path>`。

**非目标（本期不做）**：iOS 客户端（`apps/ios/` 当前仅 `.gitkeep`）——未来按 §3.4 同一转换规则对齐；
页面鉴权；页面保留/GC 策略；`/mylife` 既有托管不变；页面内容审查；协议（wire）变更。

---

## 2. 现状调研（codebase 事实，2026-10-01 直读核实）

### 2.1 服务器静态托管先例（mylife）

> 2026-10-13：先例已退役——/mylife 托管与 `_mount_web_dist` 已随 Web app 停止使用整体摘除；
> 本节作为设计当时的先例记录保留（`_mount_webroot` 沿用其挂载模式）。

- `create_app`（`services/app/application.py:349`）在 `config.bff.enabled` 时注册 BFF 并调
  `_mount_web_dist`（`:363-367`）。
- `_mount_web_dist`（`application.py:372-384`）：`app.mount("/mylife", StaticFiles(directory=..., html=True))`；
  相对路径按**仓库根**解析（`Path(__file__).resolve().parents[2]`）；**目录不存在则静默跳过**（`is_dir()` 早退，
  不报错、不影响其他接口）。
- webroot 与之的区别：webroot 是 agent 产出物的**承诺**（prompt 告知 agent 页面会被服务）——静默跳过
  会导致"agent 写了页面但 404"，故缺失语义按 §4 D2（已拍板）：自动 mkdir + 失败 fail-fast。

### 2.2 路径与配置现状

- `/opt/lvdata/webroot` **已存在**（用户 2026-10-01 手工创建，当前为空，owner=`<user>`）；
  `/opt/lvdata/derived/webroot` 另有一个空目录——那是 mylife 设计期遗留（`docs/mylife/mylife-design.md:773/:800`
  已决策 Phase 1 使用 `apps/web/mylife/dist`），与本 feature 无关，不迁移、不引用。
- `RecordingConfig.base_dir: str = "/opt/lvdata"`（`services/shared/config/models.py:222`，`enabled` 默认关 `:221`）；
  `BffConfig.data_root: str = ""` 默认回退 `recording.base_dir`（同上 `:236`）——config 字段"空串回退 base_dir"
  是既有惯例。
- AppConfig env 覆盖：`LIVACHAT__<SECTION>__<KEY>`（`models.py:251-252`，BaseSettings，`env_nested_delimiter="__"`）。
- `configs/dev/server.yaml`：`host: "0.0.0.0"` / `port: 8080`（`:9-10`）、SSL 开（自签 `configs/dev/cert.pem`）、
  `recording.base_dir: /opt/lvdata`（`:222`）；**`configs/production/`、`configs/staging/` 仅含 `.gitkeep`
  （无实际配置）**——即生产环境默认行为 = webroot 不开启；上线需显式部署动作（§9 步骤 5）。
- 安装契约（`docs/install_design.md:112/:319`）：`/opt/lvdata` 必须存在且当前用户可写，两服务 fail-fast。
- **测试装配模式（D1 默认值的关键约束）**：单测/集成测试以 `AppConfig()` 默认值装配 app——
  `services/tests/unit/bff/test_gzip.py:22`、`services/tests/unit/test_sanity.py:17` 直接
  `create_app(AppConfig())`（不跑 lifespan）；`services/tests/integration/agent_relay/test_application_wiring.py:143`
  跑**真实 lifespan** 且只把 `chat_server_db_path` 指到 tmp_path、不设 recording 段（`base_dir` 落 `/opt/lvdata`
  默认值）。⇒ 任何"默认开启 + 启动期要求 `/opt/lvdata` 存在"的设计都会把本可用 tmp 隔离的测试变成需要真机
  数据根（review 修正 D1 的直接依据）。
- uuid7 生成器**已有**：`services/shared/utils/ids.py:54` 纯标准库实现（RFC 9562，Python 无 stdlib uuid7）；
  文件名/主键先例见 `docs/server_recording_design.md` §A.1。

### 2.3 agent job 结果链路（链接的载体）

```
主 agent 契约 JSON {action:"job_request", spec, summary}
  → relay 调 prompt.background(任务说明 = spec + chatlog 路径 + 指引 + 输出要求)
    （_handle_job_request hermes.py:1223 → _render_job_task :1263 → RPC :1266）
  → Hermes background turn（同机执行,可 shell/文件/工具,分钟级）
  → WS 事件 background.complete {task_id, text}
  → JobStore 标 done（result_text 落库）              job_store_sqlite.py
  → JobResultDeliverer.deliver（delivery.py:56；PushMessage :78-81）
      PushMessage(text=result_text, proactive=true, message_id="msg_agent_job_{job_key}"，确定性幂等键 D6)
  → PushPublisher → gateway → s2c ai_text → App
```

关键事实：
- **结果即 markdown 文本**，本 feature 的链接就嵌在 `text` 里——**wire 协议零改动**
  （`hermes_adaptor_design_v2.md` §4.7 的 push 白名单语义不变）。
- **结果截断**：`job_store_sqlite.py:686`（及 `job_store.py:448`）`(text or "")[:max_result_bytes]`——
  **保头去尾**；默认/当前配置 **16384**（`agent_relay/config.py:56`、`configs/dev/server.yaml:264`；
  push 侧 `max_reply_bytes: 16384` 同量级，`configs/dev/server.yaml:236`）
  → 链接必须出现在结果文本**前部**（§3.3 prompt 明确要求），否则长文案会把链接截掉。
- Hermes、chat server、lvdata-mcp **同机部署**（`hermes_adaptor_design_v2.md` §6.4）；background agent
  可写本机文件系统——`/opt/lvdata/webroot` 与 chatlog 同数据根，写入权限与 chatlog 同级（同用户）。

### 2.4 hermes prompts 渲染机制（能力告知载体）

- 模板文件：`services/agent_relay/prompts/hermes_monitor.md`（mission 种子，主 agent 契约；仅允许
  noop/job_request/message 三种响应，明言"你只做决策，不要试图自己创建后台任务"）与
  `prompts/job_task.md`（后台任务书）。常量锚点：`_MISSION_TEMPLATE`（`hermes.py:86`）、
  `_JOB_TASK_TEMPLATE`（`:87`）。
- 渲染：`string.Template.safe_substitute`——`_render_mission`（`hermes.py:1559`，占位符
  `$chatlog_path/$chat_session_id/$path_hint/$timezone_hint/$agent_name_hint`）与
  `_render_job_task`（`hermes.py:1580`，另含 `$task`）。
- 条件注入先例：`_agent_name_hint`（`hermes.py:1536`）——feature 关闭时**整段（含标题）返回 ""**，
  占位符不残留；这是 webroot 提示段的直接模板。
- adapter 已持有数据根路径：`self.base_dir: str = "/opt/lvdata"`（`hermes.py:191`），由
  `application.py:179` 从 `cfg.recording.base_dir` 注入——webroot 路径可同源派生。
- **同步纪律（prompts_improvements 的漏改教训）**：prompt 改动须同步
  ①`docs/hermes_adaptor_design_v2.md` §4（mission 内容要点规范副本）②
  `services/tests/unit/agent_relay/test_hermes_contract.py`（渲染断言）。
- Hermes 侧外部配置先例：lvdata MCP 注册（`hermes_adaptor_design_v2.md` §6.1，本仓库只写部署说明）；
  Playwright MCP（`playwright-dev`，`--ignore-https-errors`）同理走 Hermes 配置，不在本仓库代码内。

### 2.5 App 端（仅 Android；apps/ios 仅 .gitkeep）

链接从 markdown 渲染到打开 WebView 的现有链路：

```
MarkdownContent（ui/components/MarkdownContent.kt——全 App 唯一 Markwon 入口）
  · linkResolver: `if (link.isHttpUrl()) onLinkClick(link)`      MarkdownContent.kt:73-75
  · isHttpUrl(): scheme ∈ {http,https} 且 host 非空              MarkdownContent.kt:32-36
  · 折叠正文调用点：AgentJobResultText.kt:140——会话气泡（MessageBubble.kt:157 分支
            「AI final 且 onLinkClick != null」，透传 :169）与时间线 agent 行
            （HotSlotRowItems.kt:217-222，经 AgentJobResultText :220）共用；
            job 结果全屏：AgentJobResultOverlay.kt:146
  ↑ onLinkClick 透传：ConversationScreen.kt:978 / HistoryDetailScreen.kt:132（MessageBubble 参数）
NavGraph onOpenLink = { url -> navController.navigate(Routes.webview(url)) }
  · 4 处接线：Conversation(:63) / HistoryDetail(:85) / HotSlot(:166) / EventListViewer(:208)
Routes.webview(url)（Routes.kt:29，Uri.encode）→ WebViewScreen
  · 入口再校验 isHttpUrl（WebViewScreen.kt:75）——非 http/https 显示错误占位
  · WebView 安全基线：JS 开、DOM storage 开、allowFileAccess/ContentAccess 关、无 JS bridge、
    SSL 错误忽略（WebViewScreen.kt:135-139/:182-192）
  · 顶栏动作「在浏览器中打开」（WebViewScreen.kt:112-114, openInBrowser :59）
```

现状结论：**`livachat_url://` 链接当前被 linkResolver 白名单丢弃（点击无反应）**——这是 App 端唯一缺口；
设计上转换在**渲染前**完成（MarkdownContent 内，§3.4），点击链路零特化（先于 WebViewScreen 入口的
`isHttpUrl` 校验）。

服务器地址来源与既有推导先例：
- `LivaChatConfig.serverUrl` 即所选服务器 WS 地址，如 `wss://<server-host>:8080/v1/ws/stream`
  （`ServerOption.kt:13-14`，PUBLIC/LAN 两项）。
- **既有转换先例**：`AppModule.kt:66-73` `provideMylifeUrl`（@Named 于 :65）——`wss→https / ws→http`、
  端口缺省 443/80、输出 `$scheme://$host:$port/...`；`SettingsViewModel.checkService`（`:98` 起）
  同规则（两处重复，本次可收敛）。
- `MainActivity` 为 `@AndroidEntryPoint`，可注入 Hilt 提供的 base URL 传给 `NavGraph`。

---

## 3. 设计

### 3.1 服务器静态挂载（N1）

**配置**：`services/shared/config/models.py` 新增（`root` 跟随 `BffConfig.data_root` 的空串回退惯例；
`enabled` **默认关**、dev yaml 显式开——对齐 `RecordingConfig.enabled` 惯例，依据见 §2.2 测试装配模式与
§4 D1 review 备注）：

```python
class WebrootConfig(BaseModel):
    """Agent 产出 Web 页面的静态托管（docs/webroot_feature.md）。"""
    enabled: bool = False                  # dev yaml 显式开：webroot: {enabled: true}
    root: str = ""      # 默认回退 <recording.base_dir>/webroot（/opt/lvdata/webroot）
```

**挂载**（`application.py`，与已摘除的 `_mount_web_dist` 并列新增 `_mount_webroot`）：

- `enabled=false` → 不挂载、无任何 FS 副作用（单测/集成测试的默认配置不受影响）、prompt 不含 web 能力段
  （能力与承诺一致）。
- `enabled=true` → `Path(root or base_dir/"webroot")`：`mkdir(parents=False, exist_ok=True)`（父目录缺失即
  `/opt/lvdata` 缺失，按安装契约本该 fail-fast）→ `app.mount("/webroot", StaticFiles(directory=..., html=True))`
  （mkdir 后目录必存在，StaticFiles 默认 `check_dir=True` 的构造校验自然满足）。
- 独立于 `bff.enabled`（webroot 与 MyLife 无依赖）。
- 不做缓存头定制（Starlette 默认 ETag/Last-Modified，够用；uuid 目录内容实际不可变，immutable 缓存头留作后续备选、本期不做）。
- 目录缺失/建目录失败语义（§4 D2）：enabled=true → 自动 mkdir；创建失败 → create_app 即抛错（启动 fail-fast）。
- **生产部署**：`configs/production/` 现为空配置——上线需在部署配置显式 `webroot: {enabled: true}`
  （或 env `LIVACHAT__WEBROOT__ENABLED=1`），否则功能整体不激活（§9 步骤 5）。

**已知行为（Starlette 1.6.0 实测，`starlette/staticfiles.py`）**：`html=True` 时目录请求服务 `index.html`
且无目录列表；目录无 `index.html` → 404；`/webroot/<uuid>`（无尾斜杠且 index.html 存在）→
**307 重定向**到 `<uuid>/`（RedirectResponse 默认码，`:140-144`）；路径穿越由 `lookup_path` 的
路径规范化 + `commonpath` 前缀校验拦截（`:154-173`；构造期 `check_dir=True` 校验在 `:55-56`）。
GZip/access-log 中间件对 HTML/JS/CSS 正常生效（media 排除规则不影响这几类）。

### 3.2 页面目录规范（N2）

- 目录：`/opt/lvdata/webroot/<uuidv7>/`，**每个页面一个新目录，禁止复用/覆盖**（同内容修订 = 新目录，
  旧链接永不失效——与 uuid7 时间有序命名共同承担"天然版本化"）。
- 入口：`index.html`；资产（JS/CSS/图片/数据）放同目录内任意层级，**引用一律相对路径**（页面挂在
  `/webroot/<uuid>/` 子路径下，以 `/` 开头的绝对路径会 404——这是本 feature 最容易踩的坑，prompt 重点强调）。
- 生成方式：**由 agent 现场生成**（§4 D5）；prompt 内嵌一段可复制的 uuid7 python 片段（机器已有 python3，
  `services/shared/utils/ids.py:54` 同款算法）。
- 服务器侧对目录名**不做校验**（静态目录无感知）；目录名的合法性由 agent 遵循 prompt 保证；
  App 端转换时做第一段 UUID 校验（§3.4）兜底。

### 3.3 agent prompts 变更（N2/N3）

**新增占位符 `$webroot_hint`**（样式对齐 `_agent_name_hint`：关闭时整段返回 ""，含标题，占位符不残留）：

- `_webroot_hint(webroot_dir, *, verbose)`（`hermes.py` 新增静态方法）：
  - `webroot_dir` 为空（config 关闭）→ `""`；
  - verbose=True（mission）→ 主编排要知道"可以派发 web 页面任务"（**只给能力声明 + 触发场景**，
    执行细节在背景任务书里）；
  - verbose=False（background）→ 完整执行规范（目录/uuid7 片段/相对路径/技术栈/自测/链接格式/放在结果前部）。
- `_render_mission` 增加 `webroot_hint=self._webroot_hint(self.webroot_dir, verbose=True)`；
  `_render_job_task` 同理（verbose=False）。`self.webroot_dir: str = ""` 属性（`hermes.py:191` `base_dir`
  旁），由 `application.py` 装配处（`:174-179` 一带）注入 = §3.1 解析出的同一路径（单一事实源；未启用 = ""）。

**文案草案（逐字，供评审）**：

`prompts/hermes_monitor.md`（新增小节，位于时区/名字小节同款位置）：

```markdown
# Web 页面(复杂 / 可交互内容)

用户对话中明确提到使用「网页方式呈现」,或者用户的需求超出文字/图片能承载的形式(图表、看板、
可操作的界面、需要来回交互的小工具等),在 job_request 的 `spec.task` 里明确要求"产出一个 Web 页面",
说明页面要展示与交互的内容。
后台任务会把页面放进服务器可直接访问的目录,并在结果里附上可点击的链接;你无需关心部署细节。
```

`prompts/job_task.md`（新增小节）：

```markdown
# Web 页面(复杂 / 可交互内容)

当任务适合用网页呈现(图表、看板、可操作界面、多步交互等),产出一个 Web 页面:

- 新建独立目录:`{webroot}/<uuidv7>/`,每个页面一个新目录,禁止复用已有目录;
  uuidv7 可用如下命令生成(任一等价方式亦可):
  `python3 -c "import time,uuid,os;ts=int(time.time()*1000).to_bytes(6,'big');r=os.urandom(10);a=((int.from_bytes(r[:2],'big')&0x0FFF)|0x7000).to_bytes(2,'big');b=((int.from_bytes(r[2:8],'big')&0x3FFFFFFFFFFFFFFF)|0x8000000000000000).to_bytes(8,'big');print(uuid.UUID(bytes=ts+a+b))"`
- 入口文件 `index.html`;所有资源放进该目录,引用一律用**相对路径**(页面挂在子路径下,
  以 `/` 开头的绝对路径会 404)。
- 技术栈按需求选最佳实践:默认零构建的 HTML/CSS/JS;需要图表/组件库时把库文件放进目录内(自包含),
  复杂交互应用可先本地构建、最终产物必须是该目录内的纯静态文件。
- 页面在手机端 WebView 打开(已启用 JS 与 DOM storage、忽略自签证书)与桌面浏览器都要可用。
- 验收(强制门禁):页面**必须经 livachat server 的实际访问路径验证通过后,才允许把链接写进结果**——
  用 `playwright-dev` MCP 工具(该 server 已配置 --ignore-https-errors,自签证书可直接打开)
  访问 `https://127.0.0.1:8080/webroot/<uuid>/`(这是客户端 WebView 将打开的同一地址),
  验证渲染与主要交互。不要用其他 playwright server(未忽略证书,会报 ERR_CERT_AUTHORITY_INVALID)。
  验证未通过 → 修复后重新验证;最终仍未通过 → 结果**不得**包含 livachat_url 链接(如实说明未能完成即可)。
- 结果文案中的链接用 markdown 链接格式、放在开头前几行(结果有 16KB 截断):
  `[页面标题](livachat_url://webroot/<uuid>/)`——原样使用这个内部链接,客户端会转成实际地址;
  不要写 http(s) 绝对地址。
```

（`{webroot}` = `$webroot_hint` 渲染值中的实际目录；`$timezone_hint`/`$agent_name_hint` 同款 `safe_substitute`
占位符法。链接保留尾斜杠写法：与需求示例一致、省一跳 307 重定向；App 端兜底跟随重定向，但不依赖。）

**同步项**（防漏改清单，教训见 `docs/agent_relay_prompts.md` §6）：`docs/hermes_adaptor_design_v2.md` §4
加 web 页面条目；`services/tests/unit/agent_relay/test_hermes_contract.py` 增渲染断言（开/关两态 +
占位符不残留）。

**Hermes 侧部署项（非本仓库代码）**：注册 `playwright-dev` MCP server（`playwright-mcp` 二进制 +
`--headless --ignore-https-errors`）——`--ignore-https-errors` 为必需项：服务端是自签证书，未配置的
plain `playwright`/`playwright_proxy` server 首次导航会报 `ERR_CERT_AUTHORITY_INVALID`（2026-10-01 联调
实测）。**验收门禁（N6）依赖它：无浏览器自动化工具则页面无法完成"经 server 访问"验收、不产出链接，
属部署前置项**；确认 background 可用的 shell 里 `python3` 存在。冒烟验证步骤见 §9 步骤 0。

### 3.4 链接协议与 App 渲染期转换（N4）

**协议（写入 prompts 的约定，wire 不变）**：

```
livachat_url://webroot/<uuidv7>[/<path>][/]      # 例：.../01a0f54b-.../  或  .../01a0f54b-.../static/
```

- 仅 `webroot` 一种 host，其余不命中转换（保留未来扩展空间，如 `livachat_url://audio/…` 不在本期）。
- 服务器不感知该 scheme——它只是 agent 约定 + App 渲染期转换触发器。
- **存储与传输：全链路保持原文（raw）**——JobStore / ai_text / chatlog / WorkingMemory / Room 都存
  `livachat_url://…`，服务端与本地都不做转换（"本地存储不需要转换"是本节设计前提）。

**App 渲染期转换（唯一转换点；2026-10-01 二次修订 D4'）**：

```
convertLivachatUrls(text, serverBaseUrl): String        # 纯函数，可 JVM 单测
  · serverBaseUrl 空 → 原样返回（幂等；预览/单测默认 "" 即降级为不转换）
  · 扫描 `livachat_url://webroot/`（大小写不敏感前缀匹配；不用 Uri.parse——"_" 非 RFC 合法
    scheme 字符）；token = 从前缀起到"截断界"为止：空白 / `)` / `]` / `>` / `"` / `'` / 任一非 ASCII
    字符；再剥尾部 ASCII 标点（`.` `,` `;` `:` `!` `?`）——剥离字符不进校验、不参与替换（留原位）
  · token 校验（与旧点击期解析同一套规则，实现内聚到 convert）：
      首段必须 uuidv7（^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$，
      大小写不敏感）；含空白 / ".." / "\" → 非法
  · 合法 → "$serverBaseUrl/webroot/$rest"（serverBaseUrl 形如 https://<lan-host>:8080）；非法 → 原样保留
```

- **接线点（§4 D4'）**：`MarkdownContent` 内、交 `markwon.setMarkdown` 之前一步：
  `remember(markdown, serverBaseUrl) { convertLivachatUrls(...) }`，**renderKey 用转换后文本**
  （否则转换产物变化不触发重渲染）。
- `serverBaseUrl` 经 `LocalServerBaseUrl`（`staticCompositionLocalOf { "" }`）提供：`MainActivity`
  （`@AndroidEntryPoint`）以既有 `@Named("serverBaseUrl")` 注入值 `CompositionLocalProvider` 包裹；
  `MarkdownContent` 签名不变（环境经 Local、不进参数——隔离层仍保持"可整体替换 mikepenz"）。
- **点击链路回到零特化**：交给 Markwon 的文本只含普通 https → `linkResolver` 白名单 = `isHttpUrl()`；
  `NavGraph` 4 处 `onOpenLink` 恢复原 lambda（`navController.navigate(Routes.webview(url))`）——
  点击期转换（`openLink` / `resolveServerLink` / 白名单扩展）全部删除。
- `serverBaseUrl` 推导：`AppModule` `@Named("serverBaseUrl")`（wss→https/ws→http、缺省端口 443/80；
  `serverAddress` 单一实现，`provideMylifeUrl` 与 `SettingsViewModel.checkService` 同源引用）。
  **2026-10-10 修订（已实施，见 `docs/setting_server_address.md`）**：渲染基址在 host 命中 IP→域名
  映射（`<server-host> → <public-domain>`）时用域名、未命中回退原 host——系统浏览器打开渲染链接时
  严格校验证书 hostname，而 dev 证书仅含 DNS SAN（无 IP SAN）；连接侧（SDK WS / mylife OkHttp /
  应用内 WebView）均忽略证书、IP 直连不受影响。映射**仅**作用于本渲染基址；`provideMylifeUrl` /
  `checkService` / WS 连接仍用 IP。**2026-10-10 补记：该映射暂注释停用**（`ServerLinks.kt`
  `PUBLIC_DOMAIN_BY_HOST` 注释保留）——渲染基址回退原 host（PUBLIC → IP）；恢复见 `docs/setting_server_address.md` §8。
- **打开方式**：走既有 `WebViewScreen`（应用内浏览器，JS/DOM storage 已开、忽略自签证书、顶栏可转系统浏览器）；
  `WebViewScreen` 无需任何改动。

**关键性质与约束**：

- **无迁移成本**：存量旧消息（Room 存 raw）在渲染时同样被转换 → 照常可点（不必重跑 job、不回填数据）。
- **渲染文本 ≠ 存储文本**：UI 中选择/复制得到的是转换后 https；代码层 `message.text` 恒为 raw
  （今后读取文本的代码需知晓，勿重复转换）。
- 裸文本 `livachat_url://…`（无链接语法）也会被转换 → 经 Linkify（WEB_URLS）成链、变为可点（较改造前为增益）；
  prompt 仍强制链接语法（防呆）。含代码块内的该前缀同样被替换（纯文本级转换，已知长尾）。
- **纯文本渲染面不转换**：未走 Markdown 的面（如相机/视频浮层 `VideoPreview.lastAiText`、非 final 临时文本）
  保持 raw `livachat_url://` 原文显示（不可点）——与现状一致（该面本就显示 markdown 源码）；点击语义不受影响。
- 气泡（窄，75% 屏宽）与全屏 overlay（整宽）对同一 raw 文本执行同一转换 → 两边字符流一致，
  点击偏移 / 全屏落位体系（§10.2）不受影响。

### 3.5 安全边界

| 面 | 现状/设计 | 说明 |
|----|----------|------|
| 目录穿越 | StaticFiles 自带防护 + App 端 rest 校验（禁 `..`/`\`/空白） | 双层 |
| 未鉴权访问 | `/webroot/**` 与 `/mylife` 同为公开静态（dev token 现状）；uuidv7 随机不可猜（74bit rand） | 与现状一致；Phase 2 鉴权时统一收口 |
| 同源风险 | 页面与 API 同源（`:8080`）——页面 JS 可访问无鉴权的 `/api/mylife/*`、可用内置 dev token 连 WS | **已知风险**，dev 阶段接受（内容为 agent 应本机用户要求生成）；缓解备选：独立端口/子域、CSP 头（本期不做，见 §8 开放问题） |
| 自签证书 | App WebView 忽略 SSL 错误（既有产品决策，WebViewScreen.kt:182-192）；系统浏览器打开时会提示 | 现状不变 |
| 恶意链接 | 仅 `livachat_url://webroot/<uuidv7…>` 命中转换、且产物恒为当前服务器地址（`serverBaseUrl`；2026-10-10 修订后经 IP→域名映射）；其余原样保留为不可点文本；点击白名单 = `isHttpUrl`（普通 https 语义） | 转换+校验在渲染前一次完成 |

---

## 4. 决策（2026-10-01：D1–D6 全按推荐拍板）

| # | 决策 | 结论（推荐 A 采纳） |
|---|------|--------------------|
| D1 | 挂载配置 | 新增 `AppConfig.webroot {enabled, root=""}`——**`enabled` 默认 `False`（review 修正，见下），dev yaml 显式 `webroot: {enabled: true}`，生产部署需显式开启（§9 步骤 5）**；`root` 空串回退 `recording.base_dir/webroot`（env 可覆盖 `LIVACHAT__WEBROOT__*`）——跟随 `RecordingConfig.enabled`（默认关）与 `BffConfig.data_root`（空串回退）两个既有惯例；路径单一事实源 |
| D2 | 目录缺失语义（enabled=true 时） | 自动 `mkdir(exist_ok=True)`；创建失败 → 启动 fail-fast——目录是 prompt 对 agent 的承诺，静默跳过会"写了页面但 404"；父目录 `/opt/lvdata` 缺失本就按安装契约 fail-fast |
| D3 | 打开方式 | 应用内 WebView（复用 WebViewScreen，自带"在浏览器打开"）——与 fd_markdown_view 既有交互一致；"web browser 查看"由应用内浏览器 + 顶栏系统浏览器动作满足 |
| D4 | livachat_url→https 转换位置 | **渲染期：`MarkdownContent` 内、交 Markwon 前一步 + `LocalServerBaseUrl` 注入**（2026-10-01 二次修订 D4'，取代原点击期方案）——存储/传输全链路保持 raw；点击回到普通 https。理由：无数据迁移（存量旧消息渲染时转换）、单入口覆盖全部 Markdown 渲染面（含普通 AI 回复复述链接；纯文本浮层除外，见 §5）、点击链路零特化 |
| D5 | uuidv7 目录名生成方 | agent 按 prompt 内嵌 python 片段现场生成——页面是否产出由 background agent 现场决定，relay 预生成会空耗；无新链路 |
| D6 | prompt 注入面 | mission 短版（能力声明）+ background 全版（执行规范）——主 agent 决定 job_request 内容（"要产出 web 页面"必须写进 spec），不知道能力就派不出正确任务 |

**否决/备选记录**：

- D1-B 零配置恒挂载 derived 路径 / D1-C 复用 `bff.enabled`——webroot 与 MyLife 无依赖关系，且 production 需要独立开关；
- D2-B 静默跳过（mylife 风格）/ D2-C 不自建、缺失即 fail-fast——自动建目录后只剩「父目录 `/opt/lvdata` 不存在」一种失败形态，与 fail-fast 无冲突；
- D3-B 一律系统浏览器——丢失应用内错误重试/返回体验，且与既有 markdown 链接交互不一致；
- D4-B 点击期在 MarkdownContent 内置转换（原否：隔离层注入环境依赖）／ D4-C 服务端存储前转换（需部署 `base_url` 配置、存量旧消息失效）／ D4-D App 收信时转换（多写入路径、不满足"存储 raw"）——二次修订后取"**渲染期 + CompositionLocal 注入**"：环境经 Local、签名不变；存储零改动（§3.4）；
- D5-B relay 预生成注入 spec / D5-C 专用脚本工具——预生成会空耗（页面未必产出），工具化无收益；
- D6-B 仅 background 注入——主 agent 不知道能力，不会在 `spec.task` 里写"产出 Web 页面"。

**2026-10-01 review 修正（D1 默认值）**：D1 原案 `enabled=True` 默认开 → **修正为默认 `False`**。
依据（证据见 §2.2）：单测/集成测试以 `AppConfig()` 默认值装配 app，且集成测试跑**真实 lifespan**
（`services/tests/integration/agent_relay/test_application_wiring.py:143`；`test_gzip.py:22`、
`test_sanity.py:17`）——默认开启叠加"启动期 mkdir + fail-fast"会把 `/opt/lvdata` 变成这些本可 tmp 隔离
测试的硬依赖（新 clone 机器上直接跑 `pytest` 即失败）。修正后 D2（mkdir + fail-fast）语义不变，作用域缩到
显式启用后；dev 部署在 `configs/dev/server.yaml` 显式开启。

**2026-10-01 合并记录**：本文合并 d4/k3 两稿。k3 稿的决策表/里程碑/测试矩阵表述被吸收（§6/§9）；
k3 稿与实测不符的三处已纠正：①尾斜杠机制（实测 Starlette 1.6.0 为 307 重定向，非"直接服务不重定向"，
见 §3.1 已知行为）；②"16KB 截断对链接无影响"（实为保头去尾，链接须前置，见 §2.3/§3.3）；
③`enabled` 默认 True（修正为 False，见上）。

**2026-10-01 二次修订（D4'：转换前移至渲染期）**：拍板 = App 交给 Markdown 渲染前做 `livachat_url://`
转换（`MarkdownContent` 内）；**Room 与服务端全链路保持原文（不做转换）**；点击链路回归普通 https。
对比上一版（点击期在 NavGraph 转换，已随 commit 2a64cbe 实施）：① 存量旧消息无需迁移——渲染时一律转换；
② 单入口覆盖全部 Markdown 渲染面（含普通 AI 回复复述链接的场景；纯文本浮层除外）；③ 点击链路回退为既有行为（删除 `openLink`/
`resolveServerLink`/白名单扩展）；④ server / prompts / 协议零改动（prompt "客户端会转成实际地址"的措辞
继续成立）。待实施，清单见 §7 #8–#11（新版）、§9 步骤 6、§10 二次修订记录。

**2026-10-10 三次修订（连接 IP / 渲染链接域名映射；已实施）**：`ServerOption.PUBLIC` 回退为 IP
（`wss://<server-host>:8080/v1/ws/stream`）；渲染基址对 host 命中映射 `<server-host> → <public-domain>`
的服务器改用域名（未命中回退 IP）。转换规则本体不变（§3.4 协议/校验/截断界）。根因：系统浏览器打开
渲染链接时严格校验证书 hostname，dev 证书仅签 DNS SAN；连接侧与 WebView 均忽略证书、IP 直连不受影响。
映射**仅**作用于渲染基址（`provideMylifeUrl` / `checkService` 不映射）。方案与证据见
`docs/setting_server_address.md`（已实施：`ServerLinksTest` 28/28、全量 app 单测 716 例 0 失败、assembleDebug 通过）。

---

## 5. 边界矩阵

| 场景 | 行为 |
|------|------|
| webroot 关闭（enabled=false）/未配置 | 不挂载；prompts 无 web 段（占位符不残留）；agent 不会产出链接 |
| webroot 未显式开启的生产部署（默认态） | 功能整体不激活（同上一行）；上线需 §9 步骤 5 的部署动作——无坏链风险 |
| 链接指向不存在的目录/文件 | StaticFiles 404 → WebView 错误占位（既有 onReceivedHttpError 路径） |
| `/webroot/<uuid>` 无尾斜杠 | 目录含 `index.html` → 307 → `<uuid>/`（WebView 跟随）；无 `index.html` → 404 |
| markdown 中裸文本 `livachat_url://…`（未用 `[文字](…)` 链接语法） | 渲染前转换后成 https → Linkify（`WEB_URLS`）自动成链、变为可点（较改造前为增益）；prompt 仍强制链接语法（防呆） |
| 非 webroot 的 `livachat_url://`（如未来 `audio/`） | 转换不命中（前缀不匹配）→ 原样保留 → 渲染为普通文本不可点 |
| 畸形链接（rest 空首段非 uuidv7、含空白/`..`/`\`） | 不转换、原样保留 → 渲染为不可点文本（同现状语义） |
| scheme 大小写变体（如 `Livachat_Url://`） | 转换大小写不敏感匹配（URI scheme 惯例）；rest 原样保留 |
| 结果文本被 16384B 截断 | prompt 要求链接放结果前部；截断发生在尾部时链接仍在 |
| 旧版 App（无转换逻辑） | markdown 渲染正常、链接点击无响应（既有白名单丢弃，无 crash）；无协议变更，无需版本协商 |
| 存量旧消息（Room/历史存 raw `livachat_url`） | 渲染期转换 → 照常可点（**无数据迁移**；不必重跑 job） |
| 复制消息文本（长按选择） | 复制得到渲染后的 https；存储仍 raw（渲染文本 ≠ 存储文本） |
| `serverBaseUrl` 为空（配置异常/预览/单测） | 不转换 → 链接保持纯文本不可点（优雅降级） |
| 未走 Markdown 的纯文本渲染面（相机/视频浮层 `lastAiText`、非 final 临时文本） | 不经过转换 → 显示 raw `livachat_url://` 原文（不可点）；与现状一致 |
| 页面引用绝对路径 `/xxx.js` | 404（页面自身 bug）→ prompt 强调相对路径 + **server 访问验收门禁（N6）会拦截**；联调验收项 |
| Hermes 未注册 playwright-dev MCP（或等价 `--ignore-https-errors` server） | 无可用浏览器自动化 → 无法完成"经 server 访问"验收（N6 门禁）→ **不产出带链接的结果**；部署前置项必须先行（§3.3/§9 步骤 0；plain `playwright` 因自签证书报 `ERR_CERT_AUTHORITY_INVALID`） |
| 验收未通过（页面 404/白屏/交互坏） | agent 修复后重验；最终仍未通过 → 结果不含 livachat_url 链接（如实说明，宁缺勿坏链） |
| 页面需要外部 CDN | 受手机网络环境影响；prompt 指引自包含（vendor 进目录）为默认 |
| 同一页面多次迭代 | 每版新 uuid 目录（N2），旧链接不失效；磁盘只增（见 §8 开放问题） |
| `/webroot` 下被写入非页面文件（人/进程） | 无感知，静态照服；无清理机制（开放问题） |

---

## 6. 测试计划

| 层 | 位置 | 用例 |
|----|------|------|
| server 单测（新） | `services/tests/unit/app/test_webroot.py` | `_mount_webroot`（tmp_path 配置）：enabled 挂载后 `GET /webroot/<uuid>/index.html` 200；不存在路径 404；目录自动创建；root 空串回退 base_dir；**默认配置（`AppConfig()`，enabled=false）不挂载且无 FS 副作用**；路径穿越用 **`%2e%2e` 编码**请求（裸 `..` 会被 httpx/TestClient 在客户端规范化，测不到服务端）→ 404 |
| prompts 契约 | `services/tests/unit/agent_relay/test_hermes_contract.py` | `_render_mission`/`_render_job_task`：webroot_dir 有值 → 含目录/链接格式/playwright-dev 字样；空 → 段整体消失、`$webroot_hint` 占位符不残留 |
| App 单测（新） | `apps/android/LivaChatApp/src/test/.../ui/navigation/ServerLinksTest.kt` | `convertLivachatUrls` 表驱动（合法：带/不带尾斜杠、深层路径 `static/`、query、大小写 scheme/uuid、多枚链接、`[t](…)` 上下文；非法保留：v4 uuid、`..`、空白、非 webroot host；空 base → 原样且幂等；token 截断：尾随 `。`/引号/`>` 剥离后仍替换、剥离字符留原位）；`serverBaseUrl` 推导：PUBLIC `wss://<server-host>:8080/...` → `https://<public-domain>:8080`（IP→域名映射，2026-10-10 修订；同日映射暂注释停用，PUBLIC/组合两例随之注释跳过）；LAN `wss://<lan-host>:8080/...` → `https://<lan-host>:8080`；wss/ws 与默认端口/显式端口 |
| App 回归 | 既有 MarkdownContent/WebView 测试 | http(s) 链接行为不变；渲染前转换不改含 http 文本；点击链路为普通 https（零特化） |
| 联调（真机） | dev 环境 | 语音要求"做个 XX 网页" → job 产出 → 结果 ai_text 含 `livachat_url://webroot/<uuid>/` → 气泡/全屏视图中点击 → 应用内 WebView 打开、交互可用（JS）→ 顶栏"在浏览器打开"验证系统浏览器；**含：直接点存量旧消息**（Room 存 raw → 渲染转换，验证无迁移） |
| 反向用例 | 真机 | 链接指向不存在页面 → 错误占位可重试；webroot 关闭时 agent 不再声称可建页面；**验收门禁（N6）：构造验收不通过场景（如资产用绝对路径 / 页面 404）→ agent 不输出链接** |
| 全量回归 | `services/tests` + `apps/android` | `make server-test` 全绿；Android 单测/构建绿 |

---

## 7. 改动清单

| # | 文件 | 改动 |
|---|------|------|
| 1 | `services/shared/config/models.py` | +`WebrootConfig`（`enabled` 默认 False）；`AppConfig` 挂载字段 |
| 2 | `services/app/application.py` + `configs/dev/server.yaml` | +`_mount_webroot`（enabled 时解析路径/mkdir/mount）；装配处注入 `hermes.webroot_dir`；dev yaml `+webroot: {enabled: true}` |
| 3 | `services/agent_relay/adapters/hermes.py` | +`webroot_dir` 属性；+`_webroot_hint`；`_render_mission`/`_render_job_task` 注入 |
| 4 | `services/agent_relay/prompts/hermes_monitor.md`、`job_task.md` | +web 页面小节（§3.3 文案） |
| 5 | `services/tests/unit/app/test_webroot.py`（新）、`services/tests/unit/agent_relay/test_hermes_contract.py` | §6 用例 |
| 6 | `docs/hermes_adaptor_design_v2.md` §4 | 规范副本同步（+1 条） |
| 7 | `apps/android/.../di/AppModule.kt` | （已实施）`@Named("serverBaseUrl")` 保留——二次修订无新增 |
| 8 | `apps/android/.../ui/navigation/ServerLinks.kt` | （二次修订后）`+convertLivachatUrls` + `LocalServerBaseUrl`；删点击期 `resolveServerLink`/`isLivachatWebrootUrl`（前缀常量降为 convert 私有实现，`UUIDV7` 正则保留给校验）；保留 `serverAddress`/`serverBaseUrl` 推导 |
| 9 | `apps/android/.../ui/navigation/NavGraph.kt` + `MainActivity.kt` | NavGraph 删 `serverBaseUrl` 参数与 `openLink`（4 处回原 lambda）；MainActivity 保留注入、改 `CompositionLocalProvider(LocalServerBaseUrl)` 提供 |
| 10 | `apps/android/.../ui/components/MarkdownContent.kt` | linkResolver 回退 `isHttpUrl`；渲染前接线 `convertLivachatUrls`（renderKey 用转换后文本） |
| 11 | `apps/android/.../test/` | §6 App 用例（convert 表驱动） |
| 12 | `docs/install_design.md`（注记）/ 本文档 | `/opt/lvdata/webroot` provision 说明；**生产开启动作**（部署配置显式 `webroot: {enabled: true}`）；Hermes 侧 Playwright MCP 注册与冒烟说明；实施后回填 |

> 注：#1–#6 已实施且本修订**零改动**；#7–#11 为 2026-10-01 二次修订后的 App 面（点击期实现 2a64cbe 被取代）。

---

## 8. 风险与开放问题

| # | 级别 | 风险/问题 | 缓解 / 倾向 |
|---|------|----------|------------|
| R1 | 中 | prompts 三处清单同步漏改（名字/时区的历史教训） | §3.3 明列同步项 + 契约测试锁两态渲染 |
| R2 | 中 | 同源无鉴权（页面 JS 可达 `/api/mylife`、内置 dev token 可连 WS） | dev 接受；备选：独立端口/子域或 CSP 头——**留待 Phase 2 鉴权一并收口** |
| R3 | 中 | Hermes 机器无 node/构建工具 → 复杂框架方案不可行 | prompt 默认零构建静态实现；构建工具可用性由 agent 现场确认（部署项记录） |
| R4 | 低 | agent 写绝对路径资产 → 页面 404 | prompt 强调 + 联调验收项；错误占位可重试 |
| R5 | 低 | 结果超 16384B 截断丢链接 | prompt 要求链接靠前；不动截断配置 |
| R6 | 低 | hermes 与 server 跨机部署时目录不可写 | 现有架构本就要求同机（chatlog 本地路径，`hermes_adaptor_design_v2.md` §9 Q6）；跨机属该开放问题的一部分 |
| R7 | 中 | **验收门禁（N6）依赖浏览器自动化**（`playwright-dev` MCP）——未部署则 web 页面任务无法完成验收、不产出链接 | **§9 步骤 0 冒烟前置**（先于 prompts 生效）；门禁在 prompt 层（agent 自检），宁缺勿坏链；如需更强约束见 O6（本期不做） |
| O1 | — | 页面保留/GC（磁盘只增，无删除策略） | 本期不做；后续可加按时间清理脚本（人工/定时） |
| O2 | — | `.../static/` 示例的确切语义 | 按通用路径 pass-through 处理（rest 任意层级），无需特判 |
| O3 | — | iOS 客户端对齐 | `apps/ios/` 仅 `.gitkeep`；实现时复用同一转换规则（§3.4 纯函数语义） |
| O4 | — | 是否要页面入口聚合（时间线/列表） | 本期不做，链接仅存在于 job 结果文本 |
| O5 | — | `livachat_url` scheme 含下划线（非 RFC 合法字符） | 全链路用字符串前缀匹配、不依赖严格 URI 解析器；文档明确记录 |
| O6 | — | 验收门禁是否需要在 relay/server 侧强制（如投递前探测链接可达） | 本期门禁在 prompt 层（agent 自检）；server 侧强制属后续评估（避免引入新链路） |

---

## 9. 实施顺序与里程碑（预估）

| 步骤 | 内容 | 预估 |
|------|------|------|
| 0 | **部署前置冒烟（最先做）**：Hermes 注册 `playwright-dev` MCP（`--headless --ignore-https-errors`）；用 `/opt/lvdata/webroot` 下临时测试页验证 MCP 可打开 `https://127.0.0.1:8080/webroot/<临时uuid>/` 并渲染（自签证书直接通过）；确认 background shell 有 `python3` | 0.25d |
| 1 | **Server 挂载 + 配置 + 单测**（§7 #1/#2/#5-server）——含 dev yaml 显式开启；不依赖 prompt/App，可独立验收 | M1 ~0.5d |
| 2 | **Prompts 渲染 + 文案 + 契约测试 + §4 同步**（§7 #3/#4/#5-contract/#6） | ~0.5d |
| 3 | **App 转换 + 接线 + 单测**（§7 #7-#11）——纯函数先行，再接线 | M2 ~0.5–1d |
| 4 | **联调（真机）**：dev 启动 → 语音要求建页面 → 结果链接 → WebView 打开 → 交互验证（§6 联调项 + 反向用例） | M3 ~0.5d |
| 5 | **部署与文档回填**：生产部署显式开启 webroot（`webroot: {enabled: true}`）；`/opt/lvdata/webroot`（已建）；云服务器同机部署确认；本文实施记录 + `hermes_adaptor_design_v2.md` §4 + install 注记 | ~0.25d |
| 6 | **（二次修订 D4'，已实施）App 转换前移至渲染期**（§7 #8–#11 新版）：删点击期链路 → `+convertLivachatUrls` + `LocalServerBaseUrl` → MarkdownContent 接线 → 测试重写（convert 表驱动） | ~0.5d |

**出口条件**：server 单测全绿 + prompts 契约测试全绿 + App 单测全绿 + 真机联调（含反向用例）通过 +
文档同步完成。

## 10. 实施记录（2026-10-01）

**已实施（§7 清单 #1–#11；§9 步骤 1–3）**：

| 块 | 落地 | 验证 |
|----|------|------|
| Server | `WebrootConfig`（`enabled` 默认 False）`shared/config/models.py`；`resolve_webroot_dir`/`_mount_webroot` + lifespan 注入 `hermes.webroot_dir`（`app/application.py`）；`configs/dev/server.yaml` 显式开启 | `tests/unit/app/test_webroot.py` **8 例**：挂载 200 / 不存在 404 / 无尾斜杠 307 / 自动建目录 / root 空串回退 / 默认配置不挂载且无 FS 副作用 / 父目录缺失启动 fail-fast（RuntimeError 带目录上下文）/ `%2e%2e` 穿越 404；`tests/unit` 全量 **1142 passed**（1 skipped） |
| Prompts | `hermes.py::_webroot_hint`（mission 短版 + background 全版，关闭整段消失）+ 两模板 `$webroot_hint` 占位；`docs/hermes_adaptor_design_v2.md` §4.3 #7、§4.4 同步 | `test_hermes_contract.py` +4 例（开/关两态、占位符不残留、playwright-dev/相对路径/链接格式断言），**29 passed**；渲染文案与 §3.3 逐字目检一致 |
| App | `ui/navigation/ServerLinks.kt`（前缀常量 / `isLivachatWebrootUrl` / `resolveServerLink` / `serverAddress` 推导）；`AppModule` 抽共享函数 + `@Named("serverBaseUrl")`，`provideMylifeUrl` 与 `SettingsViewModel.checkService` 收敛同源；`NavGraph` 4 处 `onOpenLink` 收口 `openLink`；`MainActivity` 注入透传；`MarkdownContent` linkResolver 放行前缀 | `ServerLinksTest` **24 例**表驱动全绿（合法：尾斜杠/深层/大小写/query；非法：非 v7/`..`/空白/反斜杠/非 webroot）；App 全量单测 BUILD SUCCESSFUL；`compileDebugAndroidTestKotlin` green（**注：该行记录的点击期实现已被二次修订 D4' 取代，见下**） |

**2026-10-01 二次修订（D4'：转换前移至渲染期；已实施）**：

- 拍板：转换时机 = **App 交给 Markdown 渲染之前**（`MarkdownContent` 内、交 Markwon 前一步）；
  Room / JobStore / chatlog / WorkingMemory / 推送**全部保持原文（raw）**；点击链路回归普通 https。
- 理由：无数据迁移（存量旧消息渲染时转换、照常可点）；单入口覆盖全部 Markdown 渲染面（含普通 AI 回复复述链接；纯文本浮层除外）；
  server / prompts / 协议零改动（"客户端会转成实际地址"措辞继续成立）。
- 实施面：§7 #8–#11、§9 步骤 6——`ServerLinks.kt`：+`convertLivachatUrls`（截断界含 `)`/`]`/`>`/`"`/`'`/
  非 ASCII + 剥尾部 ASCII 标点；非法片段原样保留，空 base 幂等降级）+`LocalServerBaseUrl`，删点击期
  `resolveServerLink`/`isLivachatWebrootUrl`；`MarkdownContent` 渲染前接线（renderKey 用转换后文本）、
  linkResolver 回退 `isHttpUrl`；`NavGraph`/`MainActivity` 回退原 lambda + `CompositionLocalProvider`；
  服务端与 prompts 零改动。
- 验证：`ServerLinksTest` **27 例**表驱动全绿（合法 / 截断界（尾随 `。`/`,`/引号/`>`）/ 非法保留（v4、`..`、
  非 webroot、空 rest、非法后接合法、非法且带尾随上下文原文保留）/ 空 base 与幂等 + `serverBaseUrl` 推导）；
  App 全量单测 BUILD SUCCESSFUL；`compileDebugAndroidTestKotlin` green。真机项：直接点**存量旧消息**
  （无迁移回归）——待执行。

**联调增量（2026-10-01）**：首轮真机 job（12:37 done/delivered）端到端跑通——结果链接在首行、
`GET /webroot/<uuid>/` 200、页面渲染与交互经 agent 浏览器截图 + vision 验证。期间发现 plain `playwright`
server 未忽略自签证书（`ERR_CERT_AUTHORITY_INVALID`，agent 自行绕行多耗 ~2min）；background 文案已改为
显式使用 `playwright-dev`（已随 Hermes 配置加载：mcp-stderr 12:57:59 `starting MCP server 'playwright-dev'`）
并注明不要用其他 playwright server；契约测试同步（+`--ignore-https-errors` 断言）。
注意：Hermes 注册的工具名会把 server 名里的 `-` 规范为 `_`（实际为 `mcp__playwright_dev__*`）——prompt 只
引用 server 名 `playwright-dev`、不写工具名前缀（避免随 Hermes 版本漂移）。

**§9 步骤 0/4 验证进展（同日补记）**：
- 步骤 0（MCP 冒烟）：CLI 会话 12:58 以 `playwright-dev` 对 `https://<lan-host>:8080/webroot/<uuid>/`
  完成 navigate / screenshot / snapshot / console 全流程，无证书错误 ✓。
- 步骤 4（真机）：13:24:32 设备 logcat `LivaChatWebView` 记录 `loadUrl → pageStarted → pageFinished`
  （`https://<lan-host>:8080/webroot/<uuid>/`，无 mainFrame 错误）——`livachat_url://` 转换 + 应用内
  WebView 打开链路真机验证通过 ✓。

**剩余（部署/真机事项）**：

- **§9 步骤 0（playwright-dev MCP 冒烟）**：CLI 侧已过（12:58 navigate/screenshot/snapshot 成功，无证书
  错误）；余量确认 = 下一轮页面任务（background agent 侧）首验即过。
- **§9 步骤 4（真机联调）**：正向链路已过（12:37 结果送达 + 13:24:32 真机点击 → WebView `pageFinished`
  无错误）；剩余：历史/时间线入口各点一次、反向用例（404 页面、N6 未过不产链）。
- **§9 步骤 5（生产部署）**：部署配置显式 `webroot: {enabled: true}`；install 注记已加
  （`docs/install_design.md` §1.6）。
- **验证口径**：全量 `make server-test`（= `pytest -q`）的 integration 套件依赖本机 infra
  （Redis:6379 / Postgres:5432，`make server-infra-up`）；本机 Docker(OrbStack) 未运行，相关文件为
  环境性失败（已核实为 redis 连接拒绝，与本次改动无关）——**infra 起后需复跑全量确认**。

---

*Design version: 2.1（含 2026-10-01 二次修订 D4'：转换前移至渲染期，待实施）| Created: 2026-10-01 | 基于 codebase 逐条核查（codegraph + 源码直读 + 安装版 Starlette 1.6.0 实测）*
