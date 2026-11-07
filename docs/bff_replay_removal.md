# BFF replay 删除方案

> 需求来源：用户 2026-09-22 口述——「删除 bff replay，其他不要动。给出计划，等待 review。」
> 基础事实全部取自当前代码（含 file:line）与全仓 grep，未做推测。
> 状态：**已实施（2026-09-22）** —— E1/E2/E3 按 §5 推荐 (a)/(a)/(a) 执行；V1/V2/V3/V5 全绿（V3 的 2 项失败经 HEAD 对照证为存量），V4 待上线前执行；**未提交**（工作区留待 review）。详见 §8。
> **本稿修订（2026-09-22 review 后）**：docs 命中面由「6 处引用」更正为 **14 文件 / 367 行**（新增 §5.1 附录）；§4 补 2 条影响项（`window.static` 无消费方、`session_json_refactor` 裁决 B 遗留项随删消灭）；§6 V4 决策规则改为「按请求形态归因」（原「非零即停手」与 R5 自相矛盾）；§2 补 `__pycache__` 物理删除细节；§3 conftest 注释登记补全至 3 处；R3 量级更正；**§8.7 两行订正（`range_invalid` 定性、mypy 归属）+ 新增 §8.8 独立复核**（第二轮）。
> 范围：**仅删除 `services/bff/replay/` 及其直接接线**。graph/timeline/search/media、Android/iOS/Web、lvdata-mcp、
> 其余 wire schema、`docs/**`（含 `REPO.md` 的 prose 提及）一律不动（§3 逐条列出「不动的理由」）。

---

## 1. 现状（代码事实）

### 1.1 接口形态

| 事实 | 位置 |
|---|---|
| `GET /api/mylife/replay`，参数 `ts_ms` / `event_ref` / `speed`(1–64，默认 8)；两者全空 → 400 `bad_request` | `services/bff/replay/router.py:11-21` |
| 会话解析：`event_ref` 精确命中 → 所辖 session；否则 `resolve_session_at(ts_ms, device_uuids)` 四步阶梯（①精确 ②containment ③最近 ④None）；解析出空 timeline → 404 `not_analyzed` | `services/bff/replay/service.py:13-41` |
| 响应 `ReplayResponse{schema_version, session{session_date}, duration_ms, cues[]}`；cue 由 chat/audio/video/window/agent_job 五类事件映射（`_event_cue`） | `service.py:47-108`；`services/bff/common/schemas.py:168-186` |
| 设备白名单已落实（P6-d）：`resolve_session_at(ts_ms, store.device_uuids or None)` | `service.py:30`；`store.py:281` |

### 1.2 消费方核查（谁在用）

| 端 | 结论 | 证据 |
|---|---|---|
| Android | **已删净（2026-09-22）** | 9bafc10「星图/Replay 整栈删除」：`ui/screens/mylife/replay/` 7 文件 + `MylifeApi/Repository` 的 replay + `Replay*` DTO 删除；测试删整类 2 个（`ReplayViewModelTest`/`ReplayTimelineTest`，含在该批「5 类 47 例」内）+ replay 相关用例（`MylifeApiTest`/`MylifeRepositoryTest` 各若干）。现存 4 处 Kotlin 命中**全部是注释**（`Routes.kt:13`、`TimelineScreen.kt:287`、`RoutesTest.kt:13`、`TimelineViewModelTest.kt:148`），零调用 |
| iOS | 从未有 | `apps/ios` 对 replay 零命中 |
| Web `apps/web/mylife` | 残留且**在生产可能仍挂着**（三重） | ① **决策死刑**：`docs/mylife/android_change_v3.md:4/466/540`（2026-08-26「Web app 不再使用」，兼容约束全解除；「代码保留不维护、删除属单独任务」）；② **代码早已调不通**：`hooks.ts:61` 打 `GET /api/mylife/replay/{sessionId}`（v1 路径式，87ad252），41f78d2 改为锚点式后 Web 未跟进 → 404；③ **但静态托管仍在**：`application.py:334/339-352 _mount_web_dist` 按 `bff.web_dist`（config 默认 `apps/web/mylife/dist`，`shared/config/models.py:238`）挂到 `/mylife`，dist 存在即挂——工作区 `apps/web/mylife/dist/` **确实存在**（被 `.gitignore:9` 忽略）⇒ 部署机若有该构建产物，Web 的 Replay tab 是可点但必 404 的坏链（**现状**，非本次删除引入；README「生产由后端托管 /mylife」） |
| 其他服务/脚本/配置 | 无 | 代码面：全仓 grep `bff/replay\|bff\.replay\|replay_router`（排除 docs 与模块自身）仅剩 §2 的待删引用；`configs/`、`tools/`、`packages/`（含 `api-schema`，零 replay DTO）、`scripts/`、`agents/`、`services/app/` 零命中。**该 pattern 的边界**：prose 提及（`REPO.md:77`）不在命中内，属「只登记不改」（§3）；docs 侧命中面见 §5.1 |
| 线上流量 | **未验证（可验证）** | 删除前可 grep 线上日志 `replay_resolve step=`（`store.py:315/336/346/348`、`service.py:25/28`）或 access log 中 `/api/mylife/replay` 计数 → 见 §6 V4 |

