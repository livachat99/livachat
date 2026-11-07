# 录制数据处理重构设计：recording_files 单一事实源 + per-event 处理

> 状态：**已评审，待实施**。本文按 2026-09-15 讨论思路重新调研设计，
> 取代旧提案 `docs/data/lvdata_recording_files_inventory_plan.md`
> （旧的 cs 扩 recording_sessions + 保留 per-session 链方案作废）。
> 2026-09-15 review 意见已并入：三项高优先级结论（§2.1 updated_at 不变式 /
> §2.1 队列丢弃纪律 / §2.7 重放成本重估），开放问题 A–H 裁定见 §5。
> 2026-09-15 二次 review 修订：补齐新行初始状态与 ON CONFLICT 语义（§2.2 步骤
> 1）、force 按类型分派（§2.4）、回填窗口 offset 落地语义（§2.7）、meta 处理
> 位置对齐（§2.3）、FK/PRAGMA 与 ref_id 清洗防回退细节（§2.2/§2.5）。
> 2026-09-15 三次修订：确认产品未上线、无生产存量、**dev 数据可弃** → 取消
> `source: fs|cs` 双轨与影子对拍/灰度（§2.6/§2.7/§3），切换清单简化为 dev
> 重建；fs 扫盘路径随 lvdata 重构一次性删除。

## 0. 提案要点（需求整理）

1. **只新增一张 `recording_files` 表**（chat_server.db），不改 `recording_sessions`。
   所有录制文件登记：`session.json`、`chat/history.jsonl`、`chat/transcribe.jsonl`、
   `chat/translate.jsonl`、`audio/*.ogg`、`video/*.jpg`（+ `image/*.jpg` 见 §5.B）。
   每行 `device_uuid / recording_session_id / created_at / updated_at`；
   **文件内容更新（jsonl append）必须 bump updated_at**——trailing-edge 保证 +
   严格单调 ms（不变式见 §2.1，是接口契约不是调优项）。这是唯一事实源。
2. lvdata-mcp 只轮询 `recording_files`，不再扫文件系统；结果同步进 lvdata `files`
   表 track 处理状态。**取消 agent_watch**（agent job 直接解析 history.jsonl 获得，
   仅已投递完成的）。**chat 三个 jsonl 都需处理进 events 表**。
3. 处理流程 **per-type per-round**（不按 session）：每轮 poll → chatlog 处理 →
   audio 处理 → video 处理；出错不在本轮重试、只标记，下轮继续；fail_count>3 停止。
   每轮产出新 events → index / vector / day_summary 触发。
   批处理阈值与跨天策略**后置优化**（本文只留挂点）。

**已裁定（2026-09-15）**：
- **A**：`transcribe` / `translate` **新增为独立事件类型**（不复用 chat+source），
  跨服务联动清单见 §1.3 / §2.5；
- **B**：agent job 历史行新增 `agent_name`（hermes|openclaw）与 `agent_id`
  （昵称，来源 = adapter 配置项，评审裁定 F）字段；并携带 `job_key`
  （消除新旧事件重复，§2.5）。
- **范围**：本阶段只做 **server 端**（livachat server + lvdata-mcp）。
  **web app 已废止，不更新**；**Android 放到后期**统一适配（新事件类型的
  客户端展示不阻塞本阶段）。
- **无存量包袱（2026-09-15）**：产品未上线、**dev 数据可弃** → 不保留
  `fs|cs` 双轨、影子对拍与灰度切换；cs 单链路直接开发与测试，fs 扫盘路径
  一次性删除（§2.6/§2.7）。

**评审裁定（2026-09-15 review，详见 §5）**：E 语义（trailing-edge + 严格单调 ms）、
A 粒度（FTS per-session 重建 + vector per-event 增量）、D（保留 list_jobs）、
F（agent_id = adapter 配置 nickname）、G（lvdata `chat_messages`，2026-09-15 修订为删除）、
H（窗口按 `(device_uuid, 时间戳)` 分组）、audio/video 取件移除
`session.status` 过滤（§2.2）。

## 1. 调研结论（支撑设计的事实与出处）

### 1.1 录制文件全景（服务端写点）

| 文件 | 写点 | 时机 | 格式 |
|------|------|------|------|
| `session.json` | `services/recording/session.py` | **仅 start 首写一次**，此后不再变动（幂等：已存在则跳过；session_json_refactor.md） | 原子 tmp+replace；静态 7 键；**stats 已不再记录** |
| `audio/<uuid7>.ogg` | `session.py:316, 437-464` | 60s 轮转；`.part` 流式 → close 后 rename | opus/ogg 分段 |
| `video/<uuid7>.jpg` | `session.py:334-341` | 每帧一文件，原子写 | JPEG |
| `chat/history.jsonl` | `services/recording/chatlog.py:29-64` | 每条 append + 立即 flush | append-only，永不重写 |
| `chat/transcribe.jsonl` | `session.py:350-357` | 语音转写完成时 | 同上 |
| `chat/translate.jsonl` | `session.py:364-380`（路由 `source in ("translate","lecture")`） | 翻译完成时 | 同上 |
| `image/<uuid7>.jpg` | `session.py:409-427` | chat 挂图快照 | JPEG（lvdata 不消费，见 §5.B） |

