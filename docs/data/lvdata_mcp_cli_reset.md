# lvdata-mcp reset CLI：分析与设计

> 状态：设计稿（未实施）；核心取舍已按推荐拍板（见「决策状态」与 §10.1）。
> 本文以当前 `tools/lvdata-mcp` 代码为唯一事实来源，关键断言附 `file:line`；
> 不记录方案演进历史。

**结论**：`reset` 以 recording session 为作用域；删除 `audio`/`video` 中
`analysis_status='ok'` 且无对应事件的源文件（不备份、不删 chat/meta），**同时清除
选中 session 的 events 及其派生数据**（FTS / 向量 / day_summary / day_stats /
derived 审计 JSON），把保留文件恢复为 `pending`（chat 清空 replay 水位），然后由
运行中 server 的下一次 poll/round 自然重新分析。这样避免旧事件、旧窗口、旧日摘要
残留。

| 用户要求 | 设计落点 |
|---|---|
| 删除已 parse 但无 events 的文件 | §3 D3/D4/D5 |
| 未 parse 的文件不能删除 | §2.2、§3 D4 |
| 重置 files 状态 | §3 D8 |
| **删除对应的 events，避免残留** | §3 D6/D7、§5.3-§5.7 |
| 由下一次 poll 重新分析，不主动 trigger | §3 D9、§5.9 |
| `--date` 必填；支持 YYYY / YYYY-MM / YYYY-MM-DD | §3 D1、§4.3 |
| 不做 `--all`、不做 from..to | §1.1 |
| 以 codebase 为准 | §2 全节、§11 锚点 |

### 决策状态（按推荐拍板）

以下核心取舍已按推荐值拍板（详见 §3 / §10.1）：

- **作用域**：按 recording session（D2）；不做严格文件/日期切片。
- **事件 purge**：选中 session 的全部非 window events + window 归零剔除（D6/D7）。
- **video 删除**：v1 按文件删除（D3/D4，沿用 video prune）；window-aware 后续。
- **源文件缺失事件**：仍 purge + warning（D13）。
- **跨 session window**：归零规则保留 + warning（D15）；扩大 scope 后续。
- **derived**：删除目标 session 的审计 JSON（D14）。
- **运行中 server**：v1 建议空闲/stop → reset → start；`reset_requests` 后续。
- **未配置 embedding**：跳过向量面 + warning；无 provider 清理后续。
- **day_summary**：接受额外 LLM；大范围用 stop → reset → start 降低。
- **交互**：`--dry-run` + `--yes`；`--keep-events` / `--keep-derived` /
  `--keep-missing-source-events` / `--timezone` / `--all` / from-to 均为后续。
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
  3. **删除对应的 events**，避免重分析后旧事件/旧窗口/旧日摘要残留。
  4. 让运行中的 lvdata-mcp server 重新分析；能靠下一次 poll 自然触发时不主动调用 REST。

### 1.1 非目标

- 不删除 chat / meta 源文件（见 §3 D3）。
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
- 非关键帧帧本身不产 video 事件（`analysis/vision.py:488-500,596-601`）；
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
video 扩展到选中 session 的全部事件类型。

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
- watcher 的 prune 也是独立写连接线程，先例说明“CLI/后台写 + 运行中 server”可行
  （`watcher.py:291-303,341-410`）；
- server 的分析 stage 与 CLI 没有共享内存锁；只能靠 SQLite 写事务串行化与
  “删除候选只取 `ok` 行”缩小竞态窗口。

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
- 不提供 `--all` / `--from` / `--to`（本期明确不做）。

### D2. 作用域按 recording session 取

`--date` 先解析为日期范围，再选 **recording_sessions**：

```
sessions = { rsession_id | recording_sessions.session_date BETWEEN start_date AND end_date
                            AND session_date != '' }
         ∪ { rsession_id | 该 session 有 audio/video 文件 uuid_ts_ms 在时间窗内 }
         ∪ { rsession_id | 该 session 有 events.ts_ms 在时间窗内 }
```

理由：

1. chat/meta 的 `uuid_ts_ms` 为 NULL（`recording_watch.py:213-220`），无法按文件
   时间戳切片；
2. video 窗口按 device 分组、跨 session（`analysis/vision.py:146-160`）；按单文件
   日期切片会把一个窗口拆成“部分帧在范围内、部分帧在范围外”，重分析分组不可复现；