### 1.3 「看似共用、实为 replay 专属」的符号（已逐个核引用）

| 符号 | 位置 | 其他引用 | 结论 |
|---|---|---|---|
| `LvdataStore.event_by_ref` | `store.py:267-279` | 仅 `replay/service.py:23` | replay 专属 → 可删 |
| `LvdataStore.resolve_session_at` | `store.py:281-351` | 仅 `replay/service.py:30`（+本节将删的测试） | replay 专属 → 可删 |
| `_parse_iso_ms` | `store.py:41-47` | 仅 `resolve_session_at` 内部（:314/:323/:330） | 随删后成死码 → **E1** |
| `_STATUS_BY_CODE["not_analyzed"]` | `router.py:20` | 唯一抛出点 `replay/service.py:41` | 随删后成死配置 → **E2** |
| `LvdataStore.get_session` | `store.py:142` | timeline:246、graph:351/386 | **保留（不动）** |

---

## 2. 删除清单

| # | 位置 | 动作 | 量 | 依据 |
|---|---|---|---|---|
| 1 | `services/bff/replay/`（`__init__.py` 0B、`router.py` 21 行、`service.py` 124 行） | 整目录删除 | 145 行 | 目录唯一内容即 replay |
| 2 | `services/bff/router.py:9`（import `replay_router`）、`:16`（`include_router`） | 删 2 行 | 2 行 | 接线 |
| 3 | `services/bff/common/schemas.py:168-186`（`# -- replay` 块 = 文件尾：`ReplaySessionInfo`/`ReplayCue`/`ReplayResponse`） | 删 19 行 | 19 行 | 仅 replay 引用；`FlatEventModel` 为共用基类，保留 |
| 4 | `services/bff/common/store.py:265-351`（`# -- replay 锚定解析（§3.2）` 段：`event_by_ref` + `resolve_session_at`） | 删 87 行 | 87 行 | §1.3 零其他引用 —— **E1 拍板** |
| 5 | `services/bff/common/store.py:41-47`（`_parse_iso_ms`） | 删 7 行 | 7 行 | 同上 —— **E1 拍板** |
| 6 | `services/bff/router.py:20` 内 `"not_analyzed": 404` 一键 | 删 | 1 键 | 唯一抛出点被删 —— **E2 拍板** |
| 7 | `services/bff/common/store.py:14`（`from datetime import datetime`） | 删 1 行 | 1 行 | **全文件唯一使用者是 `_parse_iso_ms`（:45）**——E1 选 (a) 时必须同删，否则留未用导入（ruff F401；`.github/workflows/*.yml` 均为 0B 空文件、CI 未接线，不会自动拦截）—— **E1 拍板** |
| 8 | `services/tests/unit/bff/test_replay_api.py`（213 行 / 17 例） | 整文件删除 | 213 行 | 全为 replay 用例（含 store 阶梯 2 例） |
| 9 | `services/pyproject.toml:161`（`"bff/replay/router.py" = ["B008"]`） | 删 1 行 | 1 行 | ruff 按文件豁免指向将不存在的文件（必删项，非选项） |

净删除 ≈ 475 行（含 E1 关联的 `datetime` 导入行），零新增、零修改逻辑。

