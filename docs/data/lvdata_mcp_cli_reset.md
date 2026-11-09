# lvdata-mcp reset CLI：分析与设计

> 状态：v1 已实施（`tools/lvdata-mcp/src/lvdata_mcp/reset.py` 与 `tests/test_reset.py`）；
> 核心取舍已按推荐拍板（见「决策状态」与 §10.1）；
> 2026-11-08 按第二轮评审修订：plan 只读、媒体行随主事务、非活跃时区保留
> pending、server 探测 fail-closed、FTS 缺 session 兜底、向量本地清理、
> 部分失败退出码与共享 window 状态。第三轮补充：健康检查绕过 HTTP_PROXY、
> unlink 失败保留 ok 供重试、SQLite URI 转义、空 scope 跳过扫描、统一 JSON
> 错误契约、provider 数值占位符默认值、9999-12-31 边界。第四轮补充：
> unlink 失败整 session 延后、向量 sweep 无条件执行、本地清理不建 schema、
> 跨窗 derived 提交前删除、argparse JSON 错误、负偏移时区最大日期安全归组。
> review4 补充：向量孤儿/ref 分批删除、最大日期实际执行与不截短查询窗口、
> `--config` 值误判修复、sweep/unlink 审计计数。review5 补充：DataStore 只读
> URI 统一 `Path.as_uri()`、最小/最大本地日窗口的安全归组与渲染、跨窗 derived
> 实际删除计数、跨日敏感测试改用固定时钟。review6 补充：embedding 响应数量/
> index 校验与调用方写前复核、unlink 部分成功计数、remove_refs 大批量回归。
> 本文以当前 `tools/lvdata-mcp` 代码为唯一事实来源，关键断言附 `file:line`；
> 不记录方案演进历史。

**结论**：`reset` 以 recording session 为作用域（fallback 只对 `session_date=''`
启用）；删除 `audio`/`video` 中 `analysis_status='ok'` 且无对应事件的源文件
（不备份、不删 chat/meta），**同时清除选中 session 的全部已知非 window events
及其派生数据**（FTS / 向量 / day_summary / day_stats / derived 审计 JSON），把保留
文件恢复为 `pending`（chat 清空 replay 水位），然后由 server 的下一次 poll/round
自然重新分析。这样避免旧事件、旧窗口、旧日摘要残留。

**v1 运行前置**：默认要求 server 未运行（三态探测：running/stopped/unknown；
只有明确 connection refused 视为 stopped，超时/DNS 等 unknown 也拒绝执行；
`--allow-running-server` 显式覆盖并输出 warning，见 D11/§7.5）。未知 event type
保留 + warning（无重建路径，不默认删除）；所有时区的 day_summary 均置 pending
（非活跃时区在再激活后重试，D7/§5.6）；`--date` 造成的 session 放大（范围外
文件/事件）必须在 plan 中可见（D2/§4.2）。

| 用户要求 | 设计落点 |
|---|---|
| 删除已 parse 但无 events 的文件 | §3 D3/D4/D5 |
| 未 parse 的文件不能删除 | §2.2、§3 D4 |
| 重置 files 状态 | §3 D8 |
| **删除对应的 events，避免残留** | §3 D6/D7（已知类型；未知 type 保留）、§5.3-§5.7 |
| 由下一次 poll 重新分析，不主动 trigger | §3 D9、§5.9 |
| `--date` 必填；支持 YYYY / YYYY-MM / YYYY-MM-DD | §3 D1、§4.3 |
| 不做 `--all`、不做 from..to | §1.1 |
| 以 codebase 为准 | §2 全节、§11 锚点 |

### 决策状态（按推荐拍板）

以下核心取舍已按推荐值拍板（详见 §3 / §10.1）：

- **作用域**：按 recording session（D2）；fallback 仅对 `session_date=''` 启用；
  不做严格文件/日期切片。
- **事件 purge**：选中 session 的全部**已知**非 window events + window 归零剔除
  （D6/D7）；未知 type 保留 + warning。
- **video 删除**：v1 按文件删除（D3/D4，沿用 video prune）；非关键帧删除会改变
  重分析输入，plan 强制告警；window-aware 后续。
- **源文件缺失事件**：仍 purge + warning（D13）。
- **跨 session window**：归零规则保留 + warning（D15）；扩大 scope 后续。
- **derived**：删除目标 session 的审计 JSON（D14）。
- **运行中 server**：v1 默认拒绝（三态 `/healthz` 探测，unknown 也拒绝），要求
  stop → reset → start；`--allow-running-server` 显式覆盖并输出 warning；
  `reset_requests` 后续。
- **非活跃时区 day_summary**：保留 pending 行（recompute day_stats），再激活时由
  discover 重试；不再直接删行（D7/§5.6）。
- **未配置 embedding**：本地 rag DB 存在时仍用本地连接清理（不建 schema、
  不依赖 key）；无 DB 才跳过。
- **day_summary**：接受额外 LLM；大范围用 stop → reset → start 降低。
- **交互**：`--dry-run` + `--json` + `--yes` + `--allow-running-server`；
  `--keep-events` / `--keep-files` / `--keep-derived` /
  `--keep-missing-source-events` / `--purge-unknown-types` / `--timezone` /
  `--all` / from-to 均为后续。
- **tombstone**：不做，沿用 video prune 先例。

## 1. 需求与范围

新增 CLI：

```bash
lvdata-mcp reset --date <YYYY | YYYY-MM | YYYY-MM-DD>
```

- `--date` 必填；只接受 `YYYY`、`YYYY-MM`、`YYYY-MM-DD` 三种粒度。
- 实际执行需 `--yes`（无备份的破坏性操作；见 §3 D12）；`--dry-run` 只打印计划。
- 本期不做 `--all`，不做 `--from ... --to ...` 区间。
- reset 逻辑（用户口径）：
  1. 删除**已经 parse 过、但没有生成 events**的文件，不做备份；**没有 parse 过的文件不能删除**。
  2. 重置 files 状态。
  3. **删除对应的已知类型 events**（未知 type 保留 + warning），避免重分析后旧
     事件/旧窗口/旧日摘要残留。
  4. 由 lvdata-mcp server（stop→reset→start 后）重新分析；能靠下一次 poll 自然
     触发时不主动调用 REST。

### 1.1 非目标

- 不删除 chat / meta 源文件（见 §3 D3）。
- 不删除未知 `type` 的 events（保留 + warning；无已知重建路径）。
- 不保证运行中 server 下派生面（day_summary / day_stats）无 stale（见 D11/§7.5；
  v1 默认拒绝在 server 运行时执行）。
- 不写 `chat_server.db`（`recording_files` 只读）。
- 不删除 `recording_sessions` / `analysis_runs` / `mcp_pipeline_runs`（审计历史保留）。
- 不做备份、回收站、软删除。
- 不提供 `--all` / `--from/--to`。
- 不保证跨 session video 窗口的完全原子重建（见 §7.3）。

---

## 2. codebase 事实盘点

### 2.1 files 类型与状态

`files.file_type` 取值（`recording_watch.py:43-51`）：

| file_type | 来源 cs kind | 初始 analysis_status | 是否有分析产物 |
|---|---|---|---|
| `audio` | `audio` | `pending` | 有（audio event，可能静音不产） |
| `video` | `video` | `pending` | 有（video/window event，非关键帧不产） |
| `meta` | `session` | `na` | 无（session.json 元数据） |
| `chat_history` | `chat_history` | `na` | 有（chat / agent_job event） |
| `chat_transcribe` | `chat_transcribe` | `na` | 有（transcribe event） |
| `chat_translate` | `chat_translate` | `na` | 有（translate event） |

状态机（`analysis/common.py:71-82`）：

- 成功：`analysis_status='ok'`、`fail_count=0`、`error=NULL`、`analyzed_at=now`；
- 失败：`analysis_status='failed'`、`fail_count+1`、`error`、`analyzed_at=now`；
- 取批只选 `pending|failed` 且 `fail_count < 3`（`analysis/common.py:28-61`）；
- chat 文件成功处理后回 `na`，失败会短暂为 `failed`（`analysis/chatlog.py:246-267`）。

### 2.2 “已经 parse 过”的代码口径

| 类型 | 已 parse 的判据 | 依据 |
|---|---|---|
| `audio` | `analysis_status='ok'` | 空文件/静音也走 `mark_file_ok`（`analysis/audio.py:444-469,576`） |
| `video` | `analysis_status='ok'` | 整窗 filter+extract 成功才把该窗所有帧置 ok（`analysis/vision.py:480-500`） |
| `chat_*` | `processed_updated_at IS NOT NULL`（读到文件末尾） | `analysis/chatlog.py:246-261` |
| `meta` | 不适用（无分析） | `analysis_status='na'` |

`pending` / `failed` = 没有成功 parse：**不删除**，只重置状态后重试。

### 2.3 “生成了 events”的判据

事件 ref 由分析模块确定性铸造：

| 类型 | event_ref | 依据 |
|---|---|---|
| `audio` | `<rsession_id>:audio:<file_path stem>` | `analysis/audio.py:568` |
| `video` | `<rsession_id>:video:<file_path stem>` | `analysis/vision.py:596` |
| `window` | `<rsession_id>:window:<window_id>`；`window_id` = 窗口首帧 stem | `analysis/vision.py:466` |
| `chat_history` | `chat:*` / `agent_job:*` | `analysis/chatlog.py:67-91` |
| `chat_transcribe` | `transcribe:*` | `analysis/chatlog.py:93-102` |
| `chat_translate` | `translate:*` | `analysis/chatlog.py:104-115` |

因此对 `audio` / `video`，“该文件是否生成 event”可用 `files` 行与 `events.event_ref`
集合直接判定，不需要读 derived JSON。

### 2.4 现有删除先例：video prune

`watcher._video_prune_once`（`watcher.py:341-410`）已实现同类删除：

- 候选 = `file_type='video' AND analysis_status='ok'` 且无
  `<rsession_id>:video:<stem>` 事件；
- 逐候选在同一写连接复核（状态仍 ok、仍无事件）→ `unlink(missing_ok=True)`
  → `DELETE FROM files`；
- 不删 events / FTS / 向量 / derived 审计 JSON；不写 `cs.recording_files`；
- 接受 `inventory --force` 复活被删 files 行（`docs/data_clean.md` §3.5、D6）。

本设计复用同一“无事件媒体文件”删除语义，但 reset 会进一步清除 events 与派生面，
不是 prune 的纯文件清理。

### 2.5 重新分析的自然触发

- watcher work loop 每轮固定执行
  `inventory → chatlog → audio → video → day_stats → reindex`（`watcher.py:42,185-226`）；
- `audio` / `video` 取批条件 = `analysis_status IN ('pending','failed') AND fail_count < 3`
  （`analysis/common.py:28-61`）；
- `chatlog` 取批条件 = `fail_count < 3 AND (processed_updated_at IS NULL OR
  src_updated_at > processed_updated_at)`（`analysis/chatlog.py:36-41`）。

所以只要把 files 行改回 `pending`（media）或清空 chat 水位，**下一次 round 就会自然
重新分析**，不需要 REST `trigger`。

### 2.6 日期字段

| 字段 | 语义 | 依据 |
|---|---|---|
| `recording_sessions.session_date` | session.json 的录制本地日期 `YYYY-MM-DD`（可能为空） | `recording_watch.py:267` |
| `files.uuid_ts_ms` | 音频/视频文件时间戳（UUIDv7；chat/meta 为 NULL） | `recording_watch.py:213-220` |
| `events.ts_ms` | 事件时刻 | `ddl.py:20-39` |
| `watcher.timezone` | 服务默认时区（日期窗换算） | `config.py:177` |

