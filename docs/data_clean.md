# 数据清理设计：video 帧保留（`watcher._prune_loop` 扩展）

> 状态：**已实施（2026-10-07：D1–D5 拍板 + S1–S4 完成，见 §9 收尾记录）**。
> **提案来源（用户，2026-10-07）**："watcher._prune_loop 加入 video prune：
> ① 每天执行一次，清理 72 小时之前的 video files；② 如果没有 video event，
> 就删除 video file；③ 72 小时可通过配置文件配置、不能低于 24 小时（低于就
> 按 24）；④ 以 codebase 为基础，不要猜测"。
> 本文所有关键断言均标注 file:line；体量数字为 2026-10-07 真实库实测。

## 1. 现状盘点（2026-10-07 核实）

**已有定时清理**：

| 任务 | 节拍 | 位置 |
|---|---|---|
| 审计表保留（mcp_pipeline_runs 14d / analysis_runs 30d / index_state 孤儿水位） | `audit_prune_interval_s`=86400s，启动即跑一次 | `watcher.py:291-338`（`_prune_loop`/`_prune_once`；video prune 见 `_video_prune_once` :341） |
| day_summary 无事件日清理 | 每轮 poll round（discover 阶段） | `summary/day.py:228-231`（删除点）+ `:264-276`（flush 门控感知待清理日） |
| 向量孤儿清扫 | embed flush 触发（非定时） | `vec.py` `sync_changed`/`sweep_orphans` |
| chat_store messages"只存今天" | 每小时 tick + 启动一次 | `services/shared/persistence/chat_store.py:37,156,628`（`_PRUNE_INTERVAL_S=3600`） |
| recording 周期（跨天 finalize + idle 会话 TTL=1h） | tick=`meta_flush_interval_s`=60s | `services/recording/service.py:111+`；`configs/dev/server.yaml:256-257` |

**video 数据面无清理（本次对象）**——实测体量（`/opt/lvdata/lvdata_inventory.db`）：

- video files 1700 个 / **144.3 MB**（瘦身后口径）
- 其中 `uuid_ts_ms` >72h 的 768 个；无 video 事件的 642 个 = **ok 597 个**
  （可回收 **50.9 MB**，D1 拍板口径）+ failed 45 个（审核拒绝帧，**保留**）
- 126 个 >72h 的有事件帧（关键帧）保留；<72h 的 932 个不动

## 2. 需求（明确化）

1. 触发：随 `_prune_loop` 每天执行一次（含服务启动时一次）；
2. 判据：`file_type='video'` 且帧龄 > 保留期（默认 72h）且**无 video 事件** → 删除
   （D1 细化：仅 `analysis_status='ok'` 的帧）；
3. 配置：`video_retention_hours`（默认 72；低于 24 → 按 24）；
4. 范围：**仅 video**；不改 events/其他 file_type。

## 3. 关键事实与约束（codebase 依据）

1. **事件判据**：video 事件 `event_ref = f"{rsession_id}:video:{frame_id}"`，其中
   `frame_id = Path(file_path).stem`（`vision.py` `_extract_chunk` upsert）——判
   "有无 video event" = 集合检查 `{rsession_id}:video:{stem}` ∈ 现有 video 事件 refs
   （refs 现仅 191 条，进程内集合短路即可）；
2. **files 表结构**（真实库 `.schema files`）：含迁移列 `device_uuid /
   analysis_status(DEFAULT 'pending') / fail_count / analyzed_at / error /
   src_updated_at / processed_*`；`file_path UNIQUE`、`uuid_ts_ms`、`file_size`；
3. **cs 只读约束**：`recording_watch.py:129` `ATTACH 'file:...?mode=ro' AS cs`——
   lvdata-mcp **不得写 chat_server.db** → 本设计**不触 `cs.recording_files`**
   （元数据行保留——实测 video 行 ≈258B/行，本次口径 597 行 ≈ 150KB，可忽略）；
4. **同步不会复活**：keyset `(updated_at, file_id)` 只消费水位之后的新行
   （`recording_watch.py:166-168`）——删除 files 行后日常同步不拉回；
5. **⚠️ `inventory --force` 全量重放会复活**：`check(force=True)` 重置水位到
   `("",0)` 全量重放（`recording_watch.py:160-161`）；被删行以 **INSERT 新行**
   回来，video 新行初始 `analysis_status='pending'`（`recording_watch.py:213,220`）
   → 分析加载 jpg 失败 → `failed`。**D1 拍板"failed 先不删"后无自动自愈**：
   残留为 DB-only 行（实测 ≈258B/行），见 §7 风险（异常操作场景，量级可接受）；
6. **消费方核查**（谁读非关键帧 jpg）：
   - `get_event` 图片附件：仅对 **video 事件**（=关键帧）读 jpg（`store.py:259-266`
     `_video_image_path`）——删非关键帧不影响；
   - bff media：按 `device/session/video/<filename>` 路径解析（`bff/common/store.py:448`
     `resolve_media`，无 files 表校验）——系统内请求方均来自事件（关键帧）；非关键帧
     无系统内请求路径；
   - day_stats `images`：数据源 = video 事件（关键帧）——不受影响；