**实施细节（#1 目录）**：`services/bff/replay/` 下有未跟踪的 `__pycache__/`（`.gitignore:1` 忽略），`git rm -r` 不会删它 ⇒ 用**物理删除**（`rm -rf services/bff/replay`）。行为无害（Python 不从 `__pycache__` 单独导入 sourceless 模块，删后亦无 import 方），但不物理删则残留一个只含 `__pycache__` 的目录，且 V2 的 rg 会因 gitignore 跳过、看不见该残留。

---

## 3. 不动清单（附理由，防越界）

| 对象 | 理由 |
|---|---|
| `services/tests/unit/bff/conftest.py` | `SESS_F`（:19/:112/:164）虽为 replay 平局用例而建，但 **`test_graph_api.py:134` 依赖 SESS_F 事件**（该用例断言 `stats.sessions == 2`，删种子即红）→ 种子全部保留；`TS_C1` 被 timeline(:143/:475) 引用（search 侧只是 import、未使用，ruff F401 存量——见 §8.3）→ 一律不动。**注释语义变片面的共 3 处（:19、:163、:164），按「其他不要动」不改注释**，仅在此登记 |
| `store.get_session` / `FlatEventModel` / `BffError` 其余 code / `_PRIO_SQL` 等共用件 | timeline、graph、search 在用 |
| `services/tests/integration/bff/test_bff_smoke.py`（e2e marker） | 只打 overview/timeline/search，**不含 replay**；且它是「真 app（`app.main:app`）启动」的现成验证路径 → V3 复用，不改 |
| `services/bff/{graph,timeline,search}/**`、`services/bff/common/{media,topics,deps,timeutil}.py`、`services/app/**` | 与 replay 无引用关系（§1.2 grep 已证） |
| lvdata-mcp、Android、iOS、Web 全部代码 | 与 BFF 查询层解耦；Web 死代码的删除是另一项决策（`android_change_v3.md:540`） |
| `docs/**`、`REPO.md` | 本轮不改。docs 命中面核清 = **14 文件 / 367 行**（`rg -ic replay docs/` 口径，不含本文档；含少量「回放/重放」泛指用词，≠需改行数），逐文件清单见 §5.1；`REPO.md:77`（章节标题）为 prose 命中，只登记不改 |

---

## 4. 影响面

| 面 | 变化 | 说明 |
|---|---|---|
| Wire | `/api/mylife/replay` → 404（FastAPI 无路由默认行为） | 代码内消费方影响 = 0（Android 已删、iOS 无、Web 死代码本就 404）；**唯一例外**见 §1.2 ③：线上若挂着 Web 构建产物，其 Replay tab 仍可点但必 404——现状即如此，非本次引入 |
| OpenAPI | `/openapi.json` 少 1 路径 + 3 schema | 全仓未发现 openapi 消费方/快照测试 |
| 上游字段 | `window` 事件的 `static` 变无消费方 | `video_analysis.md:124-125/180` 记载其唯一外部消费方 = bff replay 的 window cue。删除后无人读；上游照写盘、schema 不动（与 §3「wire schema 不动」同口径，仅登记） |
| 已知遗留 | `session_json_refactor` 裁决 B 的「containment 上界 ∞」待修复项随删消灭 | 该文 :60/:138 记载此代价的唯一消费方 = `replay/service.py:30`；E1(a) 删后影响半径归零（**正向副作用**，可回答「那个待办哪去了」） |
| 错误码 | `not_analyzed` 不再可能出现（E2 决定枚举去留）；`bad_request`/`not_found` 仍由 timeline/graph/search 使用 | `router.py:20` 其余键不动 |
| 可观测 | `replay_resolve step=…` 日志（6 处：`store.py:315/336/346/348` + `service.py:25/28`）消失 | 可作为 V4 归因的反向验证点（删除后不再产生新形态流量；旧客户端只会留下路由未命中的 404 访问日志） |
| DB | 零迁移、零 schema 变化（store 只读） | 无数据面风险 |

---

## 5. 待拍板

