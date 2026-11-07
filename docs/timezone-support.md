# Local Timezone 支持设计（定稿）

> 状态：设计定稿（待实施）
> 溯源：本文取代 `docs/timezone-support-k3.md` 与 `docs/timezone-support-d4.md`
> （评审结论：以 k3 为基线，合并 d4 两项优点，修复两案共同的固定偏移盲区；
> 关键差异见 §16）。
> 修订：rev2 新增「活跃时区集」上限——lvdata-mcp 仅生成最近使用时区 +
> 配置默认时区（至多 2 个）的 day_summary，多时区场景 LLM 调用量有界（§8.2/§8.3）。
> 关联文档：`docs/data/lvdata_mcp_service_v2.md`（§5.4/§5.5/§5.6/§6.5/§8.1）、
> `docs/ws-protocol-design.md`（§2.2.1）、`docs/mylife/mylife-design.md`

## 1. 背景与目标

### 1.1 问题

mylife 的日卡（day summary）按「时区 + 本地日」生成与查询，但当前链路中
**客户端所在的 local timezone 没有上传到服务端**：

- `timezones` 表（day_summary 的生成维度，v2 §5.5）只能由 lvdata-mcp 启动时按
  配置文件 seed（`watcher.timezones` + `watcher.timezone` 兜底），运维改配置才能新增时区；
- app 查询 mylife overview 时不传 timezone，BFF 恒用配置默认时区
  （`bff.timezone`，默认 `Asia/Shanghai`）分日/查 day_summary；
- 配置默认时区与 app 实际时区不一致时，日分组和日总结都是错的。

### 1.2 目标（对应需求 1–4）

| # | 目标 |
|---|------|
| G1 | app 在 `session.start` 的 `config` 中新增 `timezone` 字段（系统 API 获取 local timezone，原样上报，服务端负责归一化） |
| G2 | livachat server 收到 `session.start` 时校验并**归一化** timezone（无效则忽略 + info log），将**规范形** upsert 到 lvdata SQLite 的 `timezones` 表（新行插入 / 已存在则刷新 `updated_at`）；`updated_at` 即「最近使用」优先级信号 |
| G3 | app mylife 模块请求 overview（timeline day summary）时传当前系统时区；BFF 归一化后按该时区查询；若该时区不在 `timezones` 表中则回退到配置文件默认时区 |
| G4 | lvdata-mcp 的 day_summary 生成范围收敛为**活跃时区集**：最近使用的时区 + 配置默认时区（`watcher.timezone`），**至多 2 个**；活跃集内最近日优先生成；新加入的时区（必为最近使用）触发其历史 day_summary 生成；非活跃时区停止新生成（历史行保留、再激活续跑）——多时区场景 LLM 调用量有界 |

非目标（本期不做）：

- `apps/web/mylife`（已废止，见 §9）、`apps/android/LivaChatAppMock`（mockup app）、
  `apps/ios`（空目录）均不修改；
- 时区自动淘汰（长期未使用的时区 disable）——列为后续可选（§11.4）；
- `period_summary`（周/月/年）逻辑不变。

### 1.3 现状事实（codebase 核查，非猜测）

**协议与 DTO**：

- `packages/api-schema/c2s/session-start.json`：`config` 必填
  `enable_video/audio_sample_rate/vad_sensitivity/client_version`，
  `additionalProperties: true`（前向兼容，未知字段可透传）。
- Python DTO `ClientSessionStartConfig`（`packages/sdk-python/.../protocol_models.py:204`）
  `extra="allow"`；Kotlin DTO 同由生成器产出（`packages/sdk-kotlin/.../ProtocolModels.kt:234`）。
  双端 DTO 均由 `packages/api-schema/` 生成，**禁止手改**。
- 协议文档：`docs/ws-protocol-design.md` §2.2.1（config 字段表，行 150–156）。

**服务端 session.start 处理**：

- 入口有两处：`services/session/orchestrator.py::_handle_session_start`（行 185）与
  `services/session/omni_orchestrator.py::_handle_session_start`（行 140），
  都经 `SessionManager.start()`（`services/session/manager.py:21`）汇合——
  **单一汇聚点**（已核实 `start()` 恰有这 2 个 caller），timezone 处理放这里。
- `SessionContext`（`services/session/context.py:24-28`）已携带 config 字段
  （`client_version` 等），可平铺增加 `timezone`。
- resume 路径：Android SDK `startSession(resumeSessionId)` **每次现取
  `configProvider()` 重发完整 config**（含 resume 场景，已核实
  `LivaChatSDK.kt:128` 与 `SessionManager.kt:115-128`）——因此 timezone 每次都
  以客户端 fresh 值为准，**无需 Redis snapshot / PG 持久化**（被否决项见 §12.5）。

**Android 时区 ID 的两种形态（设计约束）**：

- `TimeZone.getDefault().id` 通常返回 IANA 名（`Asia/Shanghai`），但在部分设备/
  OEM/手动选偏移场景返回 **`GMT+08:00` 形式**。两条上报路径都原样上报，由
  服务端统一归一化（§5.1），app 不做映射。
- lvdata-mcp `resolve_timezone`（`tools/lvdata-mcp/src/lvdata_mcp/timeutil.py:31`）
  接受 IANA 名、`UTC`/`GMT`、`+08:00`、`8` 等形式，但**不接受 `GMT+08:00`**
  （GMT 前缀形式解析失败后**静默回落 UTC**，已逐行核实 timeutil.py:33-54）——
  这是必须做归一化而非简单放宽校验的直接原因（被否决项见 §12.5）。