3. `session_date` 是录制本地日期的既有字段（`recording_watch.py:267`），是存储层的
   自然日期键。

选中 session 后，**该 session 的全部 files 都进入 scope**（meta 除外，见 D3），
**该 session 的全部非 window 事件都进入 purge 范围**（见 D6）。已知语义：

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

- chat jsonl 是原始对话数据（`services/recording/session.py:130-134` 的 writer 产出），
  `chatlog` 只是把它事件化；删除源文件会丢失无法重建的用户数据。
- `session.json` 是 session 元数据，删除会破坏建档链路。
- `audio` / `video` 是分析媒体；已 parse 且无事件的文件属于“分析判定无信息量”的
  产物，删除有既有先例（video prune）。

### D4. “无事件”判定

- audio：`event_ref = f"{rsession_id}:audio:{Path(file_path).stem}"`，
  `events` 中存在该 ref → 有事件；
- video：`event_ref = f"{rsession_id}:video:{Path(file_path).stem}"`，同上；
- 只对 `analysis_status='ok'` 的行做该判定；
- `failed` / `pending` 一律不作为删除候选（没有成功 parse）。

### D5. 删除载体与顺序

沿用 video prune：

1. 路径护栏：`Path(file_path).resolve()` 必须在 `root_dir.resolve()` 下，否则跳过 + warning；
2. `unlink(missing_ok=True)` 删除物理文件；
3. `DELETE FROM files WHERE file_id=?`；
4. 不写 `cs.recording_files`（只读 ATTACH，`recording_watch.py:121-134`）；
5. 不做备份/回收站。

文件与行不是原子操作；崩溃后：
- 行在、文件不在 → 下次 reset 仍命中“ok + 无事件”，`missing_ok` 跳过 unlink，
  再删行（与 video prune 自愈方向一致）；
- 文件在、行已删 → 成为孤儿文件；本命令不扫描孤儿；可后续手工处理。

### D6. 删除对应的 events（核心新增）

**purge 范围 = 选中 session 的全部 events**，但 window 事件按“无剩余 video 事件”
规则单独判定（见 D7）。具体：

1. 对每个目标 session `S`，查询其全部 events（类型、ref、ts、payload）；
2. **删除非 window 事件**：`DELETE FROM events WHERE rsession_id=S AND type != 'window'`；
   - 包括 `chat / transcribe / translate / agent_job / audio / video`；
   - 这些事件由 chat 重放或 media 重分析重新生成；
   - 未知 `type` 一并删除并 warning（没有已知重建路径）。
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

4. 收集本次实际删除的 `event_ref` 与 `ts_ms`，供 FTS/向量/day_summary/day_stats
   清理使用；
5. 收集受影响的 session 集合 = 目标 session ∪ 被删 window 事件所在 session，
   用于 FTS 重建。

> 为什么删除全部非 window events：如果只删“文件对应的事件”，旧的 chat/agent_job
> 事件、被 video prune 删掉帧的 video 事件、以及重分析后不再产出的 audio 事件都会
> 残留。按 session 全量 purge 才能保证“当前文件集合的再次分析”是唯一事实。

### D7. 事件派生面清理

事件删除后必须在同一主事务内清理/重建：

| 面 | 操作 | 依据 |
|---|---|---|
| `search_docs` (FTS) | 先检查 schema：不存在 → `index.ensure_index(conn)`；存在但缺 `orig_text`/`session_id` 等必需列 → **中止**（不得静默 drop，否则清空全库 FTS）；否则对受影响 session 调 `index.rebuild_session`（先 DELETE 该 session 的 docs，再从剩余 events 重建）；session 行缺失时直接 `DELETE FROM search_docs WHERE session_id=?` | `index.py:78-114` |
| `index_state` | 删除目标 session 的行（强制下次 reindex 重建；`''` 全局行保留） | `index.py:125-163` |
| `vec_chunks` / `vec_state` | 收集被删 refs，`vec_index.remove_refs(refs)` 分批删除；失败记 `vectors_pending` | `vec.py:290-307` |
| `day_summary` | 受影响 `(timezone, local_date)` 置 `pending`、清空 doc/stats 重试水位 | `summary/day.py:345-358` |
| `day_stats` | 对所有 `day_summary` 中的时区，按被删事件 `ts_ms` 计算受影响日，调用 `DayStats.recompute_days` | `summary/day_stats.py:252-287` |
| `day_deferred` | 删除受影响 `(timezone, local_date)` 行，避免旧 deferred 触发 | `ddl.py:104-111` |
| `day_scan_state` | 不改；pending 行由 `_check_pending_retry` 兜底 | `summary/day.py:345-358` |
| `day_stats_state` | 不改；显式 recompute 已覆盖，后续新事件再推进 | `summary/day_stats.py:197-212` |
| `flush_state` | 不改；重分析事件会重新满足 flush 门控 | `watcher.py:230-258` |