| # | 项 | 选项 | 推荐 |
|---|---|---|---|
| E1 | store 的 replay 专属件（`event_by_ref` + `resolve_session_at` + `_parse_iso_ms`，共 94 行；**选 (a) 还需同删 `store.py:14` 的 `datetime` 导入**） | (a) 随删（b) 保留为孤儿代码 | **(a) 随删**——§1.3 已证零其他引用；保留即本仓惯例要清的「尾差死代码」。若求最小 diff 面，(b) 也自洽（功能零影响，`datetime` 导入保持原位不删） |
| E2 | `router.py:20` 的 `"not_analyzed": 404` 键 | (a) 删键 (b) 留键 | **(a) 删**——唯一抛出点随 replay 消失；Android 侧早已删该错误文案（`android_change_v3.md` §3.2-② P2） |
| E3 | docs 取代注（命中面 = **14 文件 / 367 行**，见 §5.1） | (a) 本轮不动 (b) 只给 2 处「结论被反转」加取代注（`android_timeline_hotslot.md:165`、`session_json_refactor.md:60/138`）(c) 全量取代注 | **(a) 不动**——贴合「其他不要动」；§5.1 已补全清单（原稿「6 处引用」是抽样：仅列 6 文件，且 `android_change_v3.md` 实际命中 103 行 > 所列 4 组行号）。若愿破例一处，**(b) 收益最高**：那 2 处不是行号陈旧，是结论被本次删除**反转**（兼容矩阵「无破坏」→ 破坏；待修复项 → 消灭）；其余 12 文件属措辞/行号陈旧，另批清即可。（§1.2 ③ 的 Web 生产坏链会提高取代注/Web 下线的价值，但那是 Web 存废决策，不入本轮） |

### 5.1 附录：docs replay 命中清单（`rg -ic replay docs/` 口径，2026-09-22 核）

| 文件 | 命中行 | 原 E3 | 性质 / 需注意 |
|---|---|---|---|
| `docs/mylife/android_change_v3.md` | 103 | 已列（抽样行号） | v3 契约本体；部分节已带「已随 §14 R3=C 删除」注 |
| `docs/mylife/mylife-frontend-android.md` | 68 | 已列（抽样行号） | v1 前端契约（历史记录；:113 仍写 v1 契约 `GET /replay/{session_id}`，:415 起为已删代码的目录树记录） |
| `docs/android_timeline_hotslot.md` | 47 | **漏** | **活跃契约**（状态「已实施 + 2026-09-22 全文评审」，§11 有 consolidation 待办）：:165 兼容矩阵行「新 bff × 旧 app 无破坏：旧点击进 Replay 仍有效」被本次删除**反转**（= R5 的书面化） |
| `docs/mylife/timeline-android-v2.md` | 44 | 已列（抽样行号） | 旧约定，已被 hotslot 文档取代（:153 起仍写 `navigate("mylife/replay?session=…")`「slot 是唯一 replay 入口」） |
| `docs/mylife/mylife-design.md` | 39 | 已列（抽样行号） | :521 仍记 v1 路径式路由 `GET /api/mylife/replay/{session_id}` |
| `docs/android_timeline_image_gallery.md` | 25 | 已列（抽样行号） | Gallery 交叉引用 replay 入口计数（四入口→三入口） |
| `docs/mylife/graph-android-v2.md` | 18 | **漏** | :30/:49 event 节点 → Replay 深链；头部已注「回放 tab 移除」 |
| `docs/android_timeline_topics.md` | 7 | 已列（抽样行号） | Q2 定案：非 topic 命中取代 Replay |
| `docs/video_analysis.md` | 6 | **漏** | :124-125/180 记载 `static` 唯一外部消费方 = replay window cue（已登记进 §4 影响面） |
| `docs/session_json_refactor.md` | 3 | **漏** | :60/:138 记载裁决 B（containment 上界 ∞）唯一消费方 = `replay/service.py:30`（已登记进 §4） |
| `docs/data/lvdata_mcp_service_v2.md` | 3 | **漏** | :716/722/858 上游 v2 契约的 replay 重锚决策记录 |
| `docs/video_analysis_extract_plan.md` | 2 | **漏** | :42 conftest 保留旧 schema 行的「验证图谱/replay 兼容」理由只剩 graph 一半 |
| `docs/mono-repo.md` | 1 | **漏** | :184 描述 web 三 tab（Timeline/Graph/Replay）且「已废止」 |
| `docs/data/lvdata_mcp_service_v2_plan.md` | 1 | **漏** | 计划稿（:197 graph/search/replay 适配） |