### 1.2 三个 chat jsonl 逐行 schema（写作于 `chatlog.py:45-58`）

共用字段：`ts`(int ms) / `utc`(ISO 毫秒 Z) / `role`(user|assistant) / `text` /
`ref_id` / `chat_session_id`；可选 `interrupted:true`、`source`、`images:[...]`。

- **history.jsonl**：普通对话行 + **agent job 结果行**。后者唯一区分字段
  `source:"agent_job"`（`services/session/agent_job_ingest.py:51-101`、
  `services/agent_relay/publisher.py:111-116`）；仅**投递成功**的 job 落行，
  失败/未投递不写；`ref_id` 为 message_id（兜底 `msg_agent_job_<job_key>`）。
- **transcribe.jsonl**：`source:"transcribe"`，user 行 `ref_id=transcription_id`。
- **translate.jsonl**：`source in ("translate","lecture")`。
- 三种 pipeline 模式**互斥**（同一语音段只落其一）；user/assistant 两轨不共享
  id，靠 ts 时序关联。

### 1.3 事件类型的外部依赖（新增 transcribe/translate 的联动清单）

裁定 A 选择新增两个事件类型，跨服务触点全量清点如下（实现时逐一改）：

| 服务 | 触点 | 范围 |
|------|------|------|
| lvdata | `consts.py:3`（EVENT_PRIORITY/EVENT_TYPES）；`store.py` get_timeline 游标/排序 CASE SQL（`:197-207` 两处）；`tools.py:95` `searched_types` 硬编码列表 + search 的 type 参数校验；`summary/day.py:45-47` TYPE_MAP；tests（golden_queries.json 等） | **本阶段** |
| bff | `services/bff/common/store.py:22-23`（EVENT_PRIORITY/EVENT_TYPES）+ `:29-30` `_PRIO_SQL`；未知 type 落 ELSE 桶，仅同 ts 排序差异，不阻塞。**验收点**：优先级重排后 ELSE 桶须 4→6——现状 ELSE 4 恰等于 agent_job 优先级，不改则未知类型错排到 agent_job 之前 | 视在役情况 |
| web（`apps/web/mylife`） | `types.ts:3` EventType 联合类型；`EventCard.tsx:119` switch（未知 type 返回 null） | **已废止，不更新** |
| Android | `MylifeDtos.kt:10-15` EventType 常量（当前无卡片分派逻辑，新增类型不会崩） | **后期** |
| 其他 | agent_relay 等无类型枚举；`events.type` 列无 DB 约束（自由文本） | — |

→ 影响排序（见 §2.5）：优先级在 **server 端**同步重排；无客户端发布阻塞。

### 1.4 现状机制的可复用 / 须移除

| 处置 | 机制 |
|------|------|
| 复用 | `files` 状态机语义（pending/ok/failed、fail_count<3 + 退避）、LlmClient、audio/vision 分析模块、day_summary、index/vec、tools、bff 消费面 |
| 移除 | `scanner.py` 全量扫盘（discover/mtime/staleness/`.part` 过滤）、`jobs_watch.py`（agent_jobs 同步 + links enrich + pending 合并）、`detect_dirty`（改为按类型扫 pending 文件）、watcher per-session 队列/锁/链、`finalize_stale_s`（服务端 finalize 权威：idle TTL 3600s + 跨天 + 关闭 flush，见 `service.py:_tick`） |

## 2. 设计

**处理原则（2026-09-15 补充）**：recording session 是**存储层语义**（目录归属），
处理逻辑不引入 session 边界——取件、分组、调度一律以**文件时间戳 / 内容 / 状态**
为准；跨 session 的连续帧合并处理是正确行为（如 23:59 与 00:01 的连续录屏帧
应属于同一窗口）。session 字段仅作事件/产物的存储归属。

### 2.1 服务端：`recording_files` 表（chat_server.db）

```sql
CREATE TABLE IF NOT EXISTS recording_files (
    file_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_uuid          TEXT NOT NULL,
    recording_session_id TEXT NOT NULL,
    kind                 TEXT NOT NULL,   -- session|chat_history|chat_transcribe|chat_translate|audio|video|image
    rel_path             TEXT NOT NULL UNIQUE,  -- 相对 data root（含 device_uuid 段，多设备不撞）
    size_bytes           INTEGER NOT NULL,
    ts_ms                INTEGER,          -- uuid7 解析或写入时刻
    duration_ms          INTEGER,          -- audio 分段时长；其余 null
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL     -- 固定 YYYY-MM-DDTHH:MM:SS.fffZ；严格单调递增；变更即 bump
);
CREATE INDEX IF NOT EXISTS idx_rf_updated ON recording_files(updated_at, file_id);
CREATE INDEX IF NOT EXISTS idx_rf_session ON recording_files(recording_session_id);
```