7. **不影响重分析链**：被删帧已 `ok`（分析一次即终态，不再进入取批
   `select_pending_files`）；后续 `video --force` 重跑只作用于现存文件；
8. **derived 审计 JSON**（`windows/`、`frames/`）体量小、人工核查用——本设计保留。

## 4. 设计

### 4.1 触发与节拍（`_prune_loop` 扩展）

```
_prune_loop:
  while True:
    ① try: _prune_once()        except: log  ("audit prune failed")     # 同现状
    ② try: _video_prune_once()  except: log  ("video prune failed")     # 新增，独立 try
    sleep(audit_prune_interval_s 或 86400)
```

- 节拍 = `audit_prune_interval_s`（dev 配置 86400s = 每天；启动后立即跑一次，
  与审计清理一致）；调小该配置 → video prune 频次跟随（判据幂等，重复跑无害）；
- 两项清理**独立 try**：任一失败不影响另一项、不杀 loop（沿用现有"失败下轮重试"）。

### 4.2 判据

```sql
SELECT file_id, file_path, file_size, rsession_id
FROM files
WHERE file_type = 'video'
  AND analysis_status = 'ok'                -- D1 拍板：仅删 ok；pending（在途）与 failed 保留
  AND uuid_ts_ms < :cutoff                  -- cutoff = now - retention*3600_000（D3）
```

Python 侧过滤 `f"{rsession_id}:video:{Path(file_path).stem}" not in 现有 video 事件 refs`。
（`uuid_ts_ms` 为 NULL 的行不命中 `< :cutoff`——保守跳过，不会误删。）

### 4.3 配置

- `watcher.video_retention_hours: int = 72`（`config.py` WatcherConfig，与
  audit_* 同区）；`configs/dev/lvdata_mcp.yaml` 同步一行；
- **下限 clamp（用户口径）**：运行时 `hours = max(24, video_retention_hours)`——
  0/负值同样落到 24（即**无法以 0 关闭**；如需显式关闭开关另行拍板，见 D2）；
  clamp 于使用点实现（与 `_prune_once` 的 `max(2*stage_timeout_s, 3600)` 僵尸
  窗口先例同风格，`watcher.py:321`）。

### 4.4 执行顺序与幂等（崩溃自愈）

1. 计算候选（初筛：ok ∧ 超期 ∧ 无事件，读连接）；
2. **逐候选在同一写连接上复核**（重读最新已提交态：`status` 仍 `ok` ∧ 仍无
   `{rsid}:video:{stem}` 事件；被改判 → 跳过）→ **`unlink(missing_ok=True)`
   文件 → 删行**（500 行/txn 提交）——复核收窄与 `video --force` 重跑并发的
   TOCTOU 窗口（review 修复，2026-10-07）；崩溃于两步间：行在、文件不在 →
   下轮复核通过 → `missing_ok` 跳过 → 删行 ✓ 自愈（反序会产生"行没了、文件
   还在"的永久孤儿，故禁用）；
3. 路径护栏：`Path(file_path).resolve()` 必须位于 `root_dir.resolve()` 下，
   否则跳过并 warning（防御性，file_path 来源为自身 DB）；
4. 删除结果：info log `video prune done`（`files=N bytes=X cutoff=… elapsed=…`）；
   无删除时不打 info（debug）。

### 4.5 明确不做

- 不删 `cs.recording_files` 行（§3.3 只读约束）；
- 不删 derived 审计 JSON、events、关键帧文件（§3.6/3.8）；
- 不触发 FTS/向量对账（删的是 files 与其 jpg，不动 events → 索引面无变化）；
- 不删 `analysis_status` 为 `pending` / `failed` 的帧（D1 拍板：failed 先保留——
  审核拒绝帧的 `error` 诊断信息一并保留）；
- 不新增 `/api/status` 字段（最小化；如需可观测再加，见 D5 备注）。

## 5. 决策记录（D1–D5，已拍板 2026-10-07）

| # | 问题 | 拍板 |
|---|---|---|
| D1 | 判据的 status 维度 | **仅删 `ok` 无事件帧；`failed` 先不删（用户 2026-10-07）；`pending` 不删（在途保护）**——45 个审核拒绝帧及其 `error` 诊断保留，可回收量 50.9 MB（非 54.9） |
| D2 | 下限语义 | **`max(24, 配置值)`**（按推荐；0/负值 → 24，无关闭开关） |
| D3 | 帧龄基准 | **`uuid_ts_ms`（录制时间）**（按推荐） |
| D4 | 节拍来源 | **挂 `_prune_loop`（周期 = `audit_prune_interval_s`，默认 24h）**（按推荐） | 
| D5 | 删除面 | **jpg + lvdata files 行**（cs 不动、derived/events 不动）；可观测暂仅 log（按推荐） |