- 合计 14 文件 / 367 行（= 已列 6 文件的 286 行 + 漏登记 8 文件的 81 行；本文档自身 34 行不计）。
- 口径说明：行命中含少量非端点语义（「回放/重放」泛指），**不等于「需改行数」**；且 6 个「已列」文件的 E3 行号是**抽样**，不是穷尽（故「已列」也只保证性质判对，不保证行号全）。

---

## 6. 验证计划

1. **V1 测试**：本仓规范命令 = 仓库根 `make server-test`（= `cd services && pytest -q`）。**不可从仓库根直接给路径**：`services/pyproject.toml:60-61` 设 `testpaths=["tests"]`、`pythonpath=["."]`，且用例以 `from bff…` / `from tests.unit…` 导入 ⇒ 必须在 `services/` 下执行。定向验证：`cd services && pytest tests/unit -q --junitxml=/tmp/bff-replay-removal.xml`（本仓 pytest 输出有包装、摘要行不可信 → 解析 junitxml 计数）；全量含 integration/e2e markers（`test_bff_smoke.py` 需 `/opt/lvdata` + `services/.env`，缺失自跳过）。
2. **V2 残留 grep + 静态检查**：`rg -n "bff\.replay|bff/replay|ReplayResponse|ReplayCue|resolve_session_at|event_by_ref|_parse_iso_ms|not_analyzed" services/` → 期望 0 命中（`not_analyzed` 视 E2 结果；若 E1 选 (b)，`resolve_session_at`/`event_by_ref`/`_parse_iso_ms` 命中属预期）；再手工 `ruff check`（配置在 `services/pyproject.toml:69+`，F401 默认启用）确认无 `store.py:14` 这类未用导入——**CI 未接线（`.github/workflows/*.yml` 全为 0B），不会自动拦截**。
3. **V3 路由/OpenAPI + 真启动**：
   - 轻量：`cd services && python -c` 用「裸 `FastAPI()` + `bff.router.register(app)`」（与 `conftest.py:283-298` 的 `client` fixture 同构；注意它**不等于**真 app 的 `create_app`）建 app → 断言 `app.openapi()["paths"]` 无 `/api/mylife/replay`、`components.schemas` 无 `Replay*`，`TestClient(app).get("/api/mylife/replay")` → 404（404 与 openapi 断言都不触发依赖解析，无需 store/config 注入）。
   - 真启动（推荐，复用本仓现成路径）：`cd services && pytest tests/integration/bff/test_bff_smoke.py -q -m e2e`（其内部起 `uvicorn app.main:app`，覆盖删除后 app 仍可启动；需 `/opt/lvdata` + `services/.env`）；或手工 `.venv/bin/livachat-server` + `curl -k https://localhost:8080/healthz` + `/openapi.json`。
4. **V4（上线前，可选但建议）线上流量归因**：grep 线上日志 `replay_resolve step=` 或 access log `/api/mylife/replay` 近 N 天计数，并**按请求形态归因**：
   - 路径式 `/api/mylife/replay/<uuid>`（无查询串）→ Web dist 死代码（§1.2 ③，现状即 404）；
   - `?ts_ms=` / `?event_ref=` → 旧版 Android 安装包（9bafc10 之前的包；R5 已接受）。
   以上两类 = **预期行为，按 R5 放行**（UA/Referer 可辅助区分）。**无法归因到这两类的流量 → 停手回 review**（存在代码外消费方，R1 兑现）。原稿「非零即停手」与 R5 自相矛盾——旧包流量本就预期非零。
5. **V5 回归面**：timeline/graph/search/media 用例零改动且全绿（删除为纯减法，helper 无其他引用已在 §1.3 证）。

---

## 7. 风险