字段与死 PG 表（`services/migrations/versions/0002_recording_module.py:48-61`）
同族，名字可直接参考。

**updated_at 不变式（接口契约，非调优项）**：

1. **trailing-edge 语义**：允许合并（如 ≤1s 防抖），但**末次 append 后必须
   有一次 bump**。前沿丢弃（窗口内丢 bump）会让持续 append 期间 updated_at
   无限滞后于内容——lvdata 永远看不见新行，单一事实源失效；
2. **严格单调递增**：keyset 水位 `(updated_at, file_id)` 对「re-bump 到相等值」
   隐形（`(T,5) > (T,5)` 为假）。每次 bump 取 `max(now_ms, prev+1ms)`；
3. **格式钉死毫秒**：cs 写方与 lvdata keyset 均按字符串序比较，混精度
   （有无毫秒）会直接打乱水位。

**写入点**（全在服务端 recording 侧，与文件写同点；纪律：**先落盘、后登记，
永不反向**——lvdata 见到行时文件必然已在盘上）：

| 时机 | 操作 |
|------|------|
| 会话创建（start） | 登记 session.json 行一次（INSERT OR IGNORE 幂等；记录此时 size） |
| ~~每次 session.json 落盘（start/finalize/ticker）~~ | **已删除**：静态化后不再重写/ touch（session_json_refactor.md） |
| 音频段 close+rename 后（`:459-464`） | INSERT audio 行 |
| 视频帧原子写后（`:334-341`） | INSERT video 行 |
| 三个 jsonl 每条 append 后 | UPDATE size_bytes/updated_at（trailing-edge bump，参数见 §5.E） |
| image 写入后（若登记） | INSERT image 行 |

**写者纪律（结构性约束）**：chat_server.db 单写者 = ChatStore 写队列
（`services/shared/persistence/chat_store.py:_queue`）。recording 服务必须经
ChatStore 新增写方法（`record_file()/touch_file()`）入同一队列，**不得自开
第二写连接**（WAL 锁竞争 + 顺序失控）。新增两条纪律：

- **登记不得静默丢弃**：现有 `_enqueue` 队列满即丢（仅 metric，
  `chat_store.py:264-272`）——messages 可承受，登记行丢弃 = 永久孤儿文件。
  `record_file()/touch_file()` 必须**带超时阻塞入队**（背压传导给 recording
  writer task）；入队失败走 recording "write failed" 告警路径 + 落本地
  reconcile 清单（`<data_root>/recording_files_reconcile.jsonl`，一行一失败
  {ts, op, rel_path, error}；已实施）；
- **backfill CLI 同受此约束**：走 ChatStore 接口，或声明离线（服务停态）
  执行，二选一写进 CLI help。

**backfill**：一次性 CLI 扫 data root 生成存量行（INSERT OR IGNORE 幂等），
随服务端升级交付；也是「孤儿文件」补救入口之一（§4）。

**retention**：行只增不减（lvdata `files` 表同，现状即如此，量级 ≈ 每帧一行）。
保留策略后置，不阻塞本阶段。

### 2.2 lvdata 同步层（inventory）

新模块 `recording_watch.py`（沿用 `jobs_watch.py` 模式：ATTACH `mode=ro` +
keyset `(updated_at, file_id)` + **lvdata 库单事务**）：

1. 读 `cs.recording_files` 增量（keyset 水位表 `recording_files_sync_state`）
   → upsert lvdata `files` 行：`file_type` 细分
   （`audio/video/meta/chat_history/chat_transcribe/chat_translate`）、
   `file_path = root / rel_path`（归一化拼接；lvdata data root 须与 cs
   base_dir 同路径——ATTACH 本就要求同机文件访问）、
   `src_updated_at = cs.updated_at`；
   **新行初始状态**：audio/video → `pending`，chat/meta → `na`（状态列语义
   见下）；**存量行（`ON CONFLICT(file_path)`）只更新
   `file_size`/`src_updated_at`/`scanned_at`，不覆盖
   `analysis_status`/`fail_count`/`error`/`processed_offset`/
   `processed_updated_at`**（重扫不重置分析进度与游标，同现 `_upsert_file`
   语义）；
2. **session.json（meta）行即时处理**：读盘（原子文件）→ upsert
   `recording_sessions`（映射同现 `scanner.py:_upsert_session`，stats 已为空
   不再解析）；静态化后无 finalize 转换与 `day_force`（session_json_refactor
   裁决 A：旧文件动态字段照常摄入但不消费）；