**timezones 表与 day_summary（lvdata-mcp）**：

- 表已存在（`tools/lvdata-mcp/src/lvdata_mcp/ddl.py:80-88`）：
  `timezone PK / enabled / priority / created_at / updated_at`，
  `created_at`/`updated_at` 的列 DEFAULT 均为
  `strftime('%Y-%m-%dT%H:%M:%fZ','now')`；`updated_at` 列**已存在**，无需 DDL 变更。
- v2 设计明确「写入方下一阶段」（`lvdata_mcp_service_v2.md` §5.5、§8.1，
  倾向 HTTP API 但「决策推迟」）——本设计即该阶段决策（见 §6、§12）。
- 发现机制**已天然支持新时区**：`DaySummary.discover()`
  （`tools/lvdata-mcp/src/lvdata_mcp/summary/day.py:87`）每轮 poll 遍历
  `timezones WHERE enabled = 1`；新时区在 `day_scan_state` 无行 → 游标按 `''`
  处理 → 全历史事件首扫、全部历史日进入 dirty（§5.6），按
  `day_summary_max_per_poll`（默认 10）分批消化，pending 占位行保证下轮续跑。
- 现状排序问题：`discover()` 查 timezones **无 ORDER BY**（day.py:94-95），
  时区内 dirty 日按**日期升序**（day.py:150 `for d in sorted(dirty)`）——
  与 G4 的优先级要求相反，需要改。
- `Scanner._write_day_force`（`scanner.py:386`，调用点 :197）已对全部 enabled
  时区写收尾日 force 行，新时区自动被覆盖，无需改。

**BFF**：

- `GET /api/mylife/overview` 与 `/timeline` 已接受 `timezone` query 参数
  （`services/bff/timeline/router.py`），`search`/`graph` 同样；
  校验 `validate_tz`（`services/bff/common/deps.py:48-56`）：仅接受 IANA 名
  （`ZoneInfo` 严格校验），非法 → 400；缺省 → `bff.timezone`
  （`services/shared/config/models.py:228`，默认 `Asia/Shanghai`，已核实）。
- 日界换算 `bff/common/timeutil.py::_tz`（行 11-12）同样走
  `ZoneInfo(validate_tz(...))`——引入固定偏移规范形后必须同步改造（§7.3）。
- day_summary 查询已按生效时区过滤（`LvdataStore.day_summaries(tz, ...)`，
  `services/bff/common/store.py:314`；`build_overview` 的 `_day_summary_text`/
  `_day_topics`）。**G3 只缺「不在 timezones 表 → 回退默认」这一规则**。
- `LvdataStore` 为只读 facade（`store.py:78` `mode=ro` + busy_timeout），
  新增只读查询不破坏纪律。

**livachat server 直写 lvdata SQLite 的先例**：

- Agent Relay 的 `JobLvdataWriter`（`services/agent_relay/job_persistence.py`）
  已从本进程直写同一 lvdata 库（v2 §6.2.0 契约：WAL + busy_timeout ≥ 5000ms、
  只写约定表；:103-111 注释明确**不重复设置 `journal_mode=WAL`**——WAL 是库级
  持久属性，重复设置会与 lvdata-mcp 争短暂独占锁，存在 `database is locked`
  抖动教训）。G2 沿用同一纪律，**写入方纪律有先例、非新模式**。
- DB 路径解析规则（与 `LvdataStore.from_config` 一致）：
  `bff.lvdata_db_path` 或 `<bff.data_root 或 recording.base_dir>/lvdata_inventory.db`。

**Android app**：

- `MylifeApi.overview(timezone: String? = null)` 参数已存在
  （`apps/android/LivaChatApp/.../mylife/MylifeApi.kt:42`），但
  `MylifeRepository.refreshOverview` 调用时**未传**（`MylifeRepository.kt:44`，
  已核实）。
- SDK 在 `configProvider`（`apps/android/LivaChatSDK/.../LivaChatSDK.kt:128`）
  构造 `ClientSessionStartConfig`——**每次 session.start 现取**，时区放这里可
  天然感知系统时区变化（旅行场景）。
- `LivaChatConfig`（`apps/android/LivaChatSDK/.../LivaChatConfig.kt`）为不可变
  data class，新增可空字段即可。

**关联点**：

- recording 的 `session.json` 目前硬编码 `"timezone": "UTC"`
  （`services/recording/session.py:364`，已核实）；scanner 会把它落入
  `recording_sessions.timezone`（`scanner.py:278`）。v2 §5.5 注明该列语义本就是
  「录制时客户端上报的时区」——G1 落地后可顺手修正（列为可选关联修改，§10）。
  **2026-09-15 更新**：G1 已实施、上报链路保留，且 session_json_refactor 后
  session.json 静态化——`timezone` 不再落盘（仅存 RecordingSession 内存态与
  日志）；lvdata `recording_sessions.timezone` 因此恒为默认 `'UTC'`（全仓库
  无读取方，零功能损失）。
- Mock Server（`tools/local-dev/mock-services/livachat_mock_server`）消费同一
  生成 DTO（`extra="allow"`），新字段无需改动。
- e2e client（`services/tests/e2e-backend/client.py:70`）可选加 `timezone` 参数。

## 2. 总体方案