`timeutil.day_bounds_ms(date, tz)` 返回某本地日的闭区间 `[日首, 日尾]`
（`timeutil.py:75-86`）。

注意：`session_date` 是**设备录制本地日期字符串**（session.json），而
`files.uuid_ts_ms` / `events.ts_ms` 的窗口用 `watcher.timezone` 换算——两者是
两套口径，跨时区设备上可能不一致（见 D1/D2 的命中原因输出）。

### 2.7 分析是 upsert，不会自动删除旧事件

- `upsert_event` 对同一 `event_ref` 执行 `ON CONFLICT DO UPDATE`，刷新
  `summary/payload/updated_at`（`analysis/common.py:106-124`）；
- `upsert_event_if_changed` 只在 `summary` 变化时刷新（chat 重放专用，
  `analysis/common.py:127-148`）；
- 分析模块**不删除**旧的 events：audio 由非静音变静音、video 关键帧判定变化、
  窗口重新分组后，旧 audio/video/window 事件都不会自动消失。

这就是本设计必须**显式删除 events**的原因：仅 reset 文件状态不足以避免残留。

### 2.8 video 窗口分组与跨 session

- video 按 **device** 分组，组内按 `uuid_ts_ms` 时间窗分组；session 不参与分组，
  `window_id` = 窗口首帧 stem（`analysis/vision.py:126-160,163-169`）；
- 关键帧集为空 → 不产 window 事件、不产 video 事件（`analysis/vision.py:463-474`）；
- 非关键帧本身不产 video 事件（`analysis/vision.py:488-500,596-601`）；
- window 事件 `payload.window_id` 是跨 session 关联键（`remove.py:108-145`）。

因此事件清除必须处理 **window 事件与 video 事件的跨 session 归零关系**：
只有某 `window_id` 下已无任何 video 事件时，才能删除对应 window 事件；否则会留下
孤儿 window 事件或误删仍有 video 事件的窗口。

### 2.9 `inventory --force` 会复活被删 files 行

`recording_watch.check(force=True)` 把 keyset 水位重置为 `("",0)` 全量重放
（`recording_watch.py:158-161`）。被删除的 files 行会以新行重新插入（media 初始
`pending`，`recording_watch.py:213-220`），随后因物理文件不存在而分析失败。

现有 video prune 已接受该行为（`docs/data_clean.md` §3.5、D6；`docs/bff_service_remove.md`
D6，不做 tombstone）。本设计沿用同一取舍。

### 2.10 事件删除先例：`remove.EventRemover`

`docs/bff_service_remove.md` + `remove.py` 已实现 video 事件删除的级联：

- T1 主事务：删 events；按 `payload.window_id` 跨 session 计数，归零则删 window 事件；
  删 `search_docs`；对所有 `day_summary` 中的时区重算受影响日的 `day_stats`
  （`remove.py:80-145`、`_recompute_days` `:147-163`）；
- T2 向量：`vec_index.remove_refs(refs)`，异常记 `vectors_pending`（`remove.py:167-176`）；
- 文件面：先删 jpg 再删 files 行（本 reset 只对“无事件媒体文件”做这一步）。

reset 复用其**窗口归零 + FTS 删除 + day_stats 重算 + 向量删除**思路，但范围从
video 扩展到选中 session 的全部已知事件类型（未知类型保留）。

### 2.11 删除事件不会自动更新 FTS / 向量 / day_summary

- FTS 增量取件是 `lag_sessions`：会话 `MAX(events.updated_at) > index_state` 才重建
  （`index.py:147-163`）。**删除 events 不 bump `updated_at`** → 会话不再判滞后，
  旧 `search_docs` 行会永久残留（除非 force 全量）。
- 向量孤儿清扫只在 `embed` stage 的 `sync_changed`/`sweep_orphans` 运行
  （`vec.py:240-288,319-346`）；删除事件本身不触发。
- `day_stats` 候选 = `events.updated_at > 游标`；删除不可见（`summary/day_stats.py:197-212`）。
- `day_summary` 的 dirty 发现依赖存活事件；删除不产生信号，ok 行旧 doc 会 stale
  （`summary/day.py:188-271`）。

所以 reset **必须显式清理/重建**这些派生面，否则“删除 events”反而留下更多残留。

### 2.12 进程与写入模型

- lvdata-mcp 是 lvdata DB 数据面的唯一写入方；本 CLI 是第二个写进程；
- DB 为 WAL，写连接普遍 `busy_timeout=5000`（`main.py:128-133` 等）；
- watcher 的 prune 也是独立写连接线程，说明 SQLite 层“CLI/后台写 + 运行中 server”
  可串行化（`watcher.py:291-303,341-410`）；但 reset 的派生面清理**不能**沿用该先例
  作为运行中执行的依据（见 D11）；
- server 的分析 stage 与 CLI 没有共享内存锁；只能靠 SQLite 写事务串行化与
  “删除候选只取 `ok` 行”缩小竞态窗口。

### 2.13 day_summary 只遍历活跃时区

- `active_timezones(conn, default_tz)` = 最近使用的 enabled 时区 ∪ 配置默认时区，
  **至多 2 个**（`summary/timezones.py:13-29`）；
- `discover` 与 `_check_pending_retry` 只对活跃时区执行（`summary/day.py:194-207`）；
- `day_stats` 同样只遍历活跃时区（`summary/day_stats.py:184-193`），但
  `recompute_days` 可按任意 `(tz, day)` 显式重算。

因此非活跃时区的 pending 行在**再激活前不会**重新生成（空日卡是预期过渡态）；
但保留 pending 行后，该时区重新进入 active 集合时 `_check_pending_retry` 会
立即重试。直接删行则可能因扫描游标已过、无新事件而永久缺失（见 D7/§5.6）。

---

## 3. 关键设计决策

### D1. 日期粒度与时间范围

`--date` 语法（严格、补零）：

| 输入 | 范围（本地日期） |
|---|---|
| `YYYY` | `YYYY-01-01` ~ `YYYY-12-31` |
| `YYYY-MM` | 该月首日 ~ 该月末日 |
| `YYYY-MM-DD` | 该日 |

- 用 `datetime.strptime` / `calendar.monthrange` 校验真实日期（拒绝 `2026-02-30`、
  `2026-13` 等）；
- 时间范围的时区 = `watcher.timezone`；
- 媒体时间戳回退查询用 `day_bounds_ms(start_date)` 的日首与
  `day_bounds_ms(end_date)` 的日尾；
- **两套日期口径**：主选按 `session_date`（设备录制本地日期字符串）比较；回退
  按 `watcher.timezone` 换算的 ms 窗口；plan 必须同时输出 `session_date` 与命中
  原因（primary/media/events）；
- 不提供 `--all` / `--from` / `--to`（本期明确不做）。

### D2. 作用域按 recording session 取

`--date` 先解析为日期范围，再选 **recording_sessions**：

```
sessions = { rsession_id | recording_sessions.session_date BETWEEN start_date AND end_date
                            AND session_date != '' }
         ∪ { rsession_id | session_date = ''（或 session 行缺失）
                            AND 该 session 有 audio/video 文件 uuid_ts_ms 在时间窗内 }
         ∪ { rsession_id | session_date = ''（或 session 行缺失）
                            AND 该 session 有 events.ts_ms 在时间窗内 }
```

理由：

1. chat/meta 的 `uuid_ts_ms` 为 NULL（`recording_watch.py:213-220`），无法按文件
   时间戳切片；
2. video 窗口按 device 分组、跨 session（`analysis/vision.py:146-160`）；按单文件
   日期切片会把一个窗口拆成“部分帧在范围内、部分帧在范围外”，重分析分组不可复现；
3. `session_date` 是录制本地日期的既有字段（`recording_watch.py:267`），是存储层的
   自然日期键；
4. **fallback 只对 `session_date=''` / session 行缺失的 session 启用**：对
   `session_date` 非空且在范围外的 session，单个 ts 漂移的 event 不能把它拖入
   破坏性 reset。plan 仍须展示每次命中的原因与 scope 放大计数（见 §4.2），
   因为 fallback 命中的 session 一旦入选仍是整 session reset。

选中 session 后，**该 session 的全部 files 都进入 scope**（meta 除外，见 D3），
**该 session 的全部已知非 window 事件都进入 purge 范围**（见 D6；未知 type 保留）。
已知语义：

- session 跨午夜时，按 `session_date` 命中即整 session reset，可能包含日期范围外
  的文件/事件；
- 跨 session 的 video 窗口仍可能只包含部分 session 的帧（见 §7.3）。

### D3. 删除文件：只删 audio / video，不删 chat / meta

| 类型 | 处理 |
|---|---|
| `audio` | `ok` 且无 audio 事件 → 删除物理文件 + `files` 行；否则保留并 reset |
| `video` | `ok` 且无 video 事件 → 删除物理文件 + `files` 行；否则保留并 reset |
| `chat_history` / `chat_transcribe` / `chat_translate` | **不删除**；清 replay 水位 |
| `meta` | **不删除、不重置** |
| 未知 file_type | 跳过 + warning |

理由：

- chat jsonl 是原始对话数据（`services/recording/session.py:130-134` 的 writer
  产出，append-only），`chatlog` 只是把它事件化；删除源文件会丢失无法重建的
  用户数据。**这是对用户“删除已 parse 无事件文件”字面要求的刻意收窄**：空的
  chat jsonl 也符合字面，但安全优先，永不删除 chat/meta。
- `session.json` 是 session 元数据，删除会破坏建档链路。
- `audio` / `video` 是分析媒体；已 parse 且无事件的文件属于“分析判定无信息量”的
  产物，删除有既有先例（video prune）。
- **video 特别注意**：非关键帧虽然无 video 事件，但仍是 window filter 的输入
  （`analysis/vision.py:463-500`：整窗帧都置 `ok`，只有关键帧产事件）。删除后
  窗口会重组、关键帧可能重选，**reset 之后的重新分析不是原分析的等价重跑**。
  v1 接受该语义（与用户“删除无事件文件”的要求一致），但必须在 plan/警告中显式
  提示；若目标是修复坏分析而非清理媒体，应谨慎使用（见 §6.4、§10.2）。

### D4. “无事件”判定

- audio：`event_ref = f"{rsession_id}:audio:{Path(file_path).stem}"`，
  `events` 中存在该 ref → 有事件；
- video：`event_ref = f"{rsession_id}:video:{Path(file_path).stem}"`，同上；
- 只对 `analysis_status='ok'` 的行做该判定；
- `failed` / `pending` 一律不作为删除候选（没有成功 parse）。

### D5. 删除载体与顺序

1. 路径护栏：`Path(file_path).resolve()` 必须在 `root_dir.resolve()` 下，否则跳过 + warning；
2. `unlink(missing_ok=True)` 删除物理文件（事务前）；
3. files 行在该 session 主事务内删除（与事件 purge/状态重置一起提交或回滚）；
4. unlink 失败 → **整个 session 主事务延后**（不提交），保持 events/files 行原状，
   下一轮仍可通过 primary/media/events 任一入口选中重试；
5. 不写 `cs.recording_files`（只读 ATTACH，`recording_watch.py:121-134`）；
6. 不做备份/回收站。

文件与行不是原子操作；崩溃后：
- 行在、文件不在 → 下次 reset 仍命中“ok + 无事件”，`missing_ok` 跳过 unlink，
  再在主事务删行（与 video prune 自愈方向一致）；
- 文件在、行已删 → 成为孤儿文件；本命令不扫描孤儿；可后续手工处理。