3. **session 行存在性兜底**：files 行写入前按 `(device_uuid, rsession_id)`
   `INSERT OR IGNORE` 最小 `recording_sessions` 行（`status='active'` 占位）。
   缺行有两条路径：FK 开启时（同步连接须 `PRAGMA foreign_keys = ON`，与
   `scanner.py:93` 一致）INSERT **硬报错**；FK 未开启时取件 JOIN 掉行 →
   **静默不处理**。兜底行把两条路径都消除。session 只是存储归属（§2 处理
   原则），分析取批与展示仍要 JOIN 该表；
4. **chat 类文件不写 `uuid_ts_ms`（保持 NULL）**：bff `day_has_pending`
   按 `f.uuid_ts_ms BETWEEN ? AND ?` + `analysis_status` 判定且**无 file_type
   过滤**（`services/bff/common/store.py:381-400`）——填入时间戳会把 chat 处理
   状态混入按日 pending 统计。此约束须写进 files 表注释 + 回归测试，防后人
   「顺手」补上时间戳；
5. 单事务提交：files/recording_sessions + 水位推进。**不需要
   pending 列表**：处理层按 `files` 状态驱动（取件规则见本节末），水位提交后
   即使崩溃，未处理文件仍是 pending，下轮自然捡起；
6. 返回本轮变更简报（统计用）→ 触发处理层 round。健康口径新增 **sync lag**
   （`now() - max(src_updated_at)`，未处理集；chat 类未处理 = 
   `processed_updated_at` 落后）与 chatlog 滞留量（积压字节/行数），进
   `get_status`。

**files 表升级（新增列）**：

| 列 | 语义 |
|----|------|
| `src_updated_at` | cs 行 updated_at（增量判定 + 调试） |
| `processed_updated_at` | 上次**成功**处理时的 `src_updated_at` |
| `processed_offset` | jsonl 增量读取游标（字节偏移） |

**chat/meta 状态列语义**：`analysis_status` 恒 `'na'`——「待处理」由
`processed_updated_at` **派生**（NULL 或落后即待处理），不写状态列；仅失败时
写 `analysis_status='failed' + fail_count + error`（成功后回 `'na'`）。否则
`_count_pending_files` 等 health 口径会把 chat 文件错计为积压。实现注意：
**不能复用 `common.mark_file_ok`**（它写 `'ok'`），chat/meta 走独立状态写
函数保持 `'na'` 不变量。

**取件规则（处理层每轮据此选文件）**：

- chat/meta 类：`processed_updated_at IS NULL OR src_updated_at > processed_updated_at`，
  且 `fail_count < 3`；**成功才推进** offset 与 `processed_updated_at`（失败不
  推进 → 下轮重试，直到 fail_count 达 3 停止）；
- audio/video：`analysis_status IN ('pending','failed') AND fail_count < 3`
  （`select_pending_files` 语义，退避下推保持），**移除
  `s.status IN ('active','finalized')` 过滤**（评审裁定：对齐 §2 处理原则，
  idle 会话文件不再滞留至 idle TTL；JOIN 保留——device_uuid/audio_segment_s
  仍取自 recording_sessions）；
- image 不登记（§5.B）。

### 2.3 lvdata 处理层（per-type passes，替代 per-session 链）

每轮 work loop 依次执行固定序列（全部为全局批处理，取件规则见 §2.2）：

| # | Pass | 逻辑 |
|---|------|------|
| 1 | **chatlog** | chat 三文件中满足增量条件的（meta 在同步层即时处理，见 §2.2 步骤 2），从 `processed_offset` 读**增量**行 → events upsert（映射见 §2.5）→ 成功推进 offset/`processed_updated_at`。**offset 只推进到最后一个完整 `\n` 之后**：尾部半行（崩溃截断）不是坏行，残余字节留下轮续读；仅完整行 JSON 解析失败才跳过+告警（不失败文件）。读失败标记 failed、不推进 |
| 2 | **audio** | audio pending/failed<3 → 分析（现有 AudioAnalyzer 机制不变）→ audio 事件 |
| 3 | **video** | video 帧同过滤 → **按 `(device_uuid, 时间戳)` 分组**（评审裁定 H；session 不参与分组——跨 session 的连续帧合并成同一窗口）；window_id = 首帧 stem、window/derived 产物归属**首帧所属 session**（仅存储归属，不影响数据正确性）→ filter/extract（VisionAnalyzer 逻辑不变，分组输入改为全局取件）→ window+video 事件（各 video 事件带自己帧的 session） |
| 4 | **index** | 本轮**内容发生变化**的 event_ref（upsert 内容比对判定）所在 session 触发 FTS 重建（「按 session 重建」只是重建单位/实现细节，非处理语义；复用 `index.rebuild_session`） |
| 5 | **vector** | 同集合（变化事件）做 **per-event 增量** embedding（按 event_ref 删除+重插；`vec.py:193` 已有 `DELETE FROM vec_chunks WHERE event_ref IN (...)`，支持性已验证，评审裁定 A） |
| 6 | **day_summary** | discover 机制不变（`events.updated_at` 游标 + debounce/force）。**输入去重（2026-09-16 §10）**：audio 事件且 `payload.transcript` 非空 → 不进 LLM 输入（文字已由 chatlog 铸造成的 chat/transcribe/translate 事件覆盖）；transcript 空 → 以其 summary 进入；过滤同口径作用于 stats/event_count；**stats 双口径**：`audio_segments`=进入日总结的非语音段数、`audio_segments_total`=当日 audio 事件真实总数 |