```
┌─ Android app ─────────────────────────────────────────────────┐
│ TimeZone.getDefault().id（原样上报，IANA 或 GMT±HH:MM 形态）    │
│   ├─ session.start.config.timezone ──┐                        │
│   └─ GET /api/mylife/overview?timezone=… ──────────────┐      │
└──────────────────────────────────────┼─────────────────│──────┘
                                       ▼                 ▼
┌─ livachat server（单进程：gateway/session + bff 模块）────────┐
│ shared: canonicalize_timezone()  归一化（IANA 原样 /          │
│         GMT+08:00→+08:00 / utc,gmt→UTC / 非法→None）          │
│ session 模块: 归一化 → 无效忽略+info log                       │
│              → 有效: upsert timezones(规范形, updated_at=now)  │
│              fire-and-forget，不阻塞 session.ready       (G2)  │
│ bff 模块:    resolve_request_tz()                       (G3)  │
│              非法 → 400；合法但不在 timezones 表 → 回退默认    │
└──────────────────────────────┬────────────────────────────────┘
                               │ 同一 lvdata_inventory.db（WAL）
                               ▼
┌─ lvdata-mcp（常驻）───────────────────────────────────────────┐
│ poll: DaySummary.discover()                                   │
│   活跃集 = 最近使用时区 + 配置默认时区（enabled，至多 2 个）  │
│   活跃集内 dirty 日 按日期 DESC          （最近日优先）       │
│   新时区（必为最近使用）→ 全历史首扫（既有机制）        (G4)  │
│   非活跃时区不生成；行保留，再激活续跑                        │
│ work: DaySummary.generate() 按入队顺序生成                     │
└───────────────────────────────────────────────────────────────┘
```

设计要点：

- **写入方**：livachat server（session 模块）直写 SQLite `timezones` 表，
  且**只写该表**——对齐 §6.2.0 agent_jobs 直写契约（WAL + busy_timeout ≥ 5000ms）。
  备选 HTTP API 方案见 §12.1。
- **归一化单一入口**：`services/shared/timeutil.py` 新增
  `canonicalize_timezone()`，session 模块与 BFF 共用；**只有规范形进入
  `timezones` 表与下游换算**（§5.1 的不变式），lvdata-mcp `resolve_timezone`
  对全部规范形已有正确语义，无需改动。
- **优先级信号与活跃集**：`timezones.updated_at`。session.start 到达即刷新（每次
  新会话/重连都算「最近使用」）。lvdata-mcp 每轮 poll 只为**活跃时区集**生成：
  `updated_at` 最高的 enabled 时区 + 配置默认时区 `watcher.timezone`（若 enabled），
  **至多 2 个**——多时区场景 LLM 调用量有界（§8.2/§8.3）。`priority` 列本期
  不使用（保留给未来手工调优）。
- **不新增表、不新增消息类型**：协议只做可选字段加法，全链路向后兼容。

## 3. 协议变更（G1）：session.start config.timezone

### 3.1 JSON Schema（SSOT）

`packages/api-schema/c2s/session-start.json` 的 `config.properties` 增加
（**optional**，不进 `required`）：

```json
"timezone": {
  "type": "string",
  "description": "Client local timezone ID (e.g. \"Asia/Shanghai\"; on some Android devices \"GMT+08:00\"). Send the raw system value; the server validates and canonicalizes it, and ignores invalid values. Optional."
}
```

### 3.2 代码生成

```bash
make generate-android-api    # Kotlin DTO → ClientSessionStartConfig.timezone: String? = null
# scripts/generate_python_api.py（仓库既有命令）→ Python DTO 同步产出
```

生成产物禁止手改；`make check-android-api-generated` 在 CI 校验一致性。
Kotlin 侧 `explicitNulls=false`，null 字段线上直接省略，旧 server 无感知。

### 3.3 兼容性

- 老 app 不上报 → 服务端按「未提供」处理，行为与现状完全一致；
- 老服务端收到新字段 → `additionalProperties: true` / `extra="allow"` 透传忽略；
- Mock Server、Android e2e harness 无需改动。

### 3.4 协议文档

`docs/ws-protocol-design.md` §2.2.1 字段表增加一行（可选字段），示例 config 增加
`"timezone": "Asia/Shanghai"`，并注明「原样上报系统值，服务端负责归一化」。

## 4. App 变更（G1/G3，仅 LivaChatSDK + LivaChatApp）

| 文件 | 变更 |
|------|------|
| `apps/android/LivaChatSDK/.../LivaChatConfig.kt` | 新增 `val timezone: String? = null`（null = 每次现取系统时区；允许宿主 app 显式覆盖，便于测试） |
| `apps/android/LivaChatSDK/.../LivaChatSDK.kt:128` | `configProvider` 构造 `ClientSessionStartConfig` 时填 `timezone = config.timezone ?: TimeZone.getDefault().id`（每次 session.start 现取，旅行换时区自然生效；**原样上报，不做映射**） |
| `apps/android/LivaChatApp/.../MylifeRepository.kt:44` | `api.overview(timezone = TimeZone.getDefault().id)`（每次刷新现取） |

说明：

- `MylifeApi.overview` 的 `timezone` 参数已存在，仅 repository 出口补上；
- 时区变化（飞行/跨时区）无需额外处理：两条上报路径都是请求时现取系统值；
- `GMT±HH:MM` 形态设备由服务端归一化兜底（§5.1/§7），app 无感知；
- `LivaChatAppMock`、web/mylife、iOS 不动（§9）。

## 5. 服务端 session.start 处理（G2）

### 5.1 归一化（新增，`services/shared/timeutil.py`）