`day_summary` 清理 SQL（每个受影响日）：

```sql
UPDATE day_summary
SET status='pending', generated_at=NULL, fail_count=0, error=NULL, model=NULL,
    day_summary='{}', event_count=0, day_stats='{}'
WHERE timezone=? AND local_date=?;

DELETE FROM day_deferred WHERE timezone=? AND local_date=?;
```

随后 `DayStats.recompute_days(conn, tz, [day], t0)`：

- 有剩余事件 → 更新 `day_stats`，行保持 pending，等 day_summary 重新生成；
- 无剩余事件 → 因 `status != 'ok'` 删除该行（日卡随数据消失）；
- 未被删除的其它日不受影响。

`day_summary` 置 pending 后，`_check_pending_retry` 会在活跃时区重新入队
（`summary/day.py:345-358`），`_debounce_ok` 仍遵守 `summarize_quiet_s`；若重分析
产生新事件，`updated_at` 会再次触发 discover。

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

按 session 处理，每个 session 一个**主库写事务**（`BEGIN IMMEDIATE`）：

1. （主事务前）删除该 session 的“ok + 无事件”媒体文件：每批在独立的
   `BEGIN IMMEDIATE` 短事务内复核 status/event 后 `unlink` + 删行 + 提交（D11）；
   这些文件没有 file-level event，不影响后续 purge；
2. （主事务前）删除该 session 的 derived 审计 JSON（D14），避免重分析写入新
   audit 后又被删除；
3. 主事务 `BEGIN IMMEDIATE`；
4. 删除该 session 的非 window events；按 D6 归零剔除 window events；
5. 对受影响 session 重建 FTS（`index.rebuild_session`）；
6. 删除目标 session 的 `index_state` 行；
7. 对被删事件 `ts_ms` 影响的日：更新 `day_summary` pending + 清 doc/stats，删
   `day_deferred`，`DayStats.recompute_days`；
8. 重置该 session 的 files 状态（D8）；
9. `COMMIT`；
10. （事务后）批量 `vec_index.remove_refs(deleted_refs)`；失败记 `vectors_pending`；
11. 处理下一个 session。

全部 session 完成后：

- 若配置了 embedding，调用 `vec_index.sweep_orphans()` 兜底清理崩溃残留；
- 对 scope 外 session 中被删的 window 事件，删除对应
  `video/windows/<window_id>.json`（D14，尽力而为）；
- 打印汇总；不调 REST。

崩溃语义：

- 主事务回滚 → 该 session 的 events / files 状态 / FTS / day_summary / day_stats /
  index_state 均未变；但事务前已删的无事件媒体与 derived 不恢复（见下条）；重跑 reset；
- 主事务已提交 → 该 session 已 purge/reset；server 可能已开始重分析；重跑 reset
  会再次 purge 新事件（幂等，最终仍为 reset 状态）；
- 向量删除在事务后 → 崩溃时可能残留；重跑时 `sweep_orphans` 清理；
- 文件/derived 删除在事务前 → 崩溃时 files 行可能在/不在、audit 可能已删但 events
  未 purge；重跑按“ok + 无事件”重新判定（`missing_ok` 自愈）；audit 由重分析重建。

单 session 异常（SQL/IO）→ 该 session 主事务回滚，记录 `failed_sessions`，继续处理
其余 session；最终退出码 `1` 并汇总。部分完成是安全的：重跑 reset 幂等。

### D11. 并发与竞态

- 无事件媒体删除阶段只取 `analysis_status='ok'` 的行；server 的分析取批只取
  `pending|failed`（`analysis/common.py:28-61`），正常分析不会与删除候选重叠；
- 无事件媒体删除阶段在独立的 `BEGIN IMMEDIATE` 短事务内**复核** `status='ok'` 且
  ref 不存在，再 unlink + 删行 + 提交，避免与 `trigger force`（会把状态改成 pending）
  竞态；文件 IO 在持有写锁期间执行（与 video prune 同序）；