错误语义（按需求 3）：**单个文件失败 → 标记 failed + fail_count+1，本轮不重试，
下轮继续**；`fail_count>=3` 停止。预算/并发沿用（`max_files_per_poll`、
`concurrency` 信号量、`stage_timeout_s`）；chatlog 另设单轮每文件处理上限
（行数/字节，溢出下轮续读，offset 已推进），防超大 jsonl 单轮吃满。

### 2.4 watcher 结构变化

- poll loop：inventory 同步（轻量，纯 DB + 变更 session.json 的小文件读）→
  入队一个 **round 标记**（**不携带数据**：处理层所有输入都来自 DB 状态；
  上一轮未消化完时标记自然合并，重复标记等价）。
- work loop：消费 round → 执行固定序列，**分两组**（节流定稿见 §5.C）：
  - **常跑组**：inventory / chatlog / audio / video / **reindex**（FTS 为本地
    重建、零 API 成本；延迟会让新事件在检索中不可见，不节流）；
  - **flush 组**：embed / day_summary——由门控触发（待处理 events ≥
    `flush_min_events`，或最旧待处理事件 ts 超 `flush_max_delay_s`，或
    day_summary 存在非 ok 行，或显式 force）；组内全部成功后才推进
    `flush_state.last_flush_at` 水印（崩溃不推进 → 下轮重触发）。
  `trigger_analysis` 的 stage 集合——**移除 `agent_jobs`、新增 `chatlog`**
  （inventory/chatlog/audio/video/reindex/embed/day_summary；`mcp_pipeline_runs`、
  tools schema、README §12 同步改）；**force 语义按类型分派**：
  - audio/video：`fail_count=0` + `analysis_status='pending'`（同现
    `_reset_session_files`）；
  - chat/meta：`processed_offset=0` + `processed_updated_at=NULL` +
    `fail_count=0`（整文件重放；仅重置 fail_count 不会重新取件——offset 已
    推进）；
  - 均可按 target 限定类型 / 会话。
- **删除**：per-session 队列项、`_session_locks`、`_queued`、`_overrides`（会话级）、
  `detect_dirty`、`finalize_stale_s`。
- 崩溃恢复：files 状态 / processed_offset / 水位全在 DB，重启首轮自然续跑。

### 2.5 事件映射细则（chat 三文件 → events）

| 来源行 | type | source | event_ref | summary / payload |
|--------|------|--------|-----------|-------------------|
| history 普通行 | `chat` | `chat_messages` | `{rs}:chat:{ref_id}` | text（同现状） |
| history `source=agent_job` 行 | `agent_job` | `chat_messages` | `{rs}:agent_job:{job_key}`（见下） | text 全文；payload 含 `agent_name` / `agent_id` / `job_key` |
| transcribe 行 | `transcribe` | `chat_transcribe` | `{rs}:transcribe:{ref_id}` | text |
| translate 行 | `translate` | `chat_translate` | `{rs}:translate:{ref_id}` | text；payload 含行级 `source` 原值（"translate"\|"lecture"，事件 type 统一 `translate`，后续拆型不返工） |

- **event_ref 三段硬约束**：`store.get_event` 要求恰好 3 段且全部非空
  （`store.py:257-262`）——ref_id 为空或含 `:` 时该行事件不可查询，且多个
  空 ref_id 行共享 event_ref → upsert 互覆丢数据。规则：**lvdata 入口清洗**
  ——ref_id 含 `:` 时替换为 `_`；为空时用确定性兜底
  `{kind}_{ts_ms}_{role}`（一行一事件）。上游 ref_id 实际为 uuid 系
  （`job_key` = relay 生成 uuid，兜底 `msg_agent_job_<uuid>`；message_id 为
  `msg_` 前缀），风险低，清洗是防御性措施；
- 幂等：upsert 带内容比对（summary 变化才刷 `updated_at`，同现
  `scanner.py:366-389` 语义），day_summary 水位纪律不变；
- day_summary TYPE_MAP 增加：`transcribe`/`translate` → `chat_messages` 计数
  （stats 结构不变，web 日摘要展示零改动）；
- event_ref 中段 = 该行 type（`store.get_event` 以 `parts[1]` 为 etype）；
- **images 取舍（已知）**：history 行 `images` 不进事件 payload（同现状
  `_upsert_chat_event`，payload 仅 role/text/chat_session_id）。未来事件层
  要带图时，与 §5.B image 登记一起做，不单独返工。

**类型扩展落地（A 裁定）**