| # | 风险 | 说明 / 缓解 |
|---|---|---|
| R1 | **未知消费方**（外部脚本、调试习惯、老测试机上的 App） | 代码内已穷尽核查（§1.2）；唯一不可证伪项 → 由 V4 归因规则兜底。**无法归因到「Web dist 死代码 / 旧版 App」两类的流量 → 回 review 重议** |
| R2 | E1 选「保留」→ store 留 94 行死码 | 无功能风险，仅整洁度；与本仓清理惯例不符 |
| R3 | docs 与代码不一致扩大 | 量级 = **14 文件 / 367 行**（§5.1），非原稿举的单点例：除 `mylife-design.md:521`（记 v1 路径式路由）外，`android_timeline_hotslot.md:165` 的兼容矩阵行会被本次删除**反转**，`session_json_refactor.md:60/138` 与 `video_analysis.md:124-125/180` 的「唯一消费方」记载随之失效 → E3 的取舍（已登记，本轮不动） |
| R4 | OpenAPI 收缩影响外部集成 | 未发现消费方；BFF 为内部只读面，无对外承诺文档（`docs/api/` 为空） |
| R5 | Android **老版本 App** 点 Replay 入口 | 旧包的入口会**真实打到 BFF** `/api/mylife/replay`（不是纯本地路由问题）→ 删除后 404，表现为回放页报错。影响范围 = 仍装着旧包的内测设备（9bafc10 记录「已 deploy 至测试机」）⇒ 已知可接受；新包入口随屏栈已删，不再产生该调用。**此条正是 V4 要覆盖的情形，且属 V4 归因规则中「预期放行」的一类（`?ts_ms=`/`?event_ref=` 形态，§6 V4）** |

---

## 8. 实施记录（2026-09-22 执行）

**执行范围**：E1/E2/E3 = 按 §5 推荐 **(a)/(a)/(a)**；代码面仅动 §2 清单 + 段间空行规范化（差异见 8.1）。

### 8.1 改动落点（`git status` 实测）

| 文件 | 变更 |
|---|---|
| `services/bff/replay/`（3 文件） | 物理删除（`rm -rf`，连带未跟踪 `__pycache__`）—— **-145 行** |
| `services/tests/unit/bff/test_replay_api.py` | 删除 —— **-213 行** |
| `services/bff/router.py` | **-3 +1**：replay import / `include_router` / 旧 `_STATUS_BY_CODE` 行，新增去掉 `not_analyzed` 的行 |
| `services/bff/common/schemas.py` | **-21**：replay 块 19 + 段前 2 空行（文件现以 `EntityResponse.appearances` 行结尾） |
| `services/bff/common/store.py` | **-98**：§2#4 段 87 + 段间空行 1 + `_parse_iso_ms` 7 + 其后 2 空行 + `datetime` 导入 1 |
| `services/pyproject.toml` | **-1**：`bff/replay/router.py` 的 B008 豁免行 |

合计 **删除 481 行 / 新增 1 行**（净 -480）。与 §2「净删 ≈ 475 行」差 6 行 = 5 处段间空行规范化（schemas 2 + store 3）+ 1 行改写计法差异（`_STATUS_BY_CODE` 记为「改」而非「删 1 键」）。

### 8.2 V1/V5 测试（junitxml 计数，非摘要行）

| 命令 | 结果 |
|---|---|
| `cd services && .venv/bin/python -m pytest tests/unit/bff -q --junitxml=/tmp/bff-unit-bff.xml` | **131 passed / 0 failed / 0 error / 0 skipped**（删前 = 148 = 131 + 被删 17） |
| `cd services && .venv/bin/python -m pytest tests/unit -q --junitxml=/tmp/bff-replay-removal.xml` | **1089 passed / 0 failed / 0 error / 0 skipped**；bff 分布 timeline 37 / search 55 / graph 27 / media 9 / gzip 3 ⇒ **V5 零改动全绿** |
| 额外（超出 §6 范围）`pytest tests/unit tests/integration` | unit 侧 0 失败；integration 侧失败/错误 = `tests/integration/agent_relay/*`（69 失败 + 97 错误，根因 = Redis/PG 未起：`redis.exceptions.ConnectionError … 6379`，即 pyproject 所注「需 docker-compose 基础设施」；该目录对 bff/replay **零引用**）+ `tests.integration.bff` 2 项（= §8.4 的 smoke 存量，HEAD 复现）⇒ 全部与本次删除无因果 |