### D6. 删除对应的 events（核心新增）

**purge 范围 = 选中 session 的全部已知非 window events**，但 window 事件按“无剩余
video 事件”规则单独判定（见 D7）。具体：

1. 对每个目标 session `S`，查询其全部 events（类型、ref、ts、payload）；
2. **删除已知非 window 事件**：
   `DELETE FROM events WHERE rsession_id=S AND type IN
   ('chat','transcribe','translate','agent_job','audio','video')`；
   - 这些事件由 chat 重放或 media 重分析重新生成；
   - **未知 `type` 保留 + warning**：没有已知重建路径，默认删除等于不可恢复的
     数据丢失；只有显式 `--purge-unknown-types`（后续）才删。
3. **window 事件归零剔除**：
   - 收集 S 内被删 video 事件的 `payload.window_id`，以及 S 内 window 事件的
     `payload.window_id`；
   - 对每个 `window_id`，查全库剩余 video 事件（实现复用
     `remove._payload_field` 的 Python 解析，遍历 `type='video'` 的 payload；
     与 `remove.py:108-145` 同口径，不依赖 SQLite JSON1 扩展）；
   - 剩余为 0 → 删除该 `window_id` 的所有 window 事件（可能在其他 session）；
   - 剩余 > 0 → 保留 window 事件（跨 session 窗口仍有 video 事件），输出 warning；
   - S 内 payload 缺失/无法解析 `window_id` 的 window 事件：直接删除 + warning
     （无关联 video 事件可保留，属于异常行）。

4. 收集本次实际删除的 `event_ref` 与 `ts_ms`（不含保留的未知类型），供
   FTS/向量/day_summary/day_stats 清理使用；
5. 收集受影响的 session 集合 = 目标 session ∪ 被删 window 事件所在 session，
   用于 FTS 重建。

> 为什么删除全部已知非 window events：如果只删“文件对应的事件”，旧的 chat/agent_job
> 事件、被 video prune 删掉帧的 video 事件、以及重分析后不再产出的 audio 事件都会
> 残留。按 session 全量 purge 才能保证“当前文件集合的再次分析”是唯一事实。未知
> type 无法由本服务的分析链路重建，因此保留（宁可残留也不做不可恢复删除）。

### D7. 事件派生面清理

事件删除后必须在同一主事务内清理/重建：

| 面 | 操作 | 依据 |
|---|---|---|
| `search_docs` (FTS) | 先检查 schema：不存在 → `index.ensure_index(conn)`；存在但缺 `orig_text`/`session_id` 等必需列 → **中止**（不得静默 drop，否则清空全库 FTS）；否则对受影响 session 调 `index.rebuild_session`（先 DELETE 该 session 的 docs，再从剩余 events 重建）；**session 行缺失时用 `events.device_uuid` 兜底重建**（不再清空保留事件的索引） | `index.py:78-114`、`reset.py:_rebuild_fts` |
| `index_state` | 删除目标 session 的行（强制下次 reindex 重建；`''` 全局行保留） | `index.py:125-163` |
| `vec_chunks` / `vec_state` | 收集被删 refs，`vec_index.remove_refs(refs)` 分批删除；本地删除不依赖 embedding key（无 key 时用本地 dim 构造 VecIndex）；失败记 `vectors_pending` | `vec.py:290-307` |
| `day_summary`（所有时区） | 受影响 `(timezone, local_date)` 置 `pending`、清空 doc/stats 重试水位；非活跃时区保留 pending 行供再激活重试 | `summary/day.py:345-358` |
| `day_stats` | 对所有时区的受影响日调用 `DayStats.recompute_days`（本地 SQL，无 LLM） | `summary/day_stats.py:252-287` |
| `day_deferred` | 删除受影响 `(timezone, local_date)` 行，避免旧 deferred 触发 | `ddl.py:104-111` |
| `day_scan_state` | 不改；待生成行由 `_check_pending_retry`（活跃时区）兜底 | `summary/day.py:345-358` |
| `day_stats_state` | 不改；显式 recompute 已覆盖，后续新事件再推进 | `summary/day_stats.py:197-212` |
| `flush_state` | 不改；重分析事件会重新满足 flush 门控 | `watcher.py:230-258` |

`day_summary` 清理（**所有时区**，每个受影响日）：

```sql
UPDATE day_summary
SET status='pending', generated_at=NULL, fail_count=0, error=NULL, model=NULL,
    day_summary='{}', event_count=0, day_stats='{}'
WHERE timezone=? AND local_date=?;

DELETE FROM day_deferred WHERE timezone=? AND local_date=?;
```

随后对所有时区执行 `DayStats.recompute_days(conn, tz, [day], t0)`：

- 有剩余事件 → 更新 `day_stats`，行保持 pending，等 day_summary 重新生成；
- 无剩余事件 → 因 `status != 'ok'` 删除该行（日卡随数据消失）；
- 未被删除的其它日不受影响。

`day_summary` 置 pending 后，`_check_pending_retry` 会在**活跃时区**重新入队
（`summary/day.py:345-358`），`_debounce_ok` 仍遵守 `summarize_quiet_s`；若重分析
产生新事件，`updated_at` 会再次触发 discover。非活跃时区保留 pending 行，该时区
再次激活时会被立即重试；再激活前显示空/缺失日卡是预期的过渡态。

### D8. 状态重置

| 类型 | 操作 |
|---|---|
| `audio` / `video`（保留的行，含 pending/failed/na） | `analysis_status='pending'`、`fail_count=0`、`error=NULL`、`analyzed_at=NULL` |
| `chat_*` | `processed_offset=0`、`processed_updated_at=NULL`、`fail_count=0`、`analysis_status='na'`、`error=NULL` |
| `meta` | 不操作 |

- 不重置 `src_updated_at`（chat 水位需要它；media 不使用）。
- 不重置 `recording_sessions.analysis_status`（当前流水线不读该列；见
  `analysis/common.py:28-61` 的取批条件）。
- 不重置 `analysis_runs` / `mcp_pipeline_runs`（审计历史保留）。

### D9. 重新分析：依赖下一次 poll，不主动 trigger

状态重置后：

- media：下一次 work round 的 `audio` / `video` stage 通过 `select_pending_files`
  自然取件；
- chat：下一次 `chatlog` stage 因 `processed_updated_at IS NULL` 自然取件；
- 若 server 未运行，下一次 `serve` 启动后的首轮同样会取件。

不需要调用 `POST /api/analysis/trigger`。分析预算仍由
`watcher.max_files_per_poll` 与 `watcher.concurrency` 控制，大范围 reset 会分摊到多轮。

### D10. 事务顺序与崩溃恢复

入口：**只读连接**建计划（不建表/不迁移/不切 WAL）→ 打印计划 →（仅实际执行前）
server 运行探测（三态，D11/D12）→ `_ensure_schema(cfg)` → 重新选 session 执行。
之后按 session 处理，每个 session 一个**主库写事务**（`BEGIN IMMEDIATE`）：

1. （主事务前）unlink 该 session 的“ok + 无事件”媒体文件：每批在独立的
   `BEGIN IMMEDIATE` 短事务内复核 status/event 后 `unlink`，**不删 files 行**；
   任一 unlink 失败 → 跳过该 session 主事务（保持 events/files 行），下一轮重试；
2. （主事务前）删除该 session 的 derived 审计 JSON（D14），避免重分析写入新
   audit 后又被删除；
3. 主事务 `BEGIN IMMEDIATE`；
4. 删除该 session 的**已知**非 window events（未知 type 保留 + warning）；按 D6
   归零剔除 window events（共享 `WindowState`，不再每 session 全表扫描）；
5. 在主事务内删除第 1 步收集的 files 行（与 purge 一起提交/回滚）；
6. 对受影响 session 重建 FTS（`index.rebuild_session`；缺 session 行时用
   `events.device_uuid` 兜底）；
7. 删除目标 session 的 `index_state` 行；
8. 对被删事件 `ts_ms` 影响的日：**所有时区**更新 `day_summary` pending + 清
   doc/stats，删 `day_deferred`，并 `DayStats.recompute_days`；
9. 重置该 session 的 files 状态（D8）；
10. `COMMIT`；
11. （事务后）批量 `vec_index.remove_refs(deleted_refs, create_schema=False)`
    （reset 按 500 切批，`remove_refs` 内部再按 SQLite 变量上限分批）；
    失败记 `vectors_pending`；
12. 处理下一个 session。全部 session 结束后**无条件** `sweep_orphans(
    create_schema=False)`（即使 remove 失败或 scope 为空；孤儿删除同样分批）。

全部 session 完成后：

- 若存在向量清理对象，**无条件**调用 `vec_index.sweep_orphans(
  create_schema=False)` 兜底清理崩溃/remove 失败残留（不依赖 embedding key，
  不猜测维度建表；空 scope 也执行；本地 DB 不存在时直接返回 0，不建文件）；
- 打印汇总；不调 REST。

崩溃语义：

- 主事务回滚 → 该 session 的 events / files 状态 / FTS / day_summary / day_stats /
  index_state 均未变；主事务前已 unlink 的文件不恢复，但 **files 行仍在**，
  重跑可通过 media fallback 重新选中该 session；derived 由重分析重建；
- 主事务已提交 → 该 session 已 purge/reset；server 可能已开始重分析；重跑 reset
  会再次 purge 新事件（幂等，最终仍为 reset 状态）；
- 向量删除在事务后 → 崩溃时可能残留；重跑时 `sweep_orphans` 清理；
- 文件/derived 删除在事务前 → 崩溃时 files 行保留、audit 可能已删但 events 未
  purge；重跑按“ok + 无事件”重新判定（`missing_ok` 自愈）。

单 session 异常（SQL/IO）→ 该 session 主事务回滚，记录 `failed_sessions`，继续处理
其余 session。最终退出码：任一 `sessions_failed` / `failed_unlinks` /
`vectors_pending` 为真 → `1`（部分完成），否则 `0`。重跑 reset 幂等。

### D11. 并发与竞态

- **v1 前置：默认要求 server 未运行**。CLI 在写库前做三态探测（`GET /healthz`）：
  running 或 unknown（超时/DNS/其他无法判定）都 exit 1，只有明确 connection
  refused 才视为 stopped。`--allow-running-server` 才继续，并在 text/JSON 输出
  stale-risk warning，接受下文全部竞态。健康检查使用 **空 ProxyHandler**，
  显式绕过 `HTTP_PROXY`/`ALL_PROXY`，避免代理拒绝被误判为 stopped。
- **派生表的在途写回是比文件竞态更隐蔽的残留源**：
  - `DaySummary._generate_one` 在 LLM 调用前读事件快照、调用后**无条件**写回
    `status='ok'`（`summary/day.py:439-534`）。若该写落在 reset 事务之后，会把
    已清空的 doc 恢复成旧值；若重分析不再产事件（静音 audio、零关键帧窗口），
    `discover` 只清理 `status != 'ok'` 的行（`summary/day.py:230-243`），该日卡会
    **永久 stale**，不会自愈。
  - `DayStats._run_day` 同样先读 events 再写 `day_stats`（`summary/day_stats.py:214-250`），
    可把旧事件的统计覆盖回 reset 的 recompute 结果。
  - （仅 `--allow-running-server` 场景）多 session 逐个提交会让运行中的 server 看到
    “半 reset”状态，按混合数据生成日摘要（额外 LLM 成本 + 中间态），最终仍可能被
    上面的在途写回固化。
- 无事件媒体删除阶段只取 `analysis_status='ok'` 的行；server 的分析取批只取
  `pending|failed`（`analysis/common.py:28-61`），正常分析不会与删除候选重叠；