- 事件 purge 主事务持有写锁；server 的 event 插入/状态更新会等待。单 session 事务
  同时做 events 删除、FTS 重建、day_summary/day_stats 清理，可能超过 server 端的
  `busy_timeout=5000`；server 写入失败会记日志并在下一轮重试。**大范围 reset 建议
  stop → reset → start**，既避免竞态也避免长时间占锁；
- 主事务提交后 server 可能立即重分析该 session；这是期望行为；
- 状态重置可能与在途分析互相覆盖：若 server 在主事务后完成并写回 `ok`，该文件
  本轮不会再次进入 pending。**确定性做法**：在 server 空闲时执行 reset，或
  stop → reset → start；v1 接受该竞态并在输出 warning。
- chat 文件同理。
- 与 `POST /api/events/remove` 并发：两端都是写事务，SQLite 会串行化，但语义会交错
  （remove 按 video ref 删并级联，reset 按 session 全量 purge）。v1 建议不要与
  remove 并发执行；文档层面只保证单写者顺序。

### D12. 安全交互

- `--dry-run`：只打印计划，不 unlink、不 UPDATE、不 DELETE；
- 实际执行需要 `--yes`；否则打印计划并退出（防误删）；
- 无备份；删除路径不逐条打印到 stdout（量大），只打印计数、字节数与失败路径；
- 退出码：`0` 成功/空操作；`1` 运行时错误（DB 缺失、锁超时等）；`2` 参数/日期非法或
  未带 `--yes`。

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
- 对被删 window 事件位于其它 session 的情况，删除对应
  `derived/<device>/<session>/video/windows/<window_id>.json`（尽力而为）；
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
lvdata-mcp [--config PATH] reset --date <spec> [--dry-run] [--yes]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--date` | 是 | `YYYY` / `YYYY-MM` / `YYYY-MM-DD`；严格校验 |
| `--dry-run` | 否 | 只输出计划，零写入 |
| `--yes` | 否 | 实际执行删除/重置；未带时只打印计划并退出 2 |
| `--config` | 否 | 既有全局参数，位于子命令之前 |

### 4.2 输出

`--dry-run` 示例：

```
reset plan: date=2026-09-28 timezone=Asia/Shanghai
  date window: 2026-09-28 .. 2026-09-28
  sessions: 3
  files in scope: audio=120 video=800 chat=6 meta=3
  delete candidates: audio=40 (12.3 MB) video=650 (80.1 MB)
  events to purge: chat=120 transcribe=30 translate=30 agent_job=2
                   audio=80 video=150 window=20 (window 归零后 18)
  fts docs: 432 (sessions=3)
  vector chunks: 432 (if embedding configured)
  day_summary affected: 4 day/timezone rows
  reset: audio=80 video=150 chat=6
  missing_source_events: 2 (cannot be regenerated)
  warnings:
    - audio/video files with no event and status ok will be deleted (no backup)
    - chat/meta source files are never deleted
    - events and derived audit JSON for the selected sessions will be purged
    - video window events with remaining video events outside scope are kept
```

实际执行输出同样结构，另加 `deleted=... bytes=... events_deleted=... fts_rebuilt=...
vectors_removed=... day_summary_reset=... failed_unlink=... vectors_pending=...`。

### 4.3 日期校验

```python
^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$
```

- `YYYY`：年份 0001–9999；
- `YYYY-MM`：月份 01–12；
- `YYYY-MM-DD`：真实日期（含闰年）；
- 不接受 `2026-2-3`、`2026-13`、`2026-02-30`、空串、`all`、`from/to`。

---

## 5. 执行算法

### 5.1 伪代码

```
cfg = load_config()                        # 含 provider 校验；reset 不调 LLM 但配置需完整
spec = parse_date_spec(args.date)          # 校验 + [start_date, end_date]
conn = open_db(cfg.data.resolved_db_path)  # WAL, busy_timeout=5000
check_tables_exist(conn)                   # events/files/recording_sessions/day_summary/
                                           # day_deferred/index_state 的关键表/列缺失 -> exit 1；
                                           # 不建表/不迁移（必要时先启动一次 serve 让
                                           # ddl.ensure_schema 补齐）；search_docs 允许缺失
                                           # （FTS 可建）
check_fts_schema(conn)                     # search_docs 旧 schema -> exit 1（先 reindex）

reset_sessions = select_sessions(conn, cfg, spec)   # D2
plan = build_plan(conn, reset_sessions)              # 只读：文件/事件/派生面计数