### 8.3 V2 残留 grep + 静态检查

- `rg -n "bff\.replay|bff/replay|ReplayResponse|ReplayCue|resolve_session_at|event_by_ref|_parse_iso_ms|not_analyzed" services/` → **0 命中** ✓
- `ruff check bff/router.py bff/common/schemas.py bff/common/store.py pyproject.toml` → **All checks passed**（无 `store.py:14` 类未用导入）✓
- `ruff check bff/ tests/unit/bff/` → 3 条**存量**问题，均不在本次改动文件内：`bff/graph/service.py:388 SIM102`、`bff/search/service.py:9 I001`、`tests/unit/bff/test_search_api.py:14 F401`。其中 F401 修正 §3 conftest 行措辞：**search 侧对 `TS_C1` 只是 import（未使用），实质引用在 timeline:143/:475** —— 结论不变（conftest 仍不动）。按「其他不要动」未修，登记待另批。

### 8.4 V3 路由/OpenAPI + 真启动

- **轻量**（裸 `FastAPI()` + `bff.router.register(app)`）：
  - `paths` = `/api/mylife/overview`、`/timeline`、`/graph`、`/graph/entity/{entity_id}`、`/search`、`/media/{device}/{session}/{kind}/{filename}` —— **无 `/api/mylife/replay`** ✓
  - `components.schemas` 中无 `Replay*` ✓；正向对照：timeline/graph/search 路径仍在 ✓
  - `TestClient(app).get("/api/mylife/replay")` → **404**，body `{"detail":"Not Found"}`（FastAPI 无路由默认体、非 BFF 错误信封，与 §4 Wire 行一致）✓
- **真启动**（复用本仓路径）：`cd services && PATH=.venv/bin:$PATH .venv/bin/python -m pytest tests/integration/bff/test_bff_smoke.py -q -m e2e` → **5 passed / 2 failed**。
  - 5 passed 含真实 `uvicorn app.main:app` 启动成功（`/opt/lvdata` 5.9M DB + `services/.env` 齐备）⇒ **删除后 app 仍可启动** ✓
  - 2 failed = `test_search_pagination_consistent`（q=「的」`limit=3` 时 `next_cursor` 为 None）与 `test_search_kinds_observed_within_contract`（实测 kind 出现 `video`/`agent_job`，超出测试内硬编码的 `SEARCH_KINDS` 五类）。**在 HEAD（未删 replay）的临时 worktree 上复现同样 2 失败** ⇒ 属 `/opt/lvdata` 数据/契约漂移的存量测试债，与本次删除无关；按「其他不要动」未修，建议另立小任务（临时 worktree 已清理）。

### 8.5 V4（未执行）

线上流量归因需生产 access log / `replay_resolve step=`，本机不可达 ⇒ **留待上线前按 §6 V4 规则执行**（路径式 = Web dist 死代码、`?ts_ms=`/`?event_ref=` = 旧版 App 两类预期形态放行；无法归因才回停）。

### 8.6 结论

删除面与 §2 一致、零逻辑新增；V1/V2/V3/V5 全部通过（V3 的 2 项失败已证为存量）；V4 待上线前执行。**未提交**，工作区留待 review。

### 8.7 自审（QA 闸，2026-09-22）

对实施结果再审「删除面是否真穷尽 / 文档与实现是否一致」。**未发现严重问题，未改代码**；证据：