- 无事件媒体删除阶段在独立的 `BEGIN IMMEDIATE` 短事务内**复核** `status='ok'` 且
  ref 不存在，再 unlink；**files 行不在这里删除**，而是在该 session 的主事务内
  与事件 purge 一起提交/回滚。这样首次失败后 media fallback 仍能重新选中 session；
- 共享 `WindowState` 在默认（server stopped）模式下只全表扫描一次 video/window
  events；`--allow-running-server` 下每个 session 重建状态以降低过期风险；
- 事件 purge 主事务持有写锁；server 的 event 插入/状态更新会等待。单 session 事务
  同时做 events 删除、FTS 重建、day_summary/day_stats 清理，可能超过 server 端的
  `busy_timeout=5000`；server 写入失败会记日志并在下一轮重试。**v1 契约是
  stop → reset → start**，既避免派生面竞态也避免长时间占锁；
- 主事务提交后 server 可能立即重分析该 session；这是期望行为；
- 状态重置可能与在途分析互相覆盖：若 server 在主事务后完成并写回 `ok`，该文件
  本轮不会再次进入 pending。`--allow-running-server` 下接受该竞态并在输出 warning。
- chat 文件同理。
- 与 `POST /api/events/remove` 并发：两端都是写事务，SQLite 会串行化，但语义会交错
  （remove 按 video ref 删并级联，reset 按 session 全量 purge）。v1 默认前置已排除
  该场景；`--allow-running-server` 下不要与 remove 并发执行。

### D12. 安全交互

- `--dry-run`：只打印计划，不 unlink、不 UPDATE、不 DELETE；
- 实际执行需要 `--yes`；否则打印计划并退出（防误删）；
- **server 运行保护**：实际执行（`--yes` 且非 `--dry-run`）前做三态探测；
  running/unknown 都拒绝（exit 1）；只有明确 connection refused 才放行。401 等
  HTTP 响应同样视为 running。`--dry-run` 只读，不受该前置限制；`--allow-running-server`
  覆盖时 text 输出 WARNING、JSON 输出 `warnings`/`server_status`；
- `--json`：成功时输出计划/结果；失败时统一输出
  `{"error": {"code", "message", ...}}`。错误码：
  `invalid_args`（argparse 缺参/未知参数，exit 2）/ `config_error` /
  `invalid_date` / `database_unavailable` / `database_not_found` /
  `schema_error` / `plan_failed` / `server_running` /
  `server_status_unknown` / `reset_failed`；`--json` 下这些路径不再混用纯文本；
- plan 与执行汇总必须包含：删除候选文件数与字节数、将 purge 的 events 计数
  （按 type）、**out-of-range 文件/事件计数**（session 放大可见）、未知 type 保留数；
- **video 警告**：删除无事件非关键帧后，窗口重组/关键帧选择可能变化，重分析不是
  等价重跑（D3/§6.4）；
- 无备份；删除路径不逐条打印到 stdout（量大），只打印计数、字节数与失败路径；
- 退出码：`0` 完全成功/空操作；`1` 运行时错误（DB 缺失、锁超时、server 状态非
  stopped 且未覆盖、FTS schema 过旧）**或部分完成**（`sessions_failed` /
  `failed_unlinks` / `vectors_pending` 任一非空）；`2` 参数/日期非法或未带 `--yes`。
- plan/dry-run/未确认/日期非法/拒绝执行路径均为**只读**：不建表、不迁移、不切换
  WAL；`_ensure_schema` 仅在实际执行且 server 前置检查通过后调用。

### D13. 源文件缺失时的策略

reset 是破坏性“重跑分析”：

- 若某个将被 purge 的 event 的源文件/源 chat 行已不存在，重分析无法再生成它；
- v1 仍然 purge（否则会残留，违背“避免残留”目标），但在 dry-run/执行输出中报告
  `missing_source_events` 计数与 warning（best-effort：audio/video 可按 files 行 +
  物理文件精确判定；chat 可按 chat 文件判定；window/agent_job 属于派生事件，
  只做提示）；
- 不做备份；用户需自行确认可接受。后续可加 `--keep-missing-source-events`。

### D14. derived 审计 JSON

- 目标 session 的 `derived/<device>/<rsession>/audio/*.json`、
  `video/windows/*.json`、`video/frames/*.json` **删除**；
- 理由：它们是分析审计产物，不是查询数据源（`docs/data/lvdata_mcp_design.md`）；
  重分析会重新写入。保留会留下旧 `window_id` 审计文件（“残留”）。
- 对被删 window 事件位于其它 session 的情况，在该 session 主事务内、**提交前**
  删除对应 `derived/<device>/<session>/video/windows/<window_id>.json`（尽力而为；
  避免提交后崩溃留下无法从已删 window event 重建的旧 JSON）；
- 路径必须位于 `<root>/derived/` 下；失败只 warning。
- 这是对 video prune“保留 derived”先例的有意扩展；如果希望保留人工核查材料，
  后续可加 `--keep-derived`。

### D15. 跨 session window 的边界

- 窗口按 device 跨 session；事件 purge 用“无剩余 video 事件才删 window 事件”的
  归零规则（§2.8、§2.10）；
- 若窗口仍有 video 事件在 scope 外，保留 window 事件；其 `frame_count` /
  `window_caption` 可能不包含本次删除/重分析的帧（stale），输出 warning；
- 不把 scope 自动扩展到整个窗口的全部 session（会违背日期/session 语义）。

---

## 4. CLI 契约

### 4.1 用法

```bash
lvdata-mcp [--config PATH] reset --date <spec> [--dry-run] [--json] [--yes]
          [--allow-running-server]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--date` | 是 | `YYYY` / `YYYY-MM` / `YYYY-MM-DD`；严格校验 |
| `--dry-run` | 否 | 只输出计划，零写入 |
| `--json` | 否 | 机器可读计划/结果（命中原因、out-of-range 计数、warning） |
| `--yes` | 否 | 实际执行删除/重置；未带时只打印计划并退出 2 |
| `--allow-running-server` | 否 | 实际执行前探测 `/healthz`，server 运行中则拒绝；显式携带才继续（`--dry-run` 不受限；接受 D11/§7.5 派生面 stale 风险） |
| `--config` | 否 | 既有全局参数，位于子命令之前 |

### 4.2 输出

`--dry-run` 示例：

```
reset plan: date=2026-09-28 timezone=Asia/Shanghai
  date window: 2026-09-28 .. 2026-09-28
  sessions: 3 (primary=2 fallback=1)
  session hits:
    - rs_abc... session_date=2026-09-28 primary
    - rs_def... session_date=''      media
    - rs_ghi... session_date=''      events
  files in scope: audio=120 video=800 chat=6 meta=3
  out of range (session amplification): files=40 events=120
  delete candidates: audio=40 (12.3 MB) video=650 (80.1 MB)
  events to purge: chat=120 transcribe=30 translate=30 agent_job=2
                   audio=80 video=150 window=20 (after zeroing 18)
  unknown event types (kept): 2
  fts docs: 432 (sessions=3)
  vector chunks: 432 (if embedding configured)
  day_summary affected: 4 day/timezone rows (all -> pending)
  reset: audio=80 video=150 chat=6
  missing_source_events: 2 (cannot be regenerated)
  warnings:
    - audio/video files with no event and status ok will be deleted (no backup)
    - video non-key frames will be deleted: re-analysis is NOT an equivalent
      re-run (window regrouping / key-frame selection may change)
    - chat/meta source files are never deleted
    - known events and derived audit JSON for the selected sessions will be purged
    - unknown event types are kept and will not be regenerated
    - video window events with remaining video events outside scope are kept
```

实际执行输出同样结构，另加 `files_deleted=... bytes=... files_unlinked=...
bytes_unlinked=... events_deleted=... fts_rebuilt=... vectors_removed=...
vectors_swept=... day_summary_reset=... day_summary_deleted=...
failed_sessions=... failed_unlinks=... vectors_pending=... partial=...`。
`files_unlinked`/`bytes_unlinked` 统计本轮实际删除的物理文件（含随后被
defer 的 session），`files_deleted`/`bytes` 统计已提交的 files 行删除。
`--json` 输出同字段的机器可读版本。

### 4.3 日期校验

```python
^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$
```

- `YYYY`：年份 0001–9999；
- `YYYY-MM`：月份 01–12；
- `YYYY-MM-DD`：真实日期（含闰年）；
- 不接受 `2026-2-3`、`2026-13`、`2026-02-30`、空串、`all`、`from/to`。
- `9999-12-31`（及 `9999`）是合法边界：`timeutil.day_bounds_ms` 无法构造次日
  零点，改用日首 + 24h 名义边界；**不截短查询窗口**，负偏移时区（如
  America/New_York）本地日末、对应 UTC year 10000 的事件仍会被选中。
- `timeutil` 的边界归组/渲染：
  - `safe_local_date` 按该时区的 `day_bounds_ms` 识别最小/最大本地日窗口；
    窗口内（即使 UTC 侧落在 year 0/10000）分别返回 `0001-01-01` /
    `9999-12-31`（例如 `Asia/Shanghai`、`+14:00` 的本地最小日前几个小时）；
  - 合法 epoch 但本地日期为 year 0/10000（不属于该时区的边界日）→
    `LocalDateOutOfRange`；超出 ±24h 边界窗口余量（覆盖历史 LMT）的损坏
    时间戳 → ValueError；
  - `ms_to_local` / `ms_to_local_hhmm` 对窗口内时刻用“本地日首 + 毫秒差”
    构造显示时间，避免 `fromtimestamp` 在 UTC 年界溢出。
- reset 的**目标时区**（`watcher.timezone`）必须能表示事件日期，否则计划/执行
  在 unlink 前失败；其它时区按 `LocalDateOutOfRange` 跳过（它们没有对应日卡行）。

---

## 5. 执行算法

### 5.1 伪代码