print_plan(plan)
if args.dry_run: return 0
if not args.yes: print("re-run with --yes"); return 2

cross_window_audits = []
for sid in reset_sessions:
    # 1) 无事件媒体文件删除（事务前；这些文件没有 events）
    delete_no_event_media(conn, sid)                 # D4/D5
    # 2) 旧 derived 审计删除（事务前，避免删掉重分析新写入的 audit）
    delete_derived_for_session(sid)                  # D14

    # 3) 主事务：事件 purge + 派生面 + files 状态
    with write_tx_immediate(conn):
        deleted = purge_events(conn, sid)            # D6；返回 refs/ts/affected_sessions
        rebuild_fts(conn, store, deleted.affected_sessions)   # D7
        delete_index_state(conn, sid)                # D7
        reset_day_summary_and_stats(conn, deleted.ts_list)    # D7
        reset_files(conn, sid)                       # D8
    cross_window_audits += deleted.cross_session_windows
    # 4) 事务后：向量删除
    remove_vectors(deleted.refs)                     # D7；失败记 vectors_pending

# 5) 全量兜底
if vec_index is not None:
    vec_index.sweep_orphans()
delete_cross_session_window_audits(cross_window_audits)   # D14（尽力而为）

print_summary()
return 0
```

### 5.2 选 session SQL

```sql
-- 主选（session_date 在日期范围内）
SELECT rsession_id
FROM recording_sessions
WHERE session_date != '' AND session_date BETWEEN :start_date AND :end_date;

-- 回退 1：该 session 有媒体文件落在时间窗内
SELECT DISTINCT rsession_id
FROM files
WHERE file_type IN ('audio','video')
  AND uuid_ts_ms BETWEEN :start_ms AND :end_ms;

-- 回退 2：该 session 有 events 落在时间窗内
SELECT DISTINCT rsession_id
FROM events
WHERE ts_ms BETWEEN :start_ms AND :end_ms;
```

三段取并集，写入临时表：

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

-- 2) 删除非 window 事件
DELETE FROM events WHERE rsession_id = :sid AND type != 'window';
```

Python 侧：

- 对收集到的 `video` 事件，解析 `payload.window_id`；
- 对收集到的 `window` 事件，解析 `payload.window_id`；
- 目标 `window_id` 集 = 两类并集；
- 删非 window 后，**一次遍历**全部 video 事件建立 `window_id -> 剩余计数`
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

`deleted.refs` = 非 window 的 refs + 被删 window 的 refs；
`deleted.ts_list` = 对应 `ts_ms`；
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
- `recording_sessions` 行缺失时 `rebuild_session` 返回 0 且不会删除 FTS 行；
  此时直接 `DELETE FROM search_docs WHERE session_id=?`；若该 session 仍有 events，
  输出 warning（缺 device_uuid，无法重建）。
- 删除目标 session 的 `index_state` 行（`DELETE FROM index_state WHERE target=?`），
  使重分析后 `lag_sessions` 必然重建（`index.py:147-163`）。
- 其它受影响 session（仅删了 window 事件）保留原水位；FTS 已重建，无需强制。

### 5.5 向量删除

- 收集 `deleted.refs`，按 500 个一批调用
  `vec_index.remove_refs(batch)`（`vec.py:290-307`；与 video prune 的 500 行批次
  一致，避免 `IN` 占位符过多）；
- `remove_refs` 同时删 `vec_chunks` 与 `vec_state`；
- 未配置 embedding（`build_provider` 返回 None）→ 整个向量面跳过；若磁盘上仍存在
  旧 `lvdata_index.db`，其 chunk 不可查询（`semantic_search` 返回
  `embedding_disabled`），作为已知残留记录 warning（见 §10.1）；
- 任一批异常 → log warning + `vectors_pending=True`；继续；
- 全部 session 完成后调用 `vec_index.sweep_orphans()`（`vec.py:319-346`）兜底，
  清理崩溃/失败残留。

### 5.6 day_summary / day_stats

对 `deleted.ts_list`：

```python
tzs = [r[0] for r in conn.execute("SELECT DISTINCT timezone FROM day_summary")]
for tz in tzs:
    days = sorted({ms_to_local_date(ts, tz) for ts in deleted.ts_list})
    for d in days:
        conn.execute("""
            UPDATE day_summary
            SET status='pending', generated_at=NULL, fail_count=0, error=NULL, model=NULL,
                day_summary='{}', event_count=0, day_stats='{}'
            WHERE timezone=? AND local_date=?
        """, (tz, d))
        conn.execute("DELETE FROM day_deferred WHERE timezone=? AND local_date=?", (tz, d))
    day_stats.recompute_days(conn, tz, days, now_iso())
```