## 6. 测试计划（`tests/test_retention.py` 扩展，沿用 `make_sample`/Watcher 基座）

1. **主路径**：构造 4 天前 `ok` 无事件帧 → 文件与行删除；同窗口关键帧（有事件）
   保留；72h 内新帧保留；
2. **status 维度（D1）**：>72h 的 `failed` 无事件帧 → **保留**；`pending` → 保留；
3. **下限 clamp**：`video_retention_hours=1` 时 10h 旧帧保留（clamp 24）、100h
   旧帧删除；
4. **幂等**：连续两次执行，第二次删除 0；
5. **崩溃自愈**：行在、文件已缺失 → 行被删、不抛错；
6. **路径护栏**：file_path 越 root → 跳过 + warning、行保留；
7. 抽查：events/derived JSON 不受影响。

## 7. 风险与运营

- **`inventory --force` 复活**（§3.5）：复活行被分析判 failed 后**不再被自动清理**
  （D1：failed 先不删）→ 残留为 DB-only 行（≈258B/行；异常操作场景）；若残留
  量级成困扰，届时单独拍板 failed 口径或手工清理；
- **首跑释放 ~50.9 MB**（597 个 ok 无事件帧，当前库实测）；后续按日增量；
- 审核拒绝帧（45 个 / ~4 MB）与其 `error` 诊断信息保留（D1 拍板）——确认清除
  需另行拍板；
- 磁盘与 DB 双删的短暂不一致窗口已被 §4.4 顺序设计覆盖（自愈）。
- **探针/排障**：`scripts/probe_video_filter.py` 等按 files 表全量取件——prune
  后历史非关键帧不再可复现探针（口径：仅现存帧）——接受并记录。

## 8. 实施步骤（拍板后）

S1 `watcher.py`（`_video_prune_once` + `_prune_loop` 扩展）+ `config.py`
（`video_retention_hours`）+ dev yaml；S2 测试（§6 七项）全绿；S3 dev 实跑
（首跑实删数字 vs §1 实测 597 个 / 50.9 MB 核对）；S4 文档收尾（本文档收尾记录 +
service 文档 §4.2/§4.3（配置示例 + schema）同步）。

## 9. 收尾记录（2026-10-07，已实施）

**S1–S2**：`watcher._video_prune_once`（判据 = ok ∧ >保留期 ∧ 无 `{rsid}:video:{stem}`
事件；先 unlink 后分块删行；路径越 root 跳过；`video prune done files/bytes/
skipped/cutoff/elapsed` info log）+ `_prune_loop` 双独立 try 扩展 +
`config.video_retention_hours=72`（使用点 `max(24, v)`）+ dev yaml 同步。测试
**+6 例**（主路径含关键帧/新鲜帧保留与 events/derived 不动、failed·pending
保留、clamp(1h→24h)、幂等、缺文件自愈、路径护栏）——retention 19 例、lvdata
全量全绿（exit 0）；services 1496 绿 / 1 skip（零改动）。

**S3 首跑（2026-10-07 21:06，服务重启即触发）**：**files=597 / 50.9 MB /
skipped=0 / cutoff=72h / 0.20s**——与 §1 dry-run 实测（597 个 / 50.9 MB）
**逐项一致**；video 面 1700→**1103 帧**、144.3→**93.4 MB**；抽样 200 个现存
行磁盘零缺失（无"行在文件无"残留）；服务健康（readyz=200，PID 99269）。

**S4**：本文档状态翻转 + 本节；`docs/data/lvdata_mcp_service_v2.md` §4.2 配置
示例与 §4.3（引用上表）同步 `video_retention_hours`。

**追加：配置改 24h 重跑（2026-10-07 21:10，用户指示）**：dev yaml
`video_retention_hours: 72 → 24` → 重启触发——**files=829 / 62.9 MB /
skipped=0 / 0.3s**（与 dry-run 逐项一致：10-05 剩 177 + 10-06 瘦身后 644 +
10-04 8）；video 面 1103→**274 帧**、93.4→**30.4 MB**；余量 = 关键帧 191
（事件数 191 对齐）+ failed 45 + <24h 新帧 38；现存行磁盘零缺失；服务
健康（PID 99374）。

**review 修复（2026-10-07，1 处）**：`_video_prune_once` 初筛（读连接）与删除
（写连接）之间的 TOCTOU——候选被选中后若 `video --force` 重跑并发到达（force
把全部帧瞬时重置为 pending、判完关键帧建事件），过期候选仍会被 unlink/删行，
**关键帧 jpg 不可恢复丢失**。修复：**逐候选在同一写连接上复核**（`status`
仍 `ok` ∧ 仍无事件）后才删，竞态窗口自 ~秒级收窄至微秒级；崩溃自愈顺序
（先删文件后删行）不变；§4.4 执行顺序同步更新。测试全绿（retention 19 例 +
lvdata 全量）、服务重启加载修复版（PID 99693，首跑无操作确认）。