- 优先级同步重排（语义：同 ts 展示序，文本类靠前）：
  `chat=0, transcribe=1, translate=2, window=3, video=4, audio=5, agent_job=6`；
  本阶段同步点 = lvdata（consts + store CASE ×2 + tools + tests）+ bff
  （如在役，注意 §1.3 的 ELSE 桶验收点）；web/Android 按 §1.3 范围列处理。
- 备选（保守）：新类型追加为 5/6、旧值不动——排序上 transcribe/translate 落到
  agent_job 之后；推荐上表重排，改动可控。
- **发布顺序（范围裁定后）**：无客户端阻塞——web 已废止；Android 当前只有
  DTO 常量、无类型分派逻辑（后期补）。新类型随 server 端（cs + lvdata，
  bff 视在役情况）一并发布。
- 存量数据迁移：见 §2.7 切换清单（三个 jsonl 全量重放；旧 `agent_job` 事件
  先清理后由 history.jsonl 重建）。

**agent job 行扩展（B 裁定）**

- history.jsonl 的 agent_job 行新增可选字段（`chatlog.py:45-58` 行 dict）：
  `agent_name`（"hermes"|"openclaw"，来源 `delivery.py:34/49 _agent_name` →
  `PushMessage.agent_name`（`models.py:56`）→ publisher）、
  `agent_id`（昵称，来源 = adapter 配置 `adapters.<name>.nickname`，评审裁定 F；
  未配置恒 ""）、
  `job_key`（event_ref 与旧 jobs_watch 事件完全一致（`jobs_watch.py:303`
  `{rs}:agent_job:{job_key}`），切换零重复零迁移；不带则需一次性清理旧
  agent_job 事件）。
- 透传链路：`publisher.py:105-116` → `record_agent_job_result(...)` →
  `orchestrator.py:1942` / `omni_orchestrator.py:792` →
  `agent_job_ingest.ingest_agent_job_result(...)`（`services/session/
  agent_job_ingest.py:51-101`）→ `RecordingSink.on_chat_text(..., agent_name=,
  agent_id=, job_key=)`（`recording/sink.py` 签名扩展）→ chatlog 写行。
- 状态语义：仅投递成功的 job 落行 → 事件天然只含成功结果（原 jobs_watch 的
  `status`/`fail_reason` 字段不再有，已投递即成功）。

### 2.6 移除清单与下游影响

| 项 | 处置 |
|----|------|
| `scanner.py` / fs walk / mtime / staleness / `.part` 过滤 | 删除（cs 单链路直建，无 fs 回退需求） |
| `jobs_watch.py` / `agent_job_links` 表 / pending 机制 | 删除（事件改由 history.jsonl 铸造） |
| lvdata `chat_messages` 表 | **已删除（2026-09-15，修订裁定 G）**：停写后核实无读写方（写方 scanner、读方 jobs_watch enrich 均删）；原文归档 = history.jsonl 本身。DDL/索引移除，存量库由 `migrate()` 幂等 `DROP TABLE` 清理 |
| `list_jobs` 工具 | **保留**（评审裁定 D）：仍直查 `cs.agent_jobs`（只读，独立于同步；未投递 job 的唯一检索入口）。**2026-09-27 反转：工具彻底删除**（见 `docs/data/lvdata_mcp_tools.md`；未投递 job 自此无可查入口） |
| 死 PG 代码 | **已执行（2026-09-15，范围收窄）**：删除 `orm.py` 的 `RecordingFile`/`RecordingSession` 死 ORM + `repositories/recording.py`（零调用方）。**migration 0002 保留**——它是 alembic 链 0001→0002→0003 的中间环（0003.down_revision="0002"），删除会断链且「无环境已应用」无法从仓库侧确认；PG 谱系整体退役时再随清理 |
| `get_status` / tools / index / vec / day_summary / bff | 消费面不变（files/recording_sessions 行形状保持；get_status 新增 sync lag，§2.2） |
| 文档 | **已完成（2026-09-15）**：`lvdata_agent_jobs_integration.md` 与 `chatserver_sqlite_store.md` 均已在文首记录裁决反转；`lvdata_mcp_service_v2.md` 加「已被取代」横幅（Query 平面仍有效） |

### 2.7 分期实施

| 阶段 | 内容 | 备注 |
|------|------|------|
| P0 | 服务端：建表 + 写点 + ChatStore 写方法（含阻塞入队纪律） + backfill CLI + 单测 | 先于 lvdata 落地（lvdata 依赖 recording_files 流） |
| P1 | lvdata：`recording_watch` + per-type passes + 删除 fs 扫盘/旧链路（scanner、jobs_watch、detect_dirty 等，§2.6） | 无开关、无对拍、无灰度 |
| P2 | dev 重建验证：清空 lvdata 库 → backfill 存量文件 → 全链重建 events/index/vec/day_summary，核数 | 数据可弃，重建即回退 |