```python
def canonicalize_timezone(tz: str | None) -> str | None:
    """客户端上报时区 → 规范形；非法 → None。

    规范形（lvdata-mcp resolve_timezone 已全覆盖的子集）：
    - IANA 名原样："Asia/Shanghai"（ZoneInfo 校验通过即返回）
    - 固定偏移："+08:00" / "-05:30"
      （容忍可选 GMT 前缀、大小写、小时单位写法："GMT+08:00"/"gmt+8"/"8" → "+08:00"）
    - "UTC"："utc"/"gmt"/"+00:00"/"-00:00" 归一
    固定偏移取值域 |offset| ≤ 14:00（IANA 实际极值），分钟须为 00–59。
    """

def resolve_tzinfo(tz: str):
    """规范形 → tzinfo（ZoneInfo 或固定偏移）；与 lvdata-mcp resolve_timezone 同语义。"""
```

不变式：**timezones 表、SessionContext、BFF 生效时区只持有规范形**——
`resolve_timezone`（lvdata-mcp）与 `resolve_tzinfo`（services）对规范形语义
一致，分日结果双端相同。sign 方向与 Java 一致（`GMT+08:00` = UTC+8 → `+08:00`；
注意与 IANA `Etc/GMT+8` 的 POSIX 反号约定无关，后者是另一个合法 IANA 名，原样
通过 ZoneInfo）。

### 5.2 处理流程

`SessionManager.start()` 增加可选参数 `timezone: str | None`，两个 orchestrator
各加一行透传（`cfg.timezone`，DTO 重新生成后为类型化字段）：

1. **归一化**：`canonicalize_timezone(tz)`；`None`（含空串/非法）→
   `logger.info("invalid timezone in session.start, ignored", ...)`，跳过，
   不影响会话建立。
2. **入库**（规范形有效时）：fire-and-forget，**不阻塞 session.ready**——
   `asyncio.create_task`（任务句柄存入 `self._bg_tasks: set`，done callback
   移除，防 GC 提前回收）包裹 `asyncio.to_thread` 执行单行 upsert：

   ```sql
   INSERT INTO timezones (timezone) VALUES (?)
   ON CONFLICT(timezone) DO UPDATE
   SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
   ```

   - 时间戳用 SQL 侧 `strftime`：INSERT 时 `created_at`/`updated_at` 走列
     DEFAULT（同一 strftime 表达式，ddl.py:80-88 已核实），与 v2 §5.1 规范
     格式及 lvdata-mcp `now_iso()` 输出一致，字符串比较语义正确；
   - 新行 `enabled`/`priority` 走表默认值（enabled=1）。
3. **落上下文**：规范形记入 `SessionContext.timezone`（无效/缺失为空串）——
   供日志与 RecordingSession 内存态使用（2026-09-15 session_json_refactor 后
   不再落 session.json）。

### 5.3 DB 连接纪律（对齐 §6.2.0 契约）

- 路径解析与 `LvdataStore.from_config` 同规则：
  `cfg.bff.lvdata_db_path` → 缺省 `<cfg.bff.data_root or cfg.recording.base_dir>/
  lvdata_inventory.db`。helper 放 `services/shared/`，在 `application.py:111`
  构造 `SessionManager` 时解析注入。
- **DB 文件不存在则跳过 + info log**（gateway 不创建库文件；lvdata-mcp 首跑建库
  后，后续 session.start 自然写入）。避免路径误配时造出只有 timezones 表的裸库。
  明确记录的取舍：**lvdata-mcp 首跑之前的时区上报会丢失**（仅 info log），
  正常部署顺序下影响为零，可接受。
- 每写连接仅 `PRAGMA busy_timeout = 5000`；**不重复设置** `journal_mode=WAL`
  （WAL 对库文件是持久属性，lvdata-mcp 建库时已设置；重复设置会与 lvdata-mcp
  争短暂独占锁，见 `job_persistence.py:103-111` 的教训注释）。
- 任何 DB 异常只 log 不抛出（timezone 上报是旁路增强，绝不阻断会话）。

### 5.4 依赖注意

`zoneinfo` 需要系统 tzdata；若部署镜像是最小化 Linux，需在
`services/pyproject.toml` 增加 `tzdata` 兜底依赖（运维检查项）。

## 6. timezones 表写入契约（对 v2 §5.5/§8.1 的决策落地）

v2 §8.1 把写入方形态（① lvdata-mcp HTTP API / ② 直写库）「决策推迟到 bff 阶段」。
本设计决策为 **② 的变体：livachat server（session 模块）直写**，理由：

1. 触发点天然在 session.start（gateway），不在 BFF 查询路径；
2. 直写已有 §6.2.0 agent_jobs 先例与纪律（WAL + busy_timeout ≥ 5000ms、只写约定表），
   无新模式风险；
3. HTTP API 方案会引入 gateway → lvdata-mcp 的可用性耦合与重试复杂度（§12.1 详述）。

写入纪律：

- 只写 `timezones` 表，单行 upsert；时间戳用 SQL 侧 strftime（§5.2）；
- 只写**规范形**时区（§5.1 不变式）；
- `updated_at` 语义 = 「最近使用水位」，session.start 到达即刷；
- lvdata-mcp 的 `ensure_schema` seed 维持 `INSERT OR IGNORE` 不变
  ——config seed **不刷** `updated_at`，避免配置时区伪装成「最近使用」。

实施时同步修订 `docs/data/lvdata_mcp_service_v2.md` §5.5（「写入方下一阶段」→
引用本文档）与 §8.1（决策已落地）。

## 7. BFF 变更（G3）

### 7.1 时区解析新规则