```
cfg = load_config(validate_providers=False)  # reset 不调 LLM，不要求 provider key
spec = parse_date_spec(args.date)            # 最先校验；非法日期不打开数据库

# ── plan 阶段：只读连接；不建表、不迁移、不切 WAL ──────────────────────────
conn_ro = open_db(cfg.data.resolved_db_path, readonly=True)
check_tables_exist(conn_ro)                  # 缺表 -> exit 1（不自动建表）
check_fts_schema(conn_ro)                    # search_docs 旧 schema -> exit 1
reset_sessions = select_sessions(conn_ro, spec, start_ms, end_ms)
data = load_session_data(conn_ro, reset_sessions)
plan = build_plan(cfg, conn_ro, spec, data, start_ms, end_ms)
else: close conn_ro

print_plan(plan, as_json=args.json)
if args.dry_run: return 0
if not args.yes: print("re-run with --yes"); return 2

# ── 实际执行：先探测 server，再迁移 schema，再写 ─────────────────────────
status = server_status(cfg)                  # running / stopped / unknown
if status != "stopped" and not args.allow_running_server: return 1
if status != "stopped": warn_stale_risk(status)   # text stderr / JSON warnings
_ensure_schema(cfg)                          # 仅此路径允许建表/迁移/WAL
conn = open_db(cfg.data.resolved_db_path)    # WAL, busy_timeout=5000
reset_sessions = select_sessions(conn, cfg, spec)
plan = build_plan(conn, reset_sessions)      # --yes 路径的 plan 在迁移后构建
print_plan(plan, as_json=args.json)

# 空 scope：仍执行一次本地向量 sweep（不存在的本地 DB 不建文件）
if not reset_sessions:
    pending, swept = sweep_orphans(create_schema=False)
    return 1 if pending else 0

window_state = build_window_state(conn) if needs_window_state else WindowState()

for sid in reset_sessions:
    if args.allow_running_server:
        window_state = build_window_state(conn)      # 降低在途事件导致的过期风险

    # 1) unlink 无事件媒体（事务前）；files 行留给主事务
    file_ids, failed_ids, n_unlinked, bytes_unlinked = \
        unlink_no_event_media(conn, sid)
    result.files_unlinked += n_unlinked
    result.bytes_unlinked += bytes_unlinked
    if failed_ids:
        result.sessions_deferred.append(sid)         # 主事务整体延后，行保持原状
        continue

    # 2) 旧 derived 审计删除（事务前）
    delete_derived_for_session(sid)

    # 3) 主事务：已知事件 purge + files 行 + 派生面 + 状态
    BEGIN IMMEDIATE
    deleted = purge_events(conn, sid, window_state)  # 未知 type 保留
    # 跨窗归零删掉的 window event：其 scope 外 session 的审计 JSON 在提交前删除
    delete_cross_session_window_audits(deleted.cross_window_audits)
    delete_files_rows(file_ids)                      # 与 purge 同事务
    rebuild_fts(conn, deleted.affected_sessions)     # 缺 session 用 device_uuid
    delete_index_state(conn, sid)
    reset_day_summary_and_stats(conn, deleted.ts_list)   # 所有时区 pending
    reset_files(conn, sid)
    COMMIT

    # 4) 事务后：向量删除（本地 DB 不依赖 key；内部按批删除）
    remove_vectors(deleted.refs)

# 5) 全量兜底：无条件执行；remove 失败/scope 为空更需要孤儿清扫。
#    本地 DB 不存在时直接返回 0，不创建零字节 lvdata_index.db。
if vec is not None:
    pending, swept = sweep_orphans(create_schema=False)
    result.vectors_swept += swept
    result.vectors_pending |= pending

print_summary()
return 1 if sessions_failed or failed_unlinks or vectors_pending else 0
```

### 5.2 选 session SQL

```sql
-- 主选（session_date 在日期范围内）
SELECT rsession_id
FROM recording_sessions
WHERE session_date != '' AND session_date BETWEEN :start_date AND :end_date;

-- 回退 1（仅 session_date='' 或 session 行缺失）：该 session 有媒体文件落在时间窗内
SELECT DISTINCT f.rsession_id
FROM files f
LEFT JOIN recording_sessions s ON s.rsession_id = f.rsession_id
WHERE COALESCE(s.session_date, '') = ''
  AND f.file_type IN ('audio','video')
  AND f.uuid_ts_ms BETWEEN :start_ms AND :end_ms;

-- 回退 2（仅 session_date='' 或 session 行缺失）：该 session 有 events 落在时间窗内
SELECT DISTINCT e.rsession_id
FROM events e
LEFT JOIN recording_sessions s ON s.rsession_id = e.rsession_id
WHERE COALESCE(s.session_date, '') = ''
  AND e.ts_ms BETWEEN :start_ms AND :end_ms;
```

三段取并集，写入临时表（同时记录每个 session 的命中原因，供 plan 输出）：

```sql
CREATE TEMP TABLE reset_sessions (rsession_id TEXT PRIMARY KEY);
INSERT OR IGNORE INTO reset_sessions VALUES (...);
```

后续 files/events 查询 JOIN `reset_sessions`，避免 `IN (...)` 变量数上限。

### 5.3 事件 purge

对每个 `sid`：

```sql
-- 1) 收集本 session 事件（用于 refs/ts/window_id）
SELECT event_id, event_ref, type, ts_ms, payload
FROM events WHERE rsession_id = :sid;

-- 1b) 未知类型（保留，仅告警）
SELECT type, COUNT(*) FROM events WHERE rsession_id = :sid
  AND type NOT IN ('chat','transcribe','translate','agent_job',
                   'audio','video','window')
GROUP BY type;

-- 2) 删除已知非 window 事件（未知 type 保留）
DELETE FROM events WHERE rsession_id = :sid
  AND type IN ('chat','transcribe','translate','agent_job','audio','video');
```

Python 侧：

- 对收集到的 `video` 事件，解析 `payload.window_id`；
- 对收集到的 `window` 事件，解析 `payload.window_id`；
- 目标 `window_id` 集 = 两类并集；
- 删已知非 window 后，**一次遍历**全部 video 事件建立 `window_id -> 剩余计数`
  （Python 解析 payload，复用 `remove._payload_field` 语义；与
  `remove._delete_empty_windows` 的 `remaining` 字典同口径，避免逐 id 全表扫描）：

```sql
SELECT event_id, payload FROM events WHERE type='video';
-- Python: counts[_payload_field(payload, "window_id")] += 1
```

- 目标 `window_id` 计数为 0 → 删除该 `window_id` 的全部 window 事件：

```sql
SELECT event_id, event_ref, ts_ms, rsession_id, payload
FROM events WHERE type='window';
-- Python: _payload_field(payload, "window_id") == wid
-- 逐行 DELETE FROM events WHERE event_id=?
```

- 有剩余 → 保留，记 warning（跨 session 窗口仍有 video 事件）。
- 目标 session 内 `payload` 缺失/无法解析 `window_id` 的 window 事件：删除 + warning。

`deleted.refs` = 被删已知非 window 的 refs + 被删 window 的 refs（未知 type 不
计入）；`deleted.ts_list` = 对应 `ts_ms`；
`deleted.affected_sessions` = `{sid} ∪ {被删 window 事件的 rsession_id}`。

### 5.4 FTS 重建与 index_state

```python
check_fts_schema(conn)      # 见下：不存在 -> ensure_index；旧 schema -> 中止
index.ensure_index(conn)    # 仅当表不存在时创建（幂等）
for s in deleted.affected_sessions:
    n = index.rebuild_session(conn, store, s)   # DELETE search_docs WHERE session_id=s
                                                # + 从剩余 events 重建
    if n == 0 and session_row_missing(s):
        conn.execute("DELETE FROM search_docs WHERE session_id=?", (s,))
```

- **schema 前置检查（重要）**：`index.ensure_index` 在 `search_docs` 存在但缺
  `orig_text` 时会 **DROP 整表**（`index.py:78-85`）。若 reset 直接调用它，会把
  **全库** FTS 清空，而不只是目标 session。因此：
  - 表不存在 → 调用 `ensure_index` 创建；
  - 表存在且含 `orig_text` / `session_id` 等 rebuild 必需列 → 正常；
  - 表存在但缺列 → **中止 reset**，提示先运行 `lvdata-mcp reindex`
    （或显式全量重建）；不得静默 drop。
- `index.rebuild_session` 会先删该 session 的 FTS 行，再按剩余 events 重建
  （`index.py:88-114`）；被 purge 的事件不会复活。
- `recording_sessions` 行缺失时不能直接清空该 session 的 search_docs（会误删保留
  的未知事件索引）。实现改为用 `events.device_uuid` 对剩余事件做完整重建
  （`reset.py:_rebuild_fts`）；无剩余事件时才删除 docs。
- 删除目标 session 的 `index_state` 行（`DELETE FROM index_state WHERE target=?`），
  使重分析后 `lag_sessions` 必然重建（`index.py:147-163`）。
- 其它受影响 session（仅删了 window 事件）保留原水位；FTS 已重建，无需强制。

### 5.5 向量删除

- 收集 `deleted.refs`，按 500 个一批调用
  `vec_index.remove_refs(batch, create_schema=False)`；`remove_refs`/`sweep_orphans`
  内部再按 500 条切分 `DELETE ... IN (...)`，避免孤儿数超过 SQLite
  `MAX_VARIABLE_NUMBER` 时清理永久失败、重跑无法收敛（review4 issue 1）；
- `remove_refs` 同时删 `vec_chunks` 与 `vec_state`；
- 未配置 embedding key 但磁盘上存在 `lvdata_index.db`：用本地 dim 构造 VecIndex
  仅做 SQL 删除（`remove_refs`/`sweep_orphans` 不调用 embed），不再跳过清理；
  无本地 vec DB 才跳过；
- 本地清理使用 `create_schema=False`：`vec_chunks`/`vec_state` 不存在时直接跳过，
  **绝不按猜测维度建表**；`sweep_orphans` 在向量库文件不存在时直接返回 0，
  不会为一次空 sweep 创建零字节 `lvdata_index.db`；
- 任一批异常 → log warning + `vectors_pending=True`；继续；
- **无论 remove 是否失败、scope 是否为空**，最终都执行
  `sweep_orphans(create_schema=False)`；返回的去重 orphan ref 数计入
  `vectors_swept`；sweep 失败同样 `partial=true`，退出码 1；
- embedding 响应保护（本路径触及的共享向量模块）：`DashScopeEmbeddingProvider.embed`
  每批校验 `len(resp.data) == len(batch)` 且 `index` 恰为 `0..n-1` 的排列（乱序按
  index 排序）；`rebuild_session` / `sync_changed` 在删除旧 chunk、写 `vec_state`
  前再次复核向量数，不匹配抛错并回滚（review6 issue 1）。

### 5.6 day_summary / day_stats

对 `deleted.ts_list`：

```python
tzs = [r[0] for r in conn.execute("SELECT DISTINCT timezone FROM day_summary")]
for ts in deleted.ts_list:
    safe_local_date(ts, cfg.watcher.timezone)   # 目标时区严格校验，越界即失败
for tz in tzs:
    days = []
    for ts in deleted.ts_list:
        try:
            days.append(safe_local_date(ts, tz))   # 最小/最大本地日窗口安全
        except LocalDateOutOfRange:
            continue                               # 该时区无对应日卡行
    days = sorted(set(days))
    for d in days:
        conn.execute("""
            UPDATE day_summary
            SET status='pending', generated_at=NULL, fail_count=0, error=NULL,
                model=NULL, day_summary='{}', event_count=0, day_stats='{}'
            WHERE timezone=? AND local_date=?
        """, (tz, d))
        conn.execute("DELETE FROM day_deferred WHERE timezone=? AND local_date=?",
                     (tz, d))
    day_stats.recompute_days(conn, tz, days, now_iso())   # 所有时区
```

- 所有时区（含非活跃）统一置 pending；`recompute_days` 对非活跃时区也执行（纯 SQL）；
- 目标时区先严格校验所有事件日期；其它时区本地日期越界（year 0/10000）时跳过，
  因为它们不可能有该日的 `day_summary` 行；
- `safe_local_date` 能识别**最小日与最大日**的本地日窗口；`recompute_days` 使用
  修复后的 `day_bounds_ms`（最大日 = 日首 + 24h），因此 `0001-01-01` 和
  `9999-12-31` 都可实际执行，正/负偏移时区均不会在相邻日计算时溢出；
- `DayStats.recompute_days`（`summary/day_stats.py:252-287`）：
  - 有剩余事件 → `UPDATE day_stats`；
  - 无事件且 `status != 'ok'` → 删除该行（我们刚置 pending）；
  - 无行 → 跳过。
- `day_summary` doc 清空后，BFF/MCP 不会读到旧摘要；活跃时区的 pending 行由
  `_check_pending_retry` 重新生成（`summary/day.py:345-358`）。
- 若该日仍有 scope 外的剩余事件 → 行保持 pending，discover 会从剩余事件重新生成；
  若没有任何剩余事件 → 行被 `recompute_days` 删除；若之后重分析产生新事件，
  discover 会新建该行。