- `DayStats.recompute_days`（`summary/day_stats.py:252-287`）：
  - 有剩余事件 → `UPDATE day_stats`；
  - 无事件且 `status != 'ok'` → 删除该行（我们刚置 pending）；
  - 无行 → 跳过。
- `day_summary` doc 清空后，BFF/MCP 不会读到旧摘要；pending 行由
  `_check_pending_retry` 重新生成（`summary/day.py:345-358`）。
- 若该日仍有 scope 外的剩余事件 → 行保持 pending，discover 会从剩余事件重新生成；
  若没有任何剩余事件 → 行被 `recompute_days` 删除；若之后重分析产生新事件，
  discover 会新建该行。
- 置 pending 可能让 flush 组在重分析前先按剩余事件生成一次日摘要（额外一次 LLM
  调用），重分析后还会再生成；这是正确性优先的取舍（§7.7）。

### 5.7 文件删除与状态重置

**无事件媒体删除**（事务前，逐候选）：

- 条件：`file_type IN ('audio','video') AND analysis_status='ok'`，且 event_ref 不存在；
- `unlink(missing_ok=True)` → `DELETE FROM files`；
- 路径护栏；失败跳过。

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

- `device_uuid`/`rsession_id` 来自 `recording_sessions`；
- 路径必须在 `<root>/derived/` 下；
- 先删旧 audit，再 purge/重分析，避免重分析写入的新 audit 被随后的清理删掉；
- 对 scope 外 session 的被删 window 事件，在主事务后删除对应
  `.../video/windows/<window_id>.json`（尽力而为；这些 session 不会被重分析，
  所以不会误删新 audit）；
- 失败只 warning（不影响事件/索引一致性）。

### 5.9 执行后验证