**dev 重建说明（P2 执行）**：

- 存量兼容一律不做：不清理/对齐旧 `agent_job` 事件、不做 offset 重放窗口
  开关（清库即全量重放）、不做重放成本 dry-run 核数（dev 量级、无计费约束）；
- 重建 = 清空 `lvdata_inventory.db`（数据可弃）→ backfill CLI 登记存量文件 →
  全链处理；三个 jsonl 从 offset=0 全量入 events（transcribe/translate 首次
  embedding、日总结重生成，dev 成本可忽略）；
- 产品上线时（未来）为全新部署，无历史数据迁移路径，本清单不复用。

## 3. 测试计划

- 服务端：写点单测（每种文件写后行存在、size/updated_at 正确；jsonl append 后
  updated_at bump 且严格单调）；backfill 幂等（跑两遍行数不变）；
  **队列灌满**（灌满 ChatStore 队列 → 登记阻塞成功或进 reconcile 清单，
  无静默丢）。
- lvdata：fixtures 数据根 → 全链产出 golden（recording_sessions/files/events
  字段级断言）；**offset 续读**（append 两批只处理增量）；坏行跳过；fail_count
  达 3 停止；崩溃重放（事务回滚后下轮续跑）；静态 session.json 重同步
  （旧格式动态字段摄入但不消费、started_at 归一收敛）；
  **跨 session 连续帧合并**（23:59 与 00:01 的相邻帧 → 同一窗口，session
  不设边界）、**多设备隔离**（两设备帧交错输入 → 不混入同一窗口，裁定 H）；
  **session 行兜底**（仅 cs 文件先到也能进入处理）；
  **重建幂等**（清库全链重跑：无重复事件、水位不抖动）；
  **节流契约回归**（append 后 updated_at 暂未 bump（防抖窗口内）→ trailing-edge
  保证下轮必取到，§2.1 不变式）；**尾部半行**（构造截断尾行 → offset 停在
  半行前，补齐后该行被处理）；**空 ref_id 兜底**（transcribe/translate 空
  ref_id 行 → event_ref 非空且行际不冲突）；**ref_id 清洗**（含 `:` 的
  ref_id → event_ref 仍为三段且 `get_event` 可查）。
- 联调：真实录制一段（含音频轮转、chat 对话、agent job 投递、idle finalize）→
  端到端事件符合预期；重跑幂等。

## 4. 风险与缓解

| 风险 | 缓解 |
|------|------|
| agent_job 行信息量收窄（无 `status`/`fail_reason`；仅投递成功落行） | 已裁定补 `agent_name`/`agent_id`/`job_key`（§2.5）；「仅成功落行」隐含成功语义 |
| 已投递但无录制会话的 agent job 不落行（若存在「连接但不录制」形态，则对比 jobs_watch 时代事件缺失） | delivery 需连接、连接即 acquire session，正常路径闭合；确认 recording 每连接必开（§5.I），否则该场景需裁定 |
| 新旧 agent_job 事件重复 | 不适用：dev 库清空重建，无历史事件；新行仍携带 `job_key`（§2.5），event_ref 命名与旧格式一致 |
| bff `day_has_pending` 无 file_type 过滤 | chat 类文件不写 `uuid_ts_ms`（NULL 不参与 BETWEEN，§2.2 约束 + 回归测试防回退）；未来若需填时间戳须给 bff 加类型过滤 |
| 新事件类型的客户端展示 | web 已废止（不维护）；Android 后期补 EventType 常量与卡片分派——当前无客户端消费方，不阻塞 server 端产出 |
| 优先级重排影响在途分页游标 | 影响可忽略：游标编码 `[ts_ms, type 名, event_id]`（`store.py:245-253`），优先级查询时重算 → 跨版本游标不损坏，仅同 ts 事件翻页中途可能重复/漏一条；同发版即可 |
| jsonl append bump → 写放大 / 水位不可见 | **语义已定为不变式**（trailing-edge + 严格单调 ms + 毫秒格式，§2.1）；仅节流参数待定（§5.E） |
| ChatStore 队列满 → 登记行静默丢弃成孤儿 | §2.1 纪律：阻塞入队 + 失败落 reconcile 清单；backfill CLI 手动 reconcile |
| 孤儿文件（写盘成功未登记，崩溃窗口） | 「先落盘后登记」纪律下窗口极小；backfill CLI 手动 reconcile（不做自动扫盘，符合「只 scan 表」原则） |
| offset 依赖 append-only 契约 | 契约写入表设计文档；offset 越界（文件变小）时重置为 0 全量重读（幂等）；尾部半行不推进 offset（§2.3） |

## 5. 开放问题与评审裁定

**已裁定（2026-09-15 review）**：

- **A. index/vector 更新粒度**：FTS 按 session 重建（复用 `index.rebuild_session`）；
  vector **per-event 增量**——`vec.py:193` 已有按 event_ref 删除，支持性
  验证完毕，无需退化方案。