| 审计项 | 结论 |
|---|---|
| 「replay 专属件」是否真穷尽（防残留死码） | `list_sessions`（被删的 `resolve_session_at` 曾调用它）**仍被 timeline:56/220、search:110、graph:95/343 使用**；`get_session` 仍被 timeline:246 + graph:351/386 使用；conftest 的 `make_client` 仍被 `test_media.py:77` 使用；`EVENT_REF_A_CHAT` 是被删测试文件的局部常量 ⇒ **无新增死码** |
| 注释与实现一致性 | `store.py:4/:108/:111` 的「媒体路径解析、时间范围定位」**是上游 `docs/data/lvdata_mcp_service_v2.md:707` 的原句引用**（非对 `resolve_session_at` 的描述）⇒ 不是陈旧注释，**有意不改**（改即偏离上游契约措辞） |
| `_STATUS_BY_CODE` 剩余键 | 删除**未新增**死配置（`bad_request`/`not_found` 抛出点遍布各处）；但 `range_invalid` 是**存量死键**——全仓（除 docs）搜 `range_invalid` 仅 map 自身一处命中，`media.py:45` 走 `FileResponse`（Range/416 由 Starlette 内部处理、不经 `BffError`），`git log -S range_invalid` 显示该串只在引入 map 的 87ad252 出现过 ⇒ **自始无抛出点**。按「其他不要动」不改，登记待另批（**2026-09-22 第二轮订正**：原写「media 的 Range → range_invalid ⇒ 无死配置」不成立） |
| 类型检查 | `mypy bff/` = **11 条 / 4 文件**：timeline 6、graph 3、`store.py:239` 1（`count_events` 的 no-any-return）、`deps.py:28` 1——**store.py 并非 0 错误**。HEAD 快照（临时 worktree，已清理）为 **29 条 / 5 文件**（`replay/service.py` 16、`store.py` 3、timeline 6、graph 3、deps 1）⇒ 本次删除使 mypy 错误 **29 → 11**（store.py 3 → 1，其中 :316/:347 两条正落在被删的 `resolve_session_at` 段内），属**正向副作用**（**2026-09-22 第二轮订正**：原写「11 条全在 timeline/graph、store.py 0 错误」不成立） |
| 格式化 | `ruff format --check` 报 `store.py` would reformat —— **HEAD 同区域同样报**（本仓未跑格式化，存量风格），本次改动未新增偏差（被删段落原有的 4 处 hunk 一并消失） |
| 2 项 smoke 失败的定性 | 已探真实行为：`q=的`（纯停用词）`total=0` ⇒ `next_cursor=None` 属**正确行为**（断言前提不成立）；`SEARCH_KINDS` 未含新增 kind `video`/`agent_job` ⇒ **测试债，非产品缺陷**（HEAD 复现，与删除无因果） |
| 端到端 | 真 app（uvicorn + 真实数据）启动与 overview/timeline/search 请求通过（§8.4）；OpenAPI 路径/schema 与 404 行为符合 §4（§8.4） |

### 8.8 独立复核（第二轮，2026-09-22）

对 §8 全量独立复核：**改动面与 §2 逐条一致、无越界**（graph/timeline/search/media、Android/iOS/Web、docs 均未动）；逐项复跑结果与 §8 记录一致；**未改代码**，仅订正 §8.7 两行并新增本节。

| 复核项 | 独立结果 |
|---|---|
| 改动面 `git diff --stat` | 8 文件 **+1 / −481**（与 §8.1 一致） |
| `tests/unit`（junitxml 计数） | **1089 passed / 0 failed / 0 error / 0 skipped** |
| `tests/unit/bff` | **131 passed**（= 删前 148 − 被删 17） |
| V2 残留 grep + ruff（改动 3 文件） | 0 命中 / All checks passed |
| V3 轻量 | 6 条路径（无 replay）、无 `Replay*` schema、`GET /api/mylife/replay` → 404 |
| V3 真启动 smoke | **5 passed / 2 failed**，失败项名与 §8.4 完全一致（search 数据/契约漂移，与删除面零代码重叠） |
| §5.1 docs 命中面 | 逐文件重跑一致（14 文件 / 367 行） |
| §4 / §5.1 引文逐行核实 | `android_timeline_hotslot.md:165`（兼容矩阵行已反转为假）、`session_json_refactor.md:60/138`、`video_analysis.md:124-125/180` 均属实 |
| §8.7 各行抽查 | ①「replay 专属件」、②「注释与实现一致性」（`lvdata_mcp_service_v2.md:707` 上游原句属实）、⑥ smoke 定性、⑦ 端到端 = 复核属实；③④ 已订正；⑤（格式化，纯风格）未独立复跑 |
| mypy HEAD 对照 | HEAD 29 条 / 5 文件 → 现 11 条 / 4 文件（详见 §8.7 类型检查行） |

**复核结论**：代码层无需任何改动；文档与实现一致（除已订正的 §8.7 两行）；未提交状态与 §8.6 一致，V4 仍为上线前唯一未闭环项。