- **非活跃时区**：pending 行保留；该时区再次变为活跃时，`_check_pending_retry`
  会立即重试，不依赖 `day_scan_state` 是否已越过旧事件。再激活前显示空/缺失日卡
  是预期的过渡态。
- 置 pending 可能让 flush 组在重分析前先按剩余事件生成一次日摘要（额外一次 LLM
  调用），重分析后还会再生成；这是正确性优先的取舍（§7.7）。

### 5.7 文件删除与状态重置

**无事件媒体删除**（unlink 在事务前，files 行在主事务内）：

- 条件：`file_type IN ('audio','video') AND analysis_status='ok'`，且 event_ref 不存在；
- `unlink(missing_ok=True)`，收集 `file_id`；**不在此处删 files 行**；
- 主事务内按 `file_id` 删除行，与事件 purge/files 状态一起提交或回滚；
- 任一 unlink 失败 → **该 session 主事务整体延后**：events/files 行保持原状，
  下一轮仍能通过 primary/media/events 任意入口选中；已 unlink 的文件由
  `missing_ok` 自愈；本次计 `failed_unlinks` / `sessions_deferred`，`partial=true`、退出码 1；
  本轮已成功删除的物理文件仍计入 `files_unlinked`/`bytes_unlinked`（审计不低估
  磁盘变化），但对应 files 行未提交，故不计入 `files_deleted`；
- 路径护栏；路径越 root 跳过（行按普通保留文件重置）。

**保留文件重置**（事务内）：

```sql
-- audio/video
UPDATE files SET analysis_status='pending', fail_count=0, error=NULL, analyzed_at=NULL
WHERE file_id=?;
-- chat_*
UPDATE files SET processed_offset=0, processed_updated_at=NULL, fail_count=0,
                 analysis_status='na', error=NULL
WHERE file_id=?;
```

`meta` 跳过。

### 5.8 derived 审计 JSON

每个目标 session 的**主事务前**删除：

```
derived/<device_uuid>/<rsession_id>/audio/*.json
derived/<device_uuid>/<rsession_id>/video/windows/*.json
derived/<device_uuid>/<rsession_id>/video/frames/*.json
```

- `device_uuid`/`rsession_id` 来自 `recording_sessions`；session 行缺失时用
  `files.device_uuid` 兜底，仍无法定位则 warning 跳过（这些 JSON 会成为孤儿）；
- 路径必须在 `<root>/derived/` 下；
- 先删旧 audit，再 purge/重分析，避免重分析写入的新 audit 被随后的清理删掉；
- 对 scope 外 session 的被删 window 事件，在**主事务内、提交前**删除对应
  `.../video/windows/<window_id>.json`（尽力而为；这些 session 不会被重分析，
  所以不会误删新 audit；崩溃重跑不会留下不可重建的旧 JSON）；
- 失败只 warning（不影响事件/索引一致性）。

### 5.9 执行后验证

```sql
-- 目标 session 的已知 events 应为 0（仅剩跨 session 保留的 window 事件与未知 type）
SELECT COUNT(*) FROM events e JOIN reset_sessions s ON s.rsession_id=e.rsession_id
WHERE e.type IN ('chat','transcribe','translate','agent_job','audio','video');

-- 目标 session 的 FTS 行应为重建后的剩余事件数
SELECT COUNT(*) FROM search_docs d JOIN reset_sessions s ON d.session_id=s.rsession_id;

-- 目标 session 的 files 应为 pending/na，删除候选行已消失
SELECT file_type, analysis_status, COUNT(*) FROM files f
JOIN reset_sessions s ON s.rsession_id=f.rsession_id
GROUP BY file_type, analysis_status;

-- 受影响日：pending 或已删除（无事件）
SELECT timezone, local_date, status, event_count FROM day_summary
WHERE status='pending';
```

reset 后启动 server（stop→reset→start），首轮 poll/round 及后续轮次：

- `mcp_pipeline_runs` 出现 `chatlog` / `audio` / `video` run；
- `analysis_runs` 新增对应文件的调用记录；
- events 重新生成，`updated_at` 刷新；
- FTS/向量/day_summary/day_stats 由正常流水线跟进（§6）。

不要求、也不建议在 reset 后手动调用 `POST /api/analysis/trigger`。

---

## 6. 派生数据与后续链路

### 6.1 事件 purge 后

- 目标 session 的 `chat / transcribe / translate / agent_job / audio / video` 事件
  被删除；清掉对应 FTS 行与向量行；未知 type 事件保留（无重建路径）；
- window 事件按“无剩余 video 事件”归零删除；仍有跨 session video 事件的窗口保留；
- `day_summary` 所有时区受影响日置 pending 并清空 doc；`day_stats` 对所有时区
  立即按剩余事件重算；
- `day_deferred` 受影响日清空；
- `index_state` 目标 session 清空，重分析后 FTS 必然重建。

### 6.2 重分析后的传播

- media event upsert → `events.updated_at` 刷新（`upsert_event` 无条件刷新，即使
  内容相同；`analysis/common.py:106-124`）→
  - `reindex` stage 的 `lag_sessions` 判定该会话滞后 → FTS 重建
    （`index.py:147-163`）；
  - flush 门控（`events.updated_at > flush_state.last_flush_at`）触发 `embed` →
    `vec.sync_changed` 重嵌（`watcher.py:230-256`、`vec.py:240-288`）；
  - `day_summary` 的 discover 看到 `updated_at > generated_at` → 重生成；
  - `day_stats` 游标看到 `updated_at > last_scan_at` → 重算日卡派生字段。
- chat 重放使用 `upsert_event_if_changed`：内容未变不刷 `updated_at`；但 reset 已
  删除事件，重放是 INSERT（不是 conflict），会以当前 `updated_at` 重新入库，
  因此 day_summary 会重新生成。
- 若重分析不再产出事件（audio 静音、video 无关键帧），事件不会复活；这是 reset 的
  预期结果（旧事件已 purge）。

### 6.3 删除的无事件文件

删除的 audio/video 文件本来就没有 event，且其事件面已按 session purge；因此：

- 不留下 FTS/向量/事件行；
- 不影响 `get_event`（没有事件可查）；
- 不删源 chat/meta；
- derived 审计 JSON 按 D14 清理（目标 session）。

### 6.4 video 窗口重新分组

删除“无 video event”的帧后，剩余帧（关键帧）在下一轮重新分组：

- **重要：这不是原分析的等价重跑**。非关键帧虽然无事件，但仍是 window filter
  的输入；删除后窗口组成、关键帧选择、caption/extract 结果都可能变化（D3）；
- 旧 window/video 事件已在 reset 中 purge（跨 session 保留的 window 事件除外，见 D15）；
- 新 window 事件会生成，`window_id` 可能变化；
- 若窗口仍有 scope 外 video 事件，旧 window 事件保留（D15 边界），其
  `frame_count/window_caption` 可能不包含本次删除/重分析的帧。

### 6.5 BFF / MCP 的可见性窗口

reset 执行到重分析完成之间：

- 目标 session 的 events 消失；`search`/`semantic_search`/`get_timeline`/
  `get_recent_events` 看不到这些事件；
- `day_summary` 所有时区受影响日 pending、doc 清空；日卡可能显示 pending/缺失
  （非活跃时区直到再次激活）；
- FTS/向量已清理，不会返回已删事件；
- 重分析完成后逐步恢复。

这是“删除后重建”的预期空窗；`--dry-run` 可先评估范围。

---

## 7. 风险、限制与取舍

### 7.1 无备份、事件不可恢复

reset 删除物理文件与 events，且清空 day_summary doc；没有备份/回收站。若重分析
失败或源文件缺失，旧分析结果不可恢复。防线是 `--dry-run` + `--json` 计划、
`--yes` 显式确认，以及默认的 server 未运行前置（D11/D12）。

### 7.2 源文件缺失

若被 purge 的 event 对应源文件/源 chat 行已不存在，重分析无法再生成该事件。
v1 仍 purge（避免残留），dry-run 报告 `missing_source_events` 并 warning（D13）。
这对 audio/video（物理文件）与 chat（jsonl 行）都可能发生。

### 7.3 跨 session video 窗口

- 窗口按 device 跨 session；reset 按 session 选择，可能只覆盖窗口的一部分帧；
- 事件 purge 用“无剩余 video 事件才删 window 事件”的归零规则，避免孤儿 window；
- 若窗口仍有 scope 外 video 事件，window 事件保留，但 payload 可能 stale；
- 不自动扩展 scope 到整个窗口（会超出日期/session 语义）。输出 warning。

### 7.4 session 粒度

按 `session_date` 命中即整 session reset，可能包含日期范围外的文件/事件
（session 跨午夜、session_date 异常等）。这是为了让 video 窗口/chat 文件保持原子性；
需要精确文件日期切片时，本命令不适用。

fallback 只对 `session_date=''` / session 行缺失的 session 启用（D2），因此
`session_date` 非空且在范围外的 session **不会**因单条漂移事件被拖入 reset；
代价是 `session_date` 错误但非空的 session 不会被回退命中，需按其 `session_date`
对应日期执行（或等待后续的精确 scope）。fallback 命中的 session 仍是整 session
reset，plan 必须展示 `out_of_range_files/events` 计数与每个 session 的命中原因
（§4.2），供 `--yes` 前人工判断放大范围。

### 7.5 运行中 server 的竞态

- **v1 契约：先 stop server，再 reset，再 start**。CLI 默认三态探测：running /
  unknown 均拒绝（exit 1），只有明确 connection refused 放行；只有
  `--allow-running-server` 显式覆盖并打印 warning；
- 为什么必须是硬前置：除了文件/分析状态竞态，`DaySummary._generate_one` 和
  `DayStats._run_day` 都会“LLM/计算前读、之后无条件写回”（`summary/day.py:439-534`、
  `summary/day_stats.py:214-250`）。这些写落在 reset 之后会把已清的 doc/stats
  恢复为旧值；若重分析不再产事件，`discover` 不清理 ok 行
  （`summary/day.py:230-243`），日卡会永久 stale。见 D11；
- 事件 purge 主事务持有写锁；server 写入会等待。单 session 主事务同时做 events
  删除、FTS 重建、day_summary/day_stats 清理，可能超过 server 的
  `busy_timeout=5000`，导致 server 写入失败并在下一轮重试；
- 主事务提交后 server 可能立即重分析；这是期望行为；
- `--allow-running-server` 下：在途分析可能在主事务后写回 `ok`/新事件，该文件本轮
  不会再次进入 pending；chat 同理；输出必须包含 warning 与 stale 风险说明。

### 7.6 `inventory --force` 复活

删除 files 行后，`stage inventory --force` 会从 `cs.recording_files` 全量重放，把
被删文件重新插入为 `pending`（`recording_watch.py:158-161,213-220`），随后分析因
物理文件缺失失败。与 video prune 同口径接受（`docs/data_clean.md` D6）。日常同步
不会复活：keyset 水位已过，audio/video 的 cs 行不会被 touch。

### 7.7 day_summary 的额外 LLM 成本

- 事件 purge 后活跃时区受影响日置 pending、doc 清空；flush 门控可能在重分析完成前
  先按剩余事件生成一次日摘要（媒体分析按预算分摊到多轮时尤其如此）；
- 重分析后 `updated_at` 再次触发重生成；
- 因此单日可能多一次 LLM 调用。大范围（`YYYY`）reset 成本需用 `--dry-run` 评估。
- v1 默认 stop→reset→start 已消除“reset 过程中生成”的中间态，但 start 后首轮
  flush 仍可能在全部重分析完成前触发；reset 仍会置 pending。