```sql
-- 目标 session 的 events 应为 0（或仅剩跨 session 保留的 window 事件）
SELECT COUNT(*) FROM events e JOIN reset_sessions s ON s.rsession_id=e.rsession_id;

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

运行中的 server 下一次 poll/round 后：

- `mcp_pipeline_runs` 出现 `chatlog` / `audio` / `video` run；
- `analysis_runs` 新增对应文件的调用记录；
- events 重新生成，`updated_at` 刷新；
- FTS/向量/day_summary/day_stats 由正常流水线跟进（§6）。

不要求、也不建议在 reset 后手动调用 `POST /api/analysis/trigger`。

---

## 6. 派生数据与后续链路

### 6.1 事件 purge 后

- 目标 session 的 `chat / transcribe / translate / agent_job / audio / video` 事件
  被删除；清掉对应 FTS 行与向量行；
- window 事件按“无剩余 video 事件”归零删除；仍有跨 session video 事件的窗口保留；
- `day_summary` 受影响日置 pending 并清空 doc；`day_stats` 立即按剩余事件重算；
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

- 旧 window/video 事件已在 reset 中 purge（跨 session 保留的 window 事件除外，见 D15）；
- 新 window 事件会生成，`window_id` 可能变化；
- 若窗口仍有 scope 外 video 事件，旧 window 事件保留（D15 边界），其
  `frame_count/window_caption` 可能不包含本次删除/重分析的帧。

### 6.5 BFF / MCP 的可见性窗口

reset 执行到重分析完成之间：

- 目标 session 的 events 消失；`search`/`semantic_search`/`get_timeline`/
  `get_recent_events` 看不到这些事件；
- `day_summary` 受影响日 pending、doc 清空；日卡可能显示 pending/缺失；
- FTS/向量已清理，不会返回已删事件；
- 重分析完成后逐步恢复。

这是“删除后重建”的预期空窗；`--dry-run` 可先评估范围。

---

## 7. 风险、限制与取舍

### 7.1 无备份、事件不可恢复

reset 删除物理文件与 events，且清空 day_summary doc；没有备份/回收站。若重分析
失败或源文件缺失，旧分析结果不可恢复。`--yes` 与 `--dry-run` 是唯一防线。

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

### 7.5 运行中 server 的竞态

- 事件 purge 主事务持有写锁；server 写入会等待；
- 单 session 主事务同时做 events 删除、FTS 重建、day_summary/day_stats 清理，可能
  超过 server 的 `busy_timeout=5000`，导致 server 写入失败并在下一轮重试；
  **大范围 reset 建议 stop → reset → start**；
- 主事务提交后 server 可能立即重分析；这是期望行为；
- 若 server 已完成的在途分析在主事务后写回 `ok`/新事件，该文件本轮不会再次进入
  pending；**建议在 server 空闲时执行，或 stop → reset → start**；
- chat 同理。

### 7.6 `inventory --force` 复活

删除 files 行后，`stage inventory --force` 会从 `cs.recording_files` 全量重放，把
被删文件重新插入为 `pending`（`recording_watch.py:158-161,213-220`），随后分析因
物理文件缺失失败。与 video prune 同口径接受（`docs/data_clean.md` D6）。日常同步
不会复活：keyset 水位已过，audio/video 的 cs 行不会被 touch。

### 7.7 day_summary 的额外 LLM 成本

- 事件 purge 后受影响日置 pending、doc 清空；flush 门控可能在重分析前先按剩余事件
  生成一次日摘要；
- 重分析后 `updated_at` 再次触发重生成；
- 因此单日可能多一次 LLM 调用。大范围（`YYYY`）reset 成本需用 `--dry-run` 评估。
- 若希望避免中间生成，可先 stop server，reset 后 start（重分析会先发生，再走 flush）；
  但 reset 仍会置 pending。

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

- 用户要求“删除对应的 events 避免残留”，因此 v1 默认 purge；
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
- `session_date=''` 时由 media/events 回退命中；
- 无媒体/无事件的 session 不进 scope；
- 跨午夜 session 整 session 进 scope；
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
- window 事件归零删除；
- window 仍有 scope 外 video 事件 → 保留 + warning；
- 被删 window 事件所在其它 session 的 FTS 重建；
- `deleted.refs`/`ts_list` 正确收集。

### 8.5 派生面

- `search_docs` 中目标 session 行被删除并重建；
- `index_state` 目标 session 行删除；
- `vec_chunks`/`vec_state` 被删 refs 清除；`sweep_orphans` 兜底；
- `day_summary` 受影响日 pending、doc 清空、`day_deferred` 清空；
- `day_stats` 有剩余事件时重算，无事件时行删除；
- 非活跃时区同样处理；
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
- unlink 失败 → 保留行，后续重试；
- `cs.recording_files` 不变；
- derived JSON 按 D14 删除；
- `--dry-run` 零写入。

### 8.8 崩溃/竞态

- 主事务回滚 → DB 的 events/files 状态/派生面不变；但事务前已删的无事件文件/
  derived 不恢复，重跑 reset 幂等（D10）；
- 主事务提交后崩溃 → 重跑可清；
- 向量删除失败 → `vectors_pending`，`sweep_orphans` 清；
- 无事件媒体删除事务内复核：status 从 ok 改为 pending → 跳过；
- event 在计划后出现 → 删除阶段以事务内查询为准；
- `index.ensure_index` 旧 schema 场景 → 预检中止，不 drop 全库 FTS（D7/§5.4）。

---

## 9. 实施计划

### 9.1 代码

| 文件 | 改动 |
|---|---|
| `src/lvdata_mcp/main.py` | 新增 `reset` 子命令：`--date`（required）、`--dry-run`、`--yes`；调用 `run_reset(cfg, ...)` |
| `src/lvdata_mcp/reset.py`（新增） | 日期解析、session 选择、计划、文件删除、事件 purge、FTS/向量/day_summary/day_stats 清理、derived 清理 |
| `src/lvdata_mcp/remove.py`（可选重构） | 把 `_delete_empty_windows` / `_recompute_days` 的通用逻辑提为可复用 helper（或 reset 内复刻并加测试） |
| `src/lvdata_mcp/timeutil.py`（可选） | 提取 `period_bounds` 辅助（年/月/日 → start/end 与 ms 边界） |
| `tests/test_reset.py`（新增） | §8 测试 |
| `tools/lvdata-mcp/README.md` | 增加 reset 用法、事件 purge 与风险说明 |
| `docs/data/lvdata_mcp_design.md` | 如实施，补充 CLI 清单 |

不修改：

- `watcher.py` / `analysis/*` / `summary/*`：复用现有状态机与取批；
- `ddl.py`：不新增表/列（沿用删除行语义，不做 tombstone）；
- `recording_watch.py`：不写 `cs.recording_files`；
- `server.py`：不新增 REST（reset 是本地 CLI，不是 HTTP 接口）。

### 9.2 复用点

- FTS：`index.ensure_index(conn)`（先做 schema 前置检查）+ 
  `index.rebuild_session(conn, store, session_id)`（`index.py:78-114`）；
- 向量：`VecIndex.remove_refs(refs)` / `sweep_orphans()`（`vec.py:290-307,319-346`）；
- day_stats：`DayStats.recompute_days(conn, tz, days, t0)`（`summary/day_stats.py:252-287`）；
- 窗口归零：仿 `remove._delete_empty_windows`（`remove.py:108-145`）；
- 受影响日：仿 `remove._recompute_days`（`remove.py:147-163`）；
- 时间：`timeutil.day_bounds_ms` / `ms_to_local_date`（`timeutil.py:75-86,133-136`）。

### 9.3 实现要点

- 所有写连接：`PRAGMA journal_mode=WAL`（已存在库）、`busy_timeout=5000`；
- 事件 purge + FTS + day_summary/stats + files 状态在**同一主事务**；按 session 分批；
- 文件 unlink / derived 删除在事务外；向量删除在事务后；
- 删除候选复核在 `BEGIN IMMEDIATE` 内；
- 路径护栏；
- 输出 `--dry-run` 计划与实际汇总；
- 失败路径全部 warning + 继续（部分成功），最后汇总 `vectors_pending`/`failed_unlink`。

---

## 10. 决策记录与后续

### 10.1 已拍板（v1，按推荐值）

| 决策 | 结论 |
|---|---|
| 作用域 | 按 recording session（D2）；不做严格文件/日期切片 |
| 事件 purge | 选中 session 的全部非 window events + window 归零剔除（D6/D7） |
| video 无事件文件 | v1 按文件删除（D3/D4，沿用 video prune） |
| 源文件缺失事件 | 仍 purge + warning（D13） |
| 跨 session window | 归零规则保留 + warning（D15） |
| derived 审计 JSON | 删除目标 session 的 derived（D14） |
| 运行中 server | v1 建议空闲/stop→reset→start；接受竞态 warning（D11/§7.5） |
| 未配置 embedding | 跳过向量面 + warning；旧 vec DB 作为已知残留（§5.5） |
| day_summary 额外 LLM | 接受；大范围用 stop→reset→start 降低（§7.7） |
| 交互 | `--dry-run` + `--yes`（D12） |
| tombstone | 不做；沿用 video prune 先例（§7.6） |
| 时区 | 固定 `watcher.timezone`，不提供 `--timezone`（D1/D9） |

### 10.2 后续可选（不阻塞 v1）

1. `--keep-events`：只重置文件、不 purge events。
2. `--keep-derived`：保留人工核查用 derived 审计 JSON。
3. `--keep-missing-source-events`：源文件缺失的事件跳过/中止而非 purge。
4. `--timezone`：按设备/请求时区 reset。
5. `--all` / `--from..--to`：扩大 reset 范围（会放大删除、FTS 重建与 LLM 成本）。
6. `reset_requests` 表：由 watcher 在 round 开头应用，消除跨进程竞态。
7. tombstone：防止 `inventory --force` 复活被删 files 行。
8. window-aware video 删除：只删零关键帧窗口，保留有事件窗口内的非关键帧。
9. 无 provider 的旧向量库清理：embedding 未配置时也清理旧 `lvdata_index.db`。
10. 跨 session window scope 扩展：把整个窗口涉及的 session 纳入 reset，消除 stale payload。
11. derived 的跨 session frame JSON 清理：目前只尽力清理 window JSON。
12. 严格文件/日期 scope：按 `files.uuid_ts_ms`/`events.ts_ms` 精确切片；需同时解决
    video 窗口部分重组与 chat 整文件重放问题。

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
- `summary/day.py:345-358`（pending/failed 重试发现）
- `summary/day_stats.py:252-287`（按日重算 day_stats）
- `remove.py:108-145`（window 归零删除）
- `remove.py:147-163`（受影响日重算）
- `remove.py:167-176`（向量删除 best-effort）
- `timeutil.py:75-106`（day_bounds_ms / date_window_ms）
- `docs/data_clean.md` §3.5/§4.5/D6（video prune 边界与复活取舍）
- `docs/bff_service_remove.md`（事件删除级联先例）