`services/bff/common/deps.py` 新增（`validate_tz` 由本函数取代于各 service，
保留供内部调用语义参考）：

```python
def resolve_request_tz(store, timezone: str | None, default: str) -> str:
    if timezone is None:
        return default                         # 未传 → 默认时区（现状）
    tz = canonicalize_timezone(timezone)       # 归一化（shared）
    if tz is None:
        raise BffError("bad_request", f"invalid timezone: {timezone!r}")  # 非法 → 400（维持现状口径）
    if tz == default:
        return default                         # 短路：默认时区无需查表
    known = store.timezone_known(tz)           # None = 表缺失/空表
    if known is False:                         # 合法但不在 timezones 表 → 回退默认
        log.debug("timezone %r not in timezones table, fallback to %r", tz, default)
        return default
    return tz
```

`LvdataStore` 新增只读方法（`mode=ro` 不变）：

```python
def timezone_known(self, tz: str) -> bool | None:
    """timezones 表缺失或为空 → None（全新部署，按现状放行）；否则返回是否存在该行。"""
```

边界语义：

- **表缺失/空表 → 放行请求时区**（现状行为）：保证 lvdata-mcp 从未运行过的
  全新部署/开发环境不退化；
- 「在表中」按行存在判定，不看 `enabled`（disabled 时区可能有历史 day_summary，
  且需求原文是「表里没有」）；若后续要对 disabled 也回退，一行 WHERE 可调；
- 响应体 `timezone` 字段返回**生效时区**（现有字段），app 可据此感知回退；
- 「在表中但非活跃」的时区：不回退（行存在即用），但其 day_summary 可能滞后
  （§8.3 非活跃不生成）；app 现实路径不受影响——当前系统时区必为最近一次
  session.start touch，即活跃时区；
- `GMT±HH:MM` 形态设备：归一化为 `+HH:MM` 后与表中规范形对齐，不再 400。

### 7.2 应用范围

`build_overview`（G3 核心：day summary 按生效时区查 `day_summary` 表——已有逻辑，
只换时区来源）与 `build_timeline`（同一分日语义）必改；`search`/`graph` 的
service 同用 `validate_tz`，一并替换为 `resolve_request_tz` 保持一致
（日期过滤同样按本地日界换算，语义应统一）。

### 7.3 日界换算配套改造

`bff/common/timeutil.py::_tz`（行 11-12）现为 `ZoneInfo(validate_tz(...))`，
固定偏移规范形会抛异常——改为 `resolve_tzinfo()`（§5.1 shared helper），
`day_bounds_ms`/`ms_to_local_date`/`ms_to_local` 随之透明支持 `+08:00` 规范形，
与 lvdata-mcp 分日结果一致。

## 8. lvdata-mcp 变更（G4）

### 8.1 新时区检测（既有机制，无需改动）

`DaySummary.discover()` 每轮 poll 遍历活跃时区集（§8.2）；gateway upsert 的新行
`updated_at = now` 必为最近使用 → 下轮即入活跃集被遍历到；`day_scan_state` 无该
时区行 → 游标 `''` → 全历史事件首扫、历史日全部判 dirty、落 pending 占位行分批
消化（§5.6/§6.5，且已有测试覆盖「新时区全量首扫」）。
**「有新 timezones 加入则生成其 day_summary」天然成立**。

### 8.2 活跃时区集 + 生成优先级（本次修改点，`summary/day.py`）

**活跃时区集**：discover() 只遍历以下集合（而非全部 enabled 时区），**至多
2 个**，多时区场景 LLM 调用量有界：

```python
def _active_timezones(conn) -> list[str]:
    """活跃集 = 最近使用的 enabled 时区 + 配置默认时区（若 enabled），有序去重。"""
    # 1) 最近使用：
    #    SELECT timezone FROM timezones WHERE enabled = 1
    #    ORDER BY updated_at DESC, timezone LIMIT 1
    # 2) 配置默认：cfg.watcher.timezone，且表内 enabled = 1
    # 返回 [最近使用, 配置默认]；二者相同（或默认缺失/disabled）则只 1 个
```

| 位置 | 现状 | 改为 |
|------|------|------|
| `discover()` 时区遍历（day.py:94-95） | `SELECT timezone FROM timezones WHERE enabled = 1`（无序，全量） | `_active_timezones()`：最近使用（`ORDER BY updated_at DESC, timezone LIMIT 1`）∪ 配置默认，有序去重 |
| `discover()` 时区内 dirty 日入队（day.py:150） | `for d in sorted(dirty)`（日期升序，最老优先） | `sorted(dirty, reverse=True)`（最近日优先） |

效果：`self._pending` 的入队顺序 = 全局优先级序（最近使用时区 → 配置默认时区
→ 各自最近日），单轮上限 `day_summary_max_per_poll` 先消化高优先级；溢出日已有
pending 占位行，后续轮次经 `_check_pending_retry` 兜底续跑。

### 8.3 活跃集边界语义

- **新时区即时激活**：session.start upsert 的新行 `updated_at = now`，必为
  最近使用 → 下轮 poll 即入活跃集 → `day_scan_state` 无行 → 全历史首扫
  （§8.1 既有机制）不受影响。
- **非活跃时区**：不被 discover 遍历——游标（`day_scan_state`）停滞、
  pending/failed/deferred/force 行**原样保留**（不消费、不清理）、不产新
  day_summary；历史 day_summary 行保留，BFF/MCP 查询不受影响。