### 7.8 derived 审计 JSON 删除

- D14 删除目标 session 的 derived 审计 JSON；这是审计产物，不是查询源；
- 若保留，旧 `window_id`/旧分析结果会作为文件残留；与“避免残留”冲突；
- 对 scope 外 session 的被删 window 事件，只尽力删除对应 window JSON；frame JSON
  可能残留（边界）。

### 7.9 chat / meta 不删除

- chat jsonl 是原始对话数据；meta 是 session.json 元数据；
- 删除会丢失源数据/破坏建档；本命令只清 chat replay 水位；
- chat 事件会被 purge 并由 chatlog 重放重建；若 chat 文件本身缺失，事件不可恢复
  （§7.2）。

### 7.10 事件 purge 是默认行为

- 用户要求“删除对应的 events 避免残留”，因此 v1 默认 purge **已知类型**；
- 未知 type 保留 + warning：本服务没有重建路径，删除等于不可恢复的数据丢失；
  需要清理时另设 `--purge-unknown-types`（未实现）；
- 若只想重跑分析、保留旧事件（例如仅更新部分事件），不应使用本命令；
- 若需要“保留 events、仅重置文件”，可另设 `--keep-events`（未实现）。

---

## 8. 测试计划

### 8.1 日期解析

- `2026` → `2026-01-01 .. 2026-12-31`；
- `2026-09` → `2026-09-01 .. 2026-09-30`；
- `2026-09-28` → 单日；
- 闰年/月末；
- 非法：`2026-2-3`、`2026-13`、`2026-02-30`、`all`、空；
- 时区边界（`Asia/Shanghai`）。

### 8.2 作用域

- `session_date` 命中（日/月/年）；
- `session_date=''` / session 行缺失时由 media/events 回退命中；
- `session_date` 非空且在范围外、但 events 在范围内的 session **不**入选
  （fallback 仅空 session_date）；
- 无媒体/无事件的 session 不进 scope；
- 跨午夜 session 整 session 进 scope；
- plan 的命中原因与 `out_of_range_files/events` 计数正确；
- 临时表去重。

### 8.3 文件分类

- audio：ok+event → reset；ok+no event → delete；failed/pending → reset；
- video：ok+video event → reset；ok+no event → delete；failed/pending → reset；
- chat：always reset（不删）；
- meta：跳过；
- unknown：warning；
- 空摘要/静音 audio、零关键帧窗口的 video。

### 8.4 事件 purge

- 目标 session 的 chat/transcribe/translate/agent_job/audio/video 全部删除；
- **未知 type 保留 + warning**（不计入 `deleted.refs/ts_list`）；
- window 事件归零删除；
- window 仍有 scope 外 video 事件 → 保留 + warning；
- 被删 window 事件所在其它 session 的 FTS 重建；
- `deleted.refs`/`ts_list` 正确收集。

### 8.5 派生面

- `search_docs` 中目标 session 行被删除并重建；
- `index_state` 目标 session 行删除；
- `vec_chunks`/`vec_state` 被删 refs 清除；`sweep_orphans` 兜底；
- 活跃时区：`day_summary` 受影响日 pending、doc 清空、`day_deferred` 清空；
- 非活跃时区：`day_summary` 受影响日置 **pending**、`day_deferred` 清空；该 tz
  再激活时由 `_check_pending_retry` 立即重试；
- `day_stats` 所有时区有剩余事件时重算，无事件时行删除；
- 其它日不受影响。

### 8.6 状态重置与取件

- media 全部 pending/fail_count=0/error=NULL/analyzed_at=NULL；
- chat offset=0/watermark=NULL/fail_count=0/status=na/error=NULL；
- meta 不变；
- 重复执行幂等；
- reset 后 `select_pending_files('audio')` / `('video')` 能取到行；
- reset 后 chatlog 的 `_SELECT_SQL` 能取到 chat 行。

### 8.7 删除文件

- 文件存在 → unlink + 删除 files 行；
- 文件缺失 → `missing_ok` + 删除行；
- 路径越 root → 跳过 + warning；
- unlink 失败 → 该 session 主事务延后（events/files 行保持原状），下一轮重试；
  本次 `failed_unlinks`/`sessions_deferred`，退出码 1；
- fallback-only session + 越界媒体 unlink 失败 → 第二轮仍能通过保留事件选中；
- `cs.recording_files` 不变；
- 删除 files 行不受 `analysis_runs` 影响（无 FK；审计行保留）；
- derived JSON 按 D14 删除；session 行缺失时用 `files.device_uuid` 兜底；
- `--dry-run` 零写入。

### 8.8 崩溃/竞态

- 主事务回滚 → DB 的 events/files 状态/派生面不变；但事务前已删的无事件文件/
  derived 不恢复，重跑 reset 幂等（D10）；
- 主事务提交后崩溃 → 重跑可清；
- 向量删除失败 → `vectors_pending`，**最终 sweep 仍无条件执行**；sweep 失败也 partial；
- 空 scope（本地 vec DB 存在）→ 第二轮仍执行 sweep，孤儿清除；sweep 失败 partial；
- 空 rag DB + 缺失 dim + 无 key → 本地清理不创建 `vec_chunks` schema；
- 跨窗 window event 被删 → 对应 derived JSON 在主事务提交前删除，回滚/崩溃不残留；
- 无事件媒体删除事务内复核：status 从 ok 改为 pending → 跳过；
- event 在计划后出现 → 删除阶段以事务内查询为准；
- `index.ensure_index` 旧 schema 场景 → 预检中止，不 drop 全库 FTS（D7/§5.4）；
- server 探测三态：connection refused → 正常执行；running/unknown 且未带
  `--allow-running-server` → exit 1；带覆盖 → 执行 + text/JSON warning；
  `--dry-run` 在 server 运行时仍可输出计划；
- plan/dry-run/未确认在含旧 `chat_messages` 的库上不建表、不迁移、不切 WAL；
- 媒体 unlink 后主事务失败 → files 行仍在，第二次 reset 仍通过 media fallback
  选中该 session 并完成 purge（不再假成功）；
- unlink 失败 → session 主事务延后、行保持 ok；故障解除后第二次 reset 真正
  删除文件/行并返回 0；
- HTTP_PROXY/ALL_PROXY 指向不可用代理时，本地 health 探测仍为 running（ProxyHandler({})）；
- DB 路径含 `?`/`#`/`%` 时 dry-run 与实际执行（含已有 rag DB、连续两轮）均
  读取正确库，不创建截断路径文件；`DataStore.connect()` 统一用
  `Path.absolute().as_uri()`；
- `0001-01-01` 与 `9999-12-31` 均可 dry-run、实际执行且二次执行返回 0；覆盖
  `Asia/Shanghai` / `+14:00` 的最小日、正/负偏移最大日、已有 day_summary；
- `day_bounds_ms` 不截短最大日查询窗口；`safe_local_date` 识别最小/最大本地日
  窗口；范围外（year 0/10000）在目标时区导致 plan 失败，在非目标时区跳过；
- 最大日 reset 后 scope 外剩余事件（NY 23:30）仍可被 `get_timeline()` /
  `get_event()` 读取，并可强制生成 day summary（`[23:30]` 渲染不溢出）；
- 目标时区负向下溢（`-62135596800001`，UTC）或超出边界余量的时间戳 → plan
  失败、不发生任何 unlink；
- 跨窗 derived 只统计实际存在的文件；物理删除在事务内发生后立即累计，回滚
  仍如实计入 `derived_deleted`；
- day_summary 的跨日敏感测试（debounce/deferred/restart）改用固定上海中午并
  注入 `now_iso`/`datetime.now`，不再依赖 00:0x 的执行时刻；
- 向量孤儿/ref 数超过当前 SQLite `MAX_VARIABLE_NUMBER` 时仍可删除，连续两轮
  sweep 收敛；`remove_refs` 自身也有超限回归测试；
- embedding 返回数量不足或 index 缺失/重复 → `embed()` 抛错；`sync_changed`
  / `rebuild_session` 不推进 `vec_state`，provider 恢复后重跑补齐；
- 首个媒体已 unlink、第二个 resolve/DB 操作失败 → `files_unlinked` /
  `bytes_unlinked` 仍立即累计；重跑收敛；
- `sweep_orphans` 实际删除数计入 `vectors_swept`；defer session 已删除的物理
  文件计入 `files_unlinked`/`bytes_unlinked`；
- 空 scope + 已配置 provider 但本地 vec DB 不存在 → 不创建零字节向量库；
- `reset --json` 缺 `--date` / 未知参数 → `invalid_args` JSON（无 usage 文本）；
- argparse 关闭长参数缩写（`--j` 不等于 `--json`）；`--config` 的值恰好为
  `reset` 时不得给其它子命令启用 reset JSON 错误格式；
- 空 scope dry-run 不扫描 video/window，执行不构建 WindowState；
- `--json` 的 config_error/invalid_date/database_*/schema_error/server_* 错误均为统一 JSON schema；
- 非活跃时区置 pending；插入/激活该时区后 `discover` 能重新入队该日；
- 缺 `recording_sessions` 行但保留未知事件 → 其 FTS 文档用 `events.device_uuid` 重建；
- 无 embedding key 但本地 vec DB 存在 → `vec_state` 中已删 refs 仍被清除；
- remove 失败后最终 sweep 执行且失败时 `partial=true`；
- `failed_unlinks` / `vectors_pending` → 结果 `partial=true` 且退出码 `1`；
- 模拟在途 `day_summary` / `day_stats` 写回（`--allow-running-server` 场景）：
  记录 stale 行为为已接受语义，不做自愈承诺（v1 默认前置消除该场景）；
- `--json` 输出字段与文本 plan 同源、可被脚本解析。

---

## 9. 实施计划

### 9.1 代码

| 文件 | 改动 |
|---|---|
| `src/lvdata_mcp/main.py` | 新增 `reset` 子命令：`--date`（required）、`--dry-run`、`--json`、`--yes`、`--allow-running-server`；直接调用 `run_reset`（schema 迁移由 reset 在实际执行前完成，plan/dry-run 不写库） |
| `src/lvdata_mcp/config.py` | `load_config(..., validate_providers=True)`：reset 传 `False`，不要求 audio/vision/summary api_key；缺失的 provider `dim`/`min_vec_score` 占位符删除并使用字段默认值 |
| `src/lvdata_mcp/reset.py`（新增） | 日期解析、session 选择（fallback 仅 `session_date=''`）、计划（命中原因/out-of-range/JSON）、server 探测、文件删除、已知事件 purge、FTS/向量/day_summary/day_stats 清理、derived 清理 |
| `src/lvdata_mcp/remove.py`（可选重构） | 把 `_delete_empty_windows` / `_recompute_days` 的通用逻辑提为可复用 helper（或 reset 内复刻并加测试） |
| `src/lvdata_mcp/timeutil.py` | `day_bounds_ms` 最小/最大日边界（最大日 24h 名义边界、不截短窗口）；`safe_local_date` / `ms_to_local` / `ms_to_local_hhmm` 按本地日首 + 毫秒差安全归组/渲染 |
| `src/lvdata_mcp/store.py` | `DataStore.connect()` 只读 URI 统一 `Path.absolute().as_uri()`，修复 `?/#/%` 路径截断 |
| `src/lvdata_mcp/vec.py` | `remove_refs`/`sweep_orphans` 按 500 条分批删除；本地 sweep 对不存在的 DB 不创建文件 |
| `tests/test_reset.py`（新增） | §8 测试 |
| `tools/lvdata-mcp/README.md` | 增加 reset 用法、stop→reset→start 前置、三态探测、部分完成退出码、事件 purge 与风险说明 |
| `docs/data/lvdata_mcp_design.md` | 如实施，补充 CLI 清单 |