- **B. image 不登记**：lvdata 不消费、事件 payload 不带图（§2.5 已知取舍）；
  未来事件带图时登记 + payload 一起做。
- **D. `list_jobs` 保留**直查 `cs.agent_jobs`（未投递 job 的唯一检索入口）。
  **2026-09-27 反转**：`list_jobs` 彻底删除（未投递 job 不再可查），见
  [data/lvdata_mcp_tools.md](./data/lvdata_mcp_tools.md)。
- **E. jsonl bump 语义 = trailing-edge + 严格单调 ms**（§2.1 不变式）；仅
  **参数**待定（逐条 bump vs ≤1s 防抖窗）。
- **F. `agent_id`（昵称）来源 = adapter 配置项**（`adapters.<name>.nickname`），
  未配置恒 ""；不经 job 请求透传（避免引入调用方契约）。
- **G. lvdata `chat_messages` —— 修订为「删除」（2026-09-15）**：停写后核实
  无读写方（写方 scanner、读方 jobs_watch enrich 均已删除），原文归档 =
  history.jsonl 本身；表/索引/DDL 一并移除，存量库由 `migrate()` 幂等
  `DROP TABLE` 清理。（原裁定为「停写、保留表与存量、major 再删」。）
- **H. 窗口分组 = `(device_uuid, 时间戳)`**：多设备并发录制时纯时间戳会把
  两台设备的屏幕混进同一次 filter 调用（duplicate/novelty 判定失真）；代价仅
  一列 group key。session 一律不参与分组（§2 处理原则）。
- **取件移除 `s.status IN ('active','finalized')` 过滤**（§2.2）：对齐 §2
  处理原则，idle 会话文件不再滞留至 idle TTL。
- **I. 已核实（2026-09-15）**：「已投递但无录制会话」不成立——
  `gateway/endpoint.py:63-67` 每次 WS 连接即 `recording_service.acquire()`
  （连接即建会话：目录 + session.json），而 job 投递要求连接在线 ⇒ agent job
  落行路径闭合。例外：recording 禁用（acquire 失败回退 NoOp sink，services 侧
  `endpoint.py:68-71`）时 agent job 不落行——属降级模式差异，接受。

**剩余开放**：

- **E 参数**：jsonl bump 逐条 vs ≤1s 防抖窗（语义已定，仅调参；当前实现为逐条）。
- **C. flush 门控（2026-09-15 定稿，已实施）**：round 序列分「常跑组」（inventory/
  chatlog/audio/video/reindex）与「flush 组」（embed/day_summary），后者由门控
  触发，满足任一即跑：
  1. 待处理 events 数 ≥ `watcher.flush_min_events`（默认 10）；
  2. 待处理 events 中最旧 `ts_ms` 距 now ≥ `watcher.flush_max_delay_s`（默认 600s）；
  3. `day_summary` 存在非 ok 且**不在 `day_deferred`** 的行（pending/failed）
     ——失败重试不被门控吞掉；已在 `day_deferred` 中等待 debounce/退避的行
     不算（否则活跃录制期间当前日恒有占位行，门控会恒放行、节流失效）；
  4. 显式 force（trigger_analysis）或 target 限定。
  「待处理」= `events.updated_at > flush_state.last_flush_at`（主库单库 + 索引
  查询；内容变更才刷 updated_at，等价于 embed/摘要的待处理集）；水印由 watcher
  在 **flush 组全部成功**后写 `flush_state`（专用小表；半途崩溃不推进 → 下轮
  重触发，崩溃安全）。门控在**常跑组之后评估**——本轮 chatlog/音视频新产出的
  events 立即计入（对齐"每轮产出新 events → 触发"的会话粒度）。门控未放行的
  轮：flush 组不执行，同时省去 embed 的每轮全表对账（每轮成本降为一次计数查询）。
  **明确不做 bypass**：收尾日（停录日）的"及时生成"由 debounce 首要保障
  （每日本身计时的 `summarize_quiet_s` 与条件 2 同为 10 分钟量级，可配小）；
  `day_force` 已随 session_json_refactor 裁决 A 删除。设备时钟偏移（未来 ts）
  由条件 1 兜底；跨天由 2+3 覆盖。

## 6. 工作量估计（粗）

| 项 | 规模 |
|----|------|
| 服务端（表 + 写点 + ChatStore 接口 + backfill + 测试） | 0.5–1 人周 |
| 服务端（agent job 行字段透传：publisher → sink → chatlog） | 0.5 人周 |
| lvdata（recording_watch + per-type passes + 事件映射 + 测试） | 1 人周 |
| 事件类型联动（server 端：lvdata 类型表/排序 + bff 如仍在役 + 测试） | 0.5 人周 |
| Android 适配（EventType 常量 + 卡片分派） | **后期**排期 |
| 联调 + dev 重建验证 | 0.5 人周 |
| 文档修订 | 0.5 天 |