- **再激活续跑**：再次成为最近使用（如返程后下一次 session.start touch）→
  游标从停滞处继续，停滞期间事件成为候选 → 积压日按
  `day_summary_max_per_poll` 分批补齐（最近日优先），完整性不丢、仅延迟。
- **`enabled = 0` 仍是总开关**：活跃集两个槽位都在 enabled 集合内选取，
  手工关停对默认时区同样生效。
- **配置默认时区缺位**：表中无该时区（或被 disable）→ 活跃集仅「最近使用」
  1 个。
- **`Scanner._write_day_force`（scanner.py:386）维持「全部 enabled 时区」
  不变**：单行 INSERT 极廉价，非活跃时区的 force 行惰性留存、再激活时按
  「收尾日恒 dirty」生效；活跃集过滤保持只在 discover 一处（单一过滤点，
  避免 Scanner 耦合）。
- **不新增配置项**：活跃集规则固定为「1 最近使用 + 1 配置默认」；若未来需
  调宽（如活跃集取 N 个最近使用），仅改 `_active_timezones()` 一处。

不变项：

- `generate()` 消费 `_pending` 的顺序语义（有界并发 semaphore）；
- force 模式 `_all_dirty()`（day.py:325，人工触发全量重跑）——**不受活跃集
  限制**（人工显式触发的全量重跑，语义本就如此）；
- MCP tool `get_day_summary`（tools.py:229）缺省 `watcher.timezone` 不变
  （只读查询，活跃集只约束 poll 生成路径）；
- `resolve_timezone` 不变（规范形为其既有语义子集，§5.1 不变式保证）。

## 9. apps/web/mylife 废止（需求 6）

- `apps/web/mylife`（React SPA）已废止：mylife 前端唯一消费方为 Android App。
- 本期更新 `REPO.md`：MyLife 前端命令（`make mylife-install/dev/check/build`）
  标注废止，BFF `/api/mylife` 保留（Android 消费）。
- 关联但未动（后续可自行处理）：`docs/mono-repo.md` 行 184 的目录说明、
  `bff.web_dist` 配置与 `_mount_web_dist`（2026-10-13 已随 /mylife 托管摘除整体删除）、
  Makefile `mylife-*` target。

## 10. 可选关联修改（需求 5 核查发现，建议但不阻塞）

| 项 | 说明 |
|----|------|
| recording `session.json` 的 `timezone`（`services/recording/session.py`） | 本项已实施（timezone 经 `sink.on_session_config` 透传进 RecordingSession 内存态），随后被 2026-09-15 **session_json_refactor 取代**：session.json 静态化、`timezone` 字段整体移除不再落盘；lvdata 列恒默认 `'UTC'`（无读取方） |
| e2e client（`services/tests/e2e-backend/client.py:70`） | `session_start()` 可选增加 `timezone` 参数，供集成测试覆盖 G2 |
| `docs/mono-repo.md` 行 184 | apps/web/mylife 表述随 README 一并废止化（可并入 README 同次提交） |

## 11. 风险与边界

1. **多进程写同一 SQLite**：livachat server 与 lvdata-mcp 双写方。单行 upsert
   毫秒级；WAL（lvdata-mcp 建库时已持久设置）+ busy_timeout 5000ms；异常只 log。
   与 §6.2.0 agent_jobs 同一纪律，风险已有先例背书。
2. **合法但无意义的时区**（如客户端被篡改/bug 上报 `Etc/GMT+13`）：归一化
   挡不住「合法但错」的值，但活跃集上限使其代价有界——一次 touch 仅抢占
   「最近使用」槽位，配置默认时区槽位不受影响，真实客户端下一次
   session.start 即自愈；垃圾时区的历史首扫成本为一次性。根治：手工
   `enabled=0` 关停；§11.4 的自动淘汰可后续上。
3. **时区表膨胀**：典型单设备 1–2 个时区，行数极小；无清理需求。
4. **自动淘汰（后续可选）**：`updated_at` 老化（如 > 30 天）的时区可由
   lvdata-mcp 自动 `enabled=0`，停止为其生成新 day_summary（历史行保留）。
   本期不做。
5. **旅行换时区**：app 两条上报路径都现取系统值；新时区首次 session.start
   即入活跃集，旧时区随即转为非活跃、停止新生成——**这正是活跃集上限的
   省费意图**（§8.2）。返程后旧时区再激活，停滞期积压日分批补齐（§8.3）。
6. **首扫顺序变化**：时区内 dirty 日改降序后，新时区/回填场景最近日先出
   day_summary（正是 G4 要求）；最老日经 pending 占位行在后续轮次补齐，
   完整性不受影响。
7. **tzdata 依赖**：`zoneinfo` 在极简镜像需 `tzdata` 包兜底（服务端与
   lvdata-mcp 同要求；lvdata-mcp 已依赖 `ZoneInfo`，部署上应已满足）。
8. **重连风暴**：每次 session.start（含重连/resume）一次 upsert，单行毫秒级，
   无需节流；写入失败仅 info log。
9. **规范形一致性**：`canonicalize_timezone` 是写入 timezones 表的唯一入口；
   lvdata-mcp config seed 只可能写 IANA 名（运维配置），同为规范形。若未来新增
   写入方，必须复用同一归一化函数——违者导致「表内值 resolve_timezone 静默
   回落 UTC」类错误（§1.3）。
10. **后台任务生命周期**：fire-and-forget 的 `create_task` 句柄必须入
    `self._bg_tasks` 集合（done callback 移除），否则事件循环可能提前 GC 任务。

## 12. 备选方案与被否决项（需求 8，供 review）