不修改：

- `watcher.py` / `analysis/*` / `summary/*`：复用现有状态机与取批；
- `ddl.py`：不新增表/列（沿用删除行语义，不做 tombstone）；
- `recording_watch.py`：不写 `cs.recording_files`；
- `server.py`：不新增 REST（reset 是本地 CLI，不是 HTTP 接口；只读探测既有
  `/healthz`）。

### 9.2 复用点

- FTS：`index.ensure_index(conn)`（先做 schema 前置检查）+ 
  `index.rebuild_session(conn, store, session_id)`（`index.py:78-114`）；
- 向量：`VecIndex.remove_refs(refs, create_schema=False)` /
  `sweep_orphans(create_schema=False)`（内部按 SQLite 变量上限分批）；
- day_stats：`DayStats.recompute_days(conn, tz, days, t0)`（`summary/day_stats.py:252-287`）；
- 窗口归零：仿 `remove._delete_empty_windows`（`remove.py:108-145`）；
- 受影响日：仿 `remove._recompute_days`（`remove.py:147-163`）；
- 时间：`timeutil.day_bounds_ms` / `safe_local_date`（最大日边界与安全归组共享）。

### 9.3 实现要点

- plan 用只读连接；不建表/不迁移/不设 WAL；日期校验先于任何 DB 操作；
- 实际执行顺序：三态 server 探测 → `_ensure_schema(cfg)` → 写连接；
- 事件 purge + files 行删除 + FTS + day_summary/stats + files 状态在**同一主事务**；
- 事件 purge 只删已知类型；未知类型仅查询计数并 warning；
- 媒体 unlink 在事务前，files 行在主事务内删除；任一 unlink 失败 → 整个
  session 主事务延后，events/files 行保持原状供下一轮重试；
- 所有时区 day_summary 置 pending（非活跃时区再激活后重试）；
- 共享 `WindowState` 默认只全表扫描一次；allow-running 时按 session 重建；
  空 scope 跳过 FTS/WindowState，但存在向量清理对象时仍执行 sweep；
  无 video/window 的 scope 不构建 WindowState；
- 健康检查用空 ProxyHandler 绕过环境代理；只读 URI 用 `Path.as_uri()` 转义；
- 向量 ref/孤儿删除按 500 条分批，避免超过 SQLite 变量上限；sweep 对不存在的
  本地 DB 不创建零字节文件；
- 汇总增加 `files_unlinked`/`bytes_unlinked`/`vectors_swept`，部分完成时不低估
  物理删除；
- derived 删除在事务外；向量删除在事务后（本地 DB 不依赖 embedding key）；
- 删除候选复核在 `BEGIN IMMEDIATE` 内；路径护栏；
- plan/汇总输出命中原因、out-of-range 计数、未知类型保留数；支持 `--json`；
  `--json` 错误统一 `error{code,message}`；
- 失败路径全部 warning + 继续；`sessions_failed`/`failed_unlinks`/
  `vectors_pending` 任一非空 → 退出码 `1` 并标记 `partial`。

---

## 10. 决策记录与后续

### 10.1 已拍板（v1，按推荐值）

| 决策 | 结论 |
|---|---|
| 作用域 | 按 recording session（D2）；fallback 仅 `session_date=''` / session 行缺失；不做严格文件/日期切片 |
| 事件 purge | 选中 session 的全部**已知**非 window events + window 归零剔除（D6/D7） |
| 未知 event type | 保留 + warning（无重建路径，不默认删除） |
| video 无事件文件 | v1 按文件删除（D3/D4，沿用 video prune）；非关键帧删除会改变重分析输入，plan 强制告警 |
| 源文件缺失事件 | 仍 purge + warning（D13） |
| 跨 session window | 归零规则保留 + warning（D15） |
| derived 审计 JSON | 删除目标 session 的 derived（D14） |
| 运行中 server | **三态探测**：running/unknown 拒绝；明确 refused 才放行；健康检查绕过 HTTP_PROXY；`--allow-running-server` 覆盖并输出 warning（D11/§7.5） |
| day_summary 时区 | **所有时区置 pending**；非活跃时区再激活后由 `_check_pending_retry` 重试（D7/§5.6） |
| 未配置 embedding | 有本地 vec DB 时用 `create_schema=False` 清理（不依赖 key、不建 schema）；无 DB 才跳过（§5.5）；sweep 不创建缺失的 DB 文件 |
| 向量批量删除 | `remove_refs`/`sweep_orphans` 按 500 条分批，超过 SQLite 变量上限也可重跑收敛（§5.5） |
| embedding 响应校验 | provider 校验每批数量与 index 排列；`rebuild_session`/`sync_changed` 写前复核，不匹配回滚且不推进 `vec_state`（§5.5） |
| plan 写入 | plan/dry-run/未确认/拒绝路径只读；schema 迁移仅实际执行前（D12/§5.1） |
| 媒体行事务 | unlink 在前，files 行随 session 主事务提交/回滚（D10）；任一 unlink 失败 → 整个 session 延后，events/files 行保持原状供重试 |
| 缺 session 行 FTS | 用 `events.device_uuid` 重建，保留未知事件索引（§5.4） |
| 部分完成 | `failed_unlinks`/`vectors_pending` 也返回退出码 `1`，结果标记 `partial`（D12） |
| 只读连接 | SQLite `mode=ro` 统一用 `Path.absolute().as_uri()`（reset 与 `DataStore.connect()`），避免 `?/#/%` 截断路径与误建文件 |
| 空 scope | plan/执行均直接返回，不扫描 video/window、不构建 WindowState |
| JSON 错误 | 所有失败路径统一 `{"error":{"code","message",...}}`；unknown 用 `server_status_unknown` |
| 数值占位符 | reset 模式缺失的 provider `dim`/`min_vec_score` 删除并使用默认值 |
| 日期边界 | `day_bounds_ms` 对 `9999-12-31` 使用日首 + 24h 名义边界且**不截短查询窗口**；`safe_local_date` 按本地最小/最大日窗口归组，`ms_to_local`/`ms_to_local_hhmm` 同窗口安全渲染；目标时区越界即失败，其它时区跳过（§4.3） |
| 跨窗 derived 计数 | 只统计实际 unlink 成功的文件；物理删除后立即累计，事务回滚也如实反映 |
| 审计计数 | 结果增加 `files_unlinked`/`bytes_unlinked`/`vectors_swept`；`files_deleted` 仅统计已提交行；物理 unlink 成功后立即累计，异常/回滚不漏计 |
| day_summary 额外 LLM | 接受；v1 默认 stop→reset→start 已消除 reset 期间的中间生成；start 后首轮 flush 仍可能先于全部重分析完成（§7.7） |
| 配置加载 | `load_config(validate_providers=False)`：reset 不要求 provider api_key |
| CLI 入口 | plan 只读；仅实际执行且 server 检查通过后 `_ensure_schema(cfg)` |
| 交互 | `--dry-run` + `--json` + `--yes`（D12） |
| tombstone | 不做；沿用 video prune 先例（§7.6） |
| 时区 | 日期窗固定 `watcher.timezone`，不提供 `--timezone`（D1/D9） |

### 10.2 后续可选（不阻塞 v1）

1. `--keep-events`：只重置文件、不 purge events。
2. `--keep-files`：只 purge events/重置状态，不删除物理文件（与 `--keep-events`
   组合可做“软 reset”）。
3. `--keep-derived`：保留人工核查用 derived 审计 JSON。
4. `--keep-missing-source-events`：源文件缺失的事件跳过/中止而非 purge。
5. `--purge-unknown-types`：显式允许删除未知 event type。
6. `--timezone`：按设备/请求时区 reset。
7. `--all` / `--from..--to`：扩大 reset 范围（会放大删除、FTS 重建与 LLM 成本）。
8. `reset_requests` 表：由 watcher 在 round 开头应用，消除跨进程竞态（包括
   day_summary/day_stats 在途写回）。
9. tombstone：防止 `inventory --force` 复活被删 files 行。
10. window-aware video 删除：只删零关键帧窗口，保留有事件窗口内的非关键帧，
    消除“reset 非等价重跑”的影响（v1 已强制告警）。
11. 跨 session window scope 扩展：把整个窗口涉及的 session 纳入 reset，消除 stale payload。
12. derived 的跨 session frame JSON 清理：目前只尽力清理 window JSON。
13. 严格文件/日期 scope：按 `files.uuid_ts_ms`/`events.ts_ms` 精确切片；需同时解决
    video 窗口部分重组与 chat 整文件重放问题。
14. plan 流式聚合：当前 plan 仍一次性加载目标 session 的 files/events（执行期已
    共享 WindowState、空 scope 跳过扫描）；超大库可改为临时表/流式。

---

## 11. 参考代码锚点

- `recording_watch.py:43-51`（kind → file_type）
- `recording_watch.py:65-75`（files upsert 不覆盖状态）
- `recording_watch.py:143-185`（recording_files keyset 同步）
- `recording_watch.py:213-220`（初始状态与 uuid_ts_ms 规则）
- `recording_watch.py:267`（session_date 映射）
- `analysis/common.py:28-61`（media 取批）
- `analysis/common.py:71-82`（mark ok/failed）
- `analysis/common.py:106-148`（upsert / upsert_if_changed）
- `analysis/audio.py:277-286`（静音判定）
- `analysis/audio.py:568`（audio event_ref）
- `analysis/vision.py:126-160`（窗口分组）
- `analysis/vision.py:466`（window event_ref）
- `analysis/vision.py:596`（video event_ref）
- `analysis/chatlog.py:36-41`（chat 取批）
- `analysis/chatlog.py:246-261`（chat 水位推进）
- `watcher.py:42-44`（round stage 序列）
- `watcher.py:185-226`（round 执行与 flush 门控）
- `watcher.py:341-410`（video prune 删除先例）
- `index.py:88-114`（per-session FTS 重建）
- `index.py:147-163`（FTS 滞后判定）
- `vec.py:240-288`（向量增量/孤儿清扫）
- `vec.py:290-307`（按 ref 删除向量）
- `vec.py:319-346`（全量孤儿清扫）
- `summary/day.py:188-271`（day_summary discover）
- `summary/day.py:230-243`（discover 只清非 ok 的空事件日 → ok 行可永久 stale）
- `summary/day.py:345-358`（pending/failed 重试发现，仅活跃时区）
- `summary/day.py:439-534`（`_generate_one`：LLM 前读、之后无条件写 ok）
- `summary/day_stats.py:184-193`（day_stats 只遍历活跃时区）
- `summary/day_stats.py:214-250`（`_run_day` 先读后写）
- `summary/day_stats.py:252-287`（按日重算 day_stats）
- `summary/timezones.py:13-29`（活跃时区定义，至多 2 个）
- `config.py:117-125,384-387`（provider api_key 必配校验）
- `main.py:148,243,273`（reindex/stage/eval 的 `_ensure_schema` 先例）
- `server.py:69`（`/healthz`，reset 运行探测）
- `analysis/vision.py:463-500`（整窗帧置 ok、仅关键帧产事件）
- `remove.py:108-145`（window 归零删除）
- `remove.py:147-163`（受影响日重算）
- `remove.py:167-176`（向量删除 best-effort）
- `timeutil.py:75-106`（day_bounds_ms / date_window_ms）
- `docs/data_clean.md` §3.5/§4.5/D6（video prune 边界与复活取舍）
- `docs/bff_service_remove.md`（事件删除级联先例）