### 12.1 lvdata-mcp 暴露 `POST /api/timezones`（v2 §8.1 原倾向①）

- 优点：保持 lvdata-mcp 单一写入方；可鉴权、可校验、可加自动淘汰逻辑于一点。
- 缺点：gateway session.start 路径新增对外 HTTP 依赖——lvdata-mcp 不可用时要
  降级（重试/丢弃），复杂度高于直写；且直写纪律已有 §6.2.0 先例。
- 结论：**不采用为本期方案**；若运维上坚持单一写入方，可平滑切换
  （session 模块的 `report_timezone` 换成 HTTP client，表语义不变）。

### 12.2 scanner 从 `recording_sessions.timezone` 派生时区集合

- 优点：零新写入路径。
- 缺点：依赖 recording 开启且 session.json timezone 真实（2026-09-15 静态化后
  该字段已移除，此方案失去数据源）；无 `updated_at` 最近使用信号（得另查
  MAX(scanned_at)，语义别扭）；历史一次性时区堆积进表。
- 结论：不推荐作主方案（session.json 静态化后已不可行）。

### 12.3 BFF 请求时 lazy insert（查询驱动写入）

- 优点：时区集合自动贴合查询。
- 缺点：破坏 BFF 只读原则（`mode=ro`）；且「app 时区不在表 → 回退默认」（G3）
  与「查到即插入」自相矛盾；任意客户端可刷表。
- 结论：否决。session.start 是唯一注册通道。

### 12.4 优先级用 `priority` 列而非 `updated_at`

- 需求明确以 `updated_at` 为优先级依据，且免维护；`priority` 列保留给未来
  手工调优（如把某时区钉到最高），不冲突。
- 结论：否决。

### 12.5 d4 稿特有、本定稿显式否决的三项

- **Redis snapshot + PG `sessions.timezone` 列（alembic 迁移）持久化**：
  已核实 Android SDK 每次 session.start（含 resume）都现取 configProvider 重发
  完整 config，fresh 值必然到达；且本期无下游消费者（recording 修正仅需
  内存态 SessionContext）。属 YAGNI；且若仿照 `manager.py:45` 的
  snapshot-覆盖-config 模式，旅行后 30s TTL 内 resume 会出现旧时区覆盖新上报的
  反向陷阱。结论：否决，仅落 `SessionContext`（内存）。
- **server 侧 `CREATE TABLE IF NOT EXISTS` 自建 schema**：在 server 复制一份
  ddl.py 的 DDL，有漂移隐患；路径误配时会静默建出裸库。结论：否决，采用
  「DB 文件不存在则跳过 + info log」（§5.3），取舍已显式记录。
- **不做归一化的「放宽校验」**（直接接受 `GMT+08:00` 入表）：lvdata-mcp
  `resolve_timezone` 对该形式**静默回落 UTC**（timeutil.py:33-54 逐行核实），
  会产生「按 UTC 分日但外观正常」的静默错误数据。结论：否决，必须经
  `canonicalize_timezone` 归一化（§5.1）。

## 13. 测试计划

| 层 | 用例 |
|----|------|
| shared 单测（新增） | `canonicalize_timezone`：IANA 原样；`GMT+08:00`→`+08:00`；`gmt+8`→`+08:00`；`8`→`+08:00`；`utc/gmt/+00:00`→`UTC`；垃圾串/超 ±14:00/分钟 ≥60 → `None`；`resolve_tzinfo` 与 lvdata-mcp `resolve_timezone` 对规范形结果一致 |
| services 单测（新增） | timezone 上报 helper：合法 IANA → insert；同 tz 再次上报 → `updated_at` 刷新；非法 tz → 忽略 + info log；DB 文件不存在 → 跳过不建库；后台任务句柄入 `_bg_tasks` |
| services 集成 | `session.start` 带 timezone → timezones 表出现该行（规范形）；带非法 timezone → 会话正常建立（`session.ready` 不受影响） |
| BFF 单测（`services/tests/unit/bff/test_timeline_api.py`，conftest 增 timezones 夹具） | 请求 tz 在表 → 用之；不在表 → 回退默认且响应 `timezone` 为默认值；表缺失/空表 → 放行请求 tz；非法 tz → 400；`GMT+08:00` 请求 → 归一化为 `+08:00` 后按表成员判定；`tz == default` 短路不查表 |
| lvdata-mcp 单测（`tools/lvdata-mcp/tests/test_day_summary.py`） | 活跃集 = 最近使用 + 配置默认（二者相同 → 1 个；默认缺失/disabled → 1 个；最近使用 disabled → 取次近 enabled）；非活跃时区不被遍历且其 pending/force/游标行保留；再激活后积压分批续跑；时区内入队序 = 日期 DESC；新时区首扫既有用例保持绿 |
| Android | `MylifeRepositoryTest`：overview 调用携带系统时区；SDK 单测：`configProvider` 生成 config 含 timezone（默认系统值 / 显式覆盖） |
| 回归 | `make server-test`、lvdata-mcp pytest、`make check-android-api-generated` |
| 端到端（手验） | 起 lvdata-mcp + server → Android 开会话 → `sqlite3` 查 `timezones` 行新增且 `updated_at` 刷新 → `/api/mylife/overview?timezone=…` 分日正确、表外时区回落 |

## 14. 文件级变更清单

**协议与生成物**

1. `packages/api-schema/c2s/session-start.json` — config 增加 optional `timezone`
2. 生成物（脚本产出，勿手改）：`packages/sdk-kotlin/.../ProtocolModels.kt`、
   `packages/sdk-python/.../protocol_models.py`
3. `docs/ws-protocol-design.md` — §2.2.1 字段表 + 示例 + 「原样上报」注记

**Android（不含 LivaChatAppMock）**

4. `apps/android/LivaChatSDK/.../LivaChatConfig.kt` — 新增 `timezone: String? = null`
5. `apps/android/LivaChatSDK/.../LivaChatSDK.kt` — configProvider 填 timezone（原样）
6. `apps/android/LivaChatApp/.../MylifeRepository.kt` — overview 传系统时区
   （+ 对应测试更新）

**livachat server**

7. `services/shared/timeutil.py`（新增）— `canonicalize_timezone()` /
   `resolve_tzinfo()`；DB 路径解析 helper 同放 `services/shared/`
8. `services/session/manager.py` — `start(timezone=…)`；归一化 + fire-and-forget
   upsert（`_bg_tasks` 集合）；`SessionContext.timezone`
   （`services/session/context.py`）
9. `services/session/orchestrator.py`、`services/session/omni_orchestrator.py`
   — 各一行透传 `cfg.timezone`
10. `services/app/application.py` — 构造 `SessionManager`（:111）时注入解析后的
    lvdata db 路径
11. `services/bff/common/deps.py` — `resolve_request_tz()`；
    `services/bff/common/store.py` — `timezone_known()`
12. `services/bff/common/timeutil.py` — `_tz` 改用 `resolve_tzinfo()`
13. `services/bff/timeline/service.py`（overview/timeline）、
    `services/bff/search/service.py`、`services/bff/graph/service.py`
    — `validate_tz` → `resolve_request_tz`
14. ~~（可选）`services/recording/session.py` — session.json timezone 用
    `ctx.timezone or "UTC"`~~（已实施后废弃：2026-09-15 session_json_refactor
    移除 session.json 的 timezone 字段）

**lvdata-mcp**

15. `tools/lvdata-mcp/src/lvdata_mcp/summary/day.py` — 新增 `_active_timezones()`
    （最近使用 `ORDER BY updated_at DESC, timezone LIMIT 1` ∪ 配置默认）；
    discover 改遍历活跃集；dirty 日 `sorted(dirty, reverse=True)`
16. `tools/lvdata-mcp/tests/test_day_summary.py` — 排序用例

**文档**

17. `REPO.md` — apps/web/mylife 废止标注（本期）
18. `docs/data/lvdata_mcp_service_v2.md` — §5.5/§8.1 写入方决策落地（引用本文档；
    实施时同步）

## 15. 实施顺序建议

1. 协议 schema + 双端 codegen（§3）→ 2. shared 归一化 + 服务端 G2（§5）
   → 3. lvdata-mcp G4（§8）→ 4. BFF G3（§7）→ 5. Android G1/G3（§4）
   → 6. 文档与可选关联项（§9/§10）。
   2–4 互相独立可并行；4 依赖 2 的 shared helper；5 依赖 1 的 Kotlin DTO 重新生成。

## 16. 相对评审稿的关键差异（溯源）

| 项 | k3 稿 | d4 稿 | 本定稿 |
|----|-------|-------|--------|
| 固定偏移设备（`GMT+08:00`） | 未覆盖（会 400） | 放宽接受（但 `resolve_timezone` 静默回落 UTC，数据错误） | **入口归一化**（§5.1），双端闭环 |
| 写入落点 | `SessionManager.start` 单汇聚点 | 两个 orchestrator 镜像 | k3 方案 |
| 写入阻塞语义 | 真 fire-and-forget | 自称 fire-and-forget 实为 await | k3 方案 + `_bg_tasks` 防 GC |
| DB 不存在 | 跳过不建库 | 自建表（DDL 漂移隐患） | k3 方案，取舍显式记录（§5.3） |
| Redis/PG 持久化 | 不做 | 做（无消费者，YAGNI + snapshot 覆盖陷阱） | 不做（§12.5） |
| discover 排序 | `ORDER BY updated_at DESC` | `ORDER BY updated_at DESC, timezone` | **d4 方案**（tie-break 确定性） |
| 默认时区短路 | 无 | `tz != default_tz` 短路 | **d4 方案**（§7.1） |
| upsert 时间戳 | Python 侧 `now_iso()` 格式 | SQL 侧 `strftime` | **d4 方案**（与列 DEFAULT 同源，更简；两者输出格式一致） |

---

## 17. Agent 侧（relay → Hermes prompts）时区注入——另立文档

本链（客户端上报 → 服务端归一/落库 → BFF/lvdata 读侧）之外还有**最后一跳**：把设备时区
交给 **Hermes agent**（它调 lvdata MCP 时需要 `timezone` 入参，也要据此解释"今天/昨天"）。

- 设计与决策：**[docs/agent_relay_prompts.md](./agent_relay_prompts.md)**（2026-09-28 已拍板
  **并已实施**，见其 §7 实施记录）——`session.started` 事件加 `timezone`（发布处透传本链已 canonical 的
  `ctx.timezone`）→ relay `AgentContext` → mission prompt 的 `# 本设备时区` 小节 +
  `job_task.md` 行 + nudge 载荷字段；
- 与本链的关系：**不改**本文 §5/§6/§7/§8 的任何口径（上报、归一化、落库、读侧解析均复用）；
  relay 侧不做二次校验（信任本文 §5.1 的 canonical 值，MCP 接受 IANA/`UTC`/`+HH:MM`）。
