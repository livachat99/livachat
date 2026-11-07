# 音频段切段改为「墙钟兜底」：修 mute 空洞导致的段 ts 失真

> 来源：2026-09-25 用户实测——日卡 audio 入口 1 段（显示 12:07），真实说话在 13:11（**差 64 分钟**）；
> 点该段时间头跳 EventListViewer → 「该时段没有可展示的内容」（±10min 窗口内无五类事件）。
> 全部事实取自当前代码（含 file:line），未做推测。
> 状态：**已实施（2026-09-25，E1–E9 定案 / E10 否决）——代码 + 单测完成，真机验证待跑（§9）**。
> 2026-09-25 review：核对全部 file:line 属实；补三处严重缺口——① 收尾状态归零/共用收尾函数（§4-0）、
> ② E6 与 relay 10s 门槛 + 会话级去重的联动（R6/E8）、③ payload `duration_s` 是配置值而非内容时长（R7/E7）。
> E7/E8 定案：E7 分析侧改取实际内容时长（§4-9；三轮 review 改向为**实测 ogg granule**）；E8 墙钟关闭不 publish（relay 侧零变化）。
> **二轮 review（2026-09-25）——发现严重竞态**：内容条件与墙钟条件**构造上只差约 1 个包时长**（3000×20ms：
> 内容满在 59.98s，定时器在 60.00s）。节拍抖动 ≥1 包 / writer 排队滞后 / 采集时钟慢 0.1% 都会让墙钟先到 ⇒
> 段不再经「内容满」路径 ⇒ **`recording.ready` 静默停发**（E8 下 = 该会话 relay proactive 静默失效，无告警）。
> 修：墙钟阈值 = 段长 + **余量**（E9 定案）。另：段内空洞（「说 30s→停 40s→再说」）是同一 bug 的缩小版
> （残差 ≤ 段长 + 余量）——「包间隔切段」提案（E10）**已否决**，保持现状、记入已知限制（R4）。
> **三轮 review（2026-09-25）——E7 改向 + 五项补强**：
> ① E7 原方案读 `files.duration_ms`，但该列在 lvdata `files` 表**不存在**（基表 `ddl.py:211-221` 与
> `FILES_ALTERS :159-171` 均无，cs→lvdata sync 也不同步）——照原文实施 `row.get()` 恒 None = **静默失效且单测可绿**
> → 改向为分析侧**实测 ogg granule**（E7′，§4-9：零 DDL、不动 sync、无迁移，backfill/存量行亦得真值）；
> ② 内容阈值 `session.py:344` 硬编码 `60_000` 补入清单（§4-4b）——非默认段长下缺失它直接反转 E9 不变式
> （如 config=30s ⇒ 墙钟 36s 恒先于内容 60s ⇒ publish 静默停发，R8 被配置变更完整兑现）；
> ③ E8 论据补强：碎片对 relay 是**双通道污染**——`last_proactive_at` 在 duration 门槛**之前**更新
> （`session_manager.py:263-264` vs `:266`），被 10s 门槛挡掉的碎片也已吃掉 30s debounce 窗口；
> ④ 余量单位陷阱显式化（常量 ms vs `call_later`/monotonic 秒，§4-2/§4-4）+ 补单位断言用例（§6-1k③——既有用例抓不到 ×1000 bug）；
> ⑤ 墙钟关闭加 reason 遥测（§4-5/R8：wall_clock 占比 >0 即抢跑告警信号，从「静默」变「可观测」）。

---

## 1. 问题与根因

### 1.1 现象

| 观测 | 值 |
|---|---|
| 日卡 09-25 counts | `{audio: 1, chat: 2, agent_job: 1}`；唯一 hot_slot = 13:11:41–13:13:38 |
| audio 段（files/事件）时间 | **12:07:50** |
| 真实说话时刻 | **13:11:41**（同日的 chat 事件时刻） |

### 1.2 根因链（代码事实）

| 环节 | 行为 | 位置 |
|---|---|---|
| 段创建 | **首包到达时惰性创建**（无包不建段） | `services/recording/session.py:335` |
| 段文件名 | `f"{uuid7()}.ogg.part"` → uuid7 内嵌**创建时刻** | `session.py:561` |
| 登记时间戳 | `ts_ms=uuid7_to_ms(path.stem)` → files 行 ts = **段创建时刻** | `session.py:535` |
| 事件 ts | 取 `row["uuid_ts_ms"]` | `tools/lvdata-mcp/src/lvdata_mcp/analysis/audio.py:367`、`:456`、`:518` |
| **切段条件** | `_audio_segment_ms += item.duration_ms`，**累计 ≥60_000 才切——纯「已写入音频时长」驱动，无任何墙钟条件** | `session.py:341`、`:344` |
| 空洞处理 | OGG 只按包累计 granule，**不插静音** → 空洞在文件时间线里被压缩掉 | `services/recording/ogg.py:52-62` |
| 客户端 mute | `SilenceFlushGate`：mute 沿后补发 **1s 全零静音**，窗口耗尽后**完全停发** | `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/media/SilenceFlushGate.kt:7-17`（约定）、`:19`（`flushFrames=50`） |
| 断链 | WS detach 会 close 段（`session.py:189-196`）→ **只有「长连接 + mute」才会中招** | |

**时间线示意（本次实测那条）**：

```
墙钟  12:07:50 ──────── mute：无包（64 分钟） ──────── 13:11:41 ─── 13:12:41 …
上行  20ms 帧 × N + 1s 静音尾            停发                   恢复真实包
段    ├── 段#k 创建（uuid7=12:07:50）──────────────────────────────────┤ 累计满 60s 才切
文件  12:07:0x…12:07:5x（+1s 静音）‖ 13:11:41…      ← 空洞被压掉，两点在文件里「挨着」
事件  ts = 段#k 的 uuid7 = 12:07:50                 ← 与真实说话差 64 分钟
```

→ **段 ts 与段内容脱节，误差上界 = 静音空洞长度（无界）**。

### 1.3 影响面

- audio 事件 ts（检索/日卡计数分布/能量包络锚点）；
- audio gallery 时间头 → ±10min EventListViewer 空窗（本次实测即此）；
- hot slot 对齐观感（hot_slots 本身不含 audio，是既有设计，非本问题）；
- **video 不受影响**（逐帧 `uuid7()` = 到达时刻）；chat 由 jsonl 落盘时刻决定，亦不受影响。

---

## 2. 目标 / 非目标

**目标**：段 ts 恢复可信（误差有界且小）；mute 期间段按时落地（事件循环健康前提下 ≤ 段长 + 余量，不再挂数十分钟；阻塞情形见 R2）。

**非目标**：不做静音填充（mute 期间仍未录到音频）；**不改客户端**（服务端自治）；不改 hot_slots 的类型口径；
不在本批做「段内首个有效帧偏移校正」（E5，另立方案）。

---

## 3. 方案

### 3.1 切段规则：**两者取先到**（E1）

```
close when (内容累计_ms >= 段长_s × 1000)  OR  (墙钟_s since 建段 >= 段长_s + 余量_s)
```

> 单位（三轮 review 显式化，见 §4-2 单位陷阱）：内容累计是 **ms**、`audio_segment_s` 是 **秒**、余量常量是 **ms**。

- 保留内容条件：维持既有「段内音频 ≤ `audio_segment_s`」约束（包长/速率异常时可能先满 60s）；
- 新增墙钟条件：空洞/包稀疏时先到——**本问题要修的场景**；
- 墙钟项**必须带余量**（E9 定案：`_AUDIO_WALL_BACKSTOP_MARGIN_MS = 6_000`）：两者构造上只差约 1 个包时长
  （60s 段 = 3000 包 × 20ms → 内容满在 59.98s，墙钟在 60.00s），任何抖动/排队滞后/时钟偏斜都会让墙钟先到；
  而 publish 只在内容满路径发（E8）→ 抢跑 = 该会话 relay proactive 静默失效（无告警、无日志，只能靠 agent_job 计数缺失发现）；
- 关段后**不自动开新段**：仍由下一个包惰性创建（`session.py:335` 不变）→
  **永久 mute 时只在 mute 前那一段落地一次，之后完全静默，不产生空段**。

**修后行为（本次实测场景）**：12:07:50 建段 → 12:08:56（= 段长+余量）定时关闭；13:11:41 的话落进**新段**
（uuid7≈13:11:41）→ 事件 ts ≈ 13:11（误差 = 首包距真实发声 ≤1 帧）✓。

### 3.2 定时器（E2）

- `loop.call_later(audio_segment_s + 余量, cb, segment_index)`，**以段序号作身份**；回调先校验
  `_ogg_writer is not None and self._audio_segment_index == idx`，否则 no-op（幂等）；
  段序号只增不减 → `attach()` 丢弃段后残留的句柄同样被索引校验挡掉，不会误关新段；
- 回调 = 调用**与包路径共用的收尾** `_finish_audio_segment()`（契约见 §4-0），只关闭+登记、
  **不建新段**；登记走既有 async 包装 `_register_media_bg`（`session.py:548-557`）；
- **收尾必须把 `_audio_segment_ms` 归零**（并 `audio_segments += 1`）：漏归零会跨段串味——
  下一段的 `duration_ms` / 切段点被上一段内容顶高（极端时产生 20ms 级小段）；
- `_close_audio_segment()` 内 **cancel + 清空定时器**（cancel 放在 `_ogg_writer is None` 短路**之前**，
  否则「无 writer」分支会漏 cancel）；detach / `_flush_all` 因此自动覆盖，无需各写一遍；
- **包路径的墙钟检查是定时器被事件循环延迟时的兜底**（见 §4-4）：两个触发源都必须幂等——
  这是最容易写错的地方（迟到回调不得误关「下一段」）；
- **余量取值（E9）= 6s**：需 > writer 单次阻塞量级（登记入队超时 5s，`chat_store.py:176`）+ 留 ~10%
  采集时钟偏斜余量；它是「内容路径优先」的保证，故**入口墙钟检查用同一阈值**（§4-4）。

### 3.3 时长语义

- `recording_files.duration_ms`（cs 库）维持**内容时长**（`self._audio_segment_ms`）——墙钟跨度可 > `audio_segment_s`（含空洞），两者不再相等；
- `recording.ready` 的 `duration_ms` 修正为实际内容时长（E6，见 §4-7）；
- `recording.ready` 的**发送范围**（E8 定案）：只有「内容满」路径 publish，墙钟关闭**不** publish
  （与 detach/flush 同口径；理由：relay 以 `recording_session_id` 去重，短碎片会挤掉该会话唯一名额）；
- 分析侧 payload `duration_s` 改取**实际内容时长**——分析时解析 ogg granule 实测（E7′ 定案，见 §4-9）。

### 3.4 配置来源（E4）

`RecordingConfig.audio_segment_s`（`services/shared/config/models.py:223`，默认 60）——
session 已持有 `config: dict = RecordingConfig.model_dump()`（`session.py:70`、`service.py:194-218`），
直接 `self.config.get("audio_segment_s", 60)`（取值非法 → 回退 60：`<=0` 会让 `call_later(0)` 退化成一包一段；
**非有限**（YAML `.inf` / JSON `1e309`）会让 `int(inf)` 在 `_write_session_json` 与包路径双双抛
OverflowError——前者使 `start()` 失败 ⇒ 整设备录制起不来，见 §9 review 修正）；
同时订正**两处**硬编码：内容阈值 `session.py:344` 的 `60_000`（§4-4b，三轮 review 补入）与会话配置回执里的
`"audio_segment_s": 60`（`session.py:632`），**单一出处**。

---

## 4. 实现清单（file:line）

| # | 改动 | 位置 |
|---|---|---|
| 0 | **新增 `_finish_audio_segment(reason: str) -> tuple[Path, int] \| None`（同步收尾契约，R1 的落点）**：`_close_audio_segment()`（含 cancel timer）→ `ms = _audio_segment_ms` → `_audio_segment_ms = 0` → `ms > 0` 则 `audio_segments += 1` → `_audio_segment_opened_at = None`（卫生；writer-None 短路本已兜住）→ 按 reason 打遥测 → 返回 `(path, ms)`（reason 由收尾方传入，遥测与归零同处一地，R8）。**五个关闭点全部改走它**（内容满 / 入口墙钟 / 定时器 / detach / `_flush_all`），调用方各自决定 `await _register_media(...)` 还是 `_register_media_bg(...)`；登记统一 `duration_ms=ms or None`（与 detach/flush 现口径一致；内容满路径 `ms ≥ 段长 × 1000`，默认 60_000，行为不变） | 新方法，`_close_audio_segment` 旁（`session.py:571`） |
| 1 | 新增字段：`_audio_segment_opened_at: float \| None`（**monotonic**，免疫 NTP 跳变；取 `time.monotonic()`——与既有 `_detached_at`（`session.py:85`）同源，CPython 下与 `loop.time()` 同一时钟）、`_audio_timer: asyncio.TimerHandle \| None`；模块常量 `_AUDIO_WALL_BACKSTOP_MARGIN_MS = 6_000`（E9） | `session.py:88-95`（Audio state 区） |
| 2 | `_rotate_audio_segment()`：建段后记 `opened_at`，启动 `loop.call_later(audio_segment_s + _AUDIO_WALL_BACKSTOP_MARGIN_MS / 1000, cb, 新段序号)`——**注意单位**（三轮 review）：`call_later`/`monotonic` 是**秒**、余量常量是 ms，直译 `段长 + 6000` ≈ 1.7h 永不点火；覆盖 `self._audio_timer` 前**防御性 cancel 旧句柄**（「rotation 时无 pending timer」依赖全部关闭路径都 cancel，一行防御换不显式依赖该不变式） | `session.py:559-570` |
| 3 | `_close_audio_segment()`：**先** cancel + 清空 timer，再走既有 `_ogg_writer is None` 幂等短路 | `session.py:571-586` |
| 4 | `_handle_audio()` 入口新增墙钟检查：`_ogg_writer` 存在且 `now - opened_at >= audio_segment_s + _AUDIO_WALL_BACKSTOP_MARGIN_MS / 1000`（**秒**，单位同 #2）→ 先 `_finish_audio_segment()` 收尾（登记同内容路径、**不发** `recording.ready`，见 E8），再由下方惰性建新段写包。**与定时器必须同阈值**，否则两条兜底路径语义不一致（E9） | `session.py:325-340` |
| 4b | **E4 补全（三轮 review 缺口）**：内容条件 `>= 60_000` 硬编码 → `>= 段长 * 1000`（与定时器/入口检查同源取值）。默认 60 无行为差异；但 config ≠ 60 时缺失本行直接反转 E9 不变式——如 `audio_segment_s=30` 则墙钟 36s 恒先于内容 60s → 内容满路径永不发生 → publish 静默停发（R8 被配置变更完整兑现，E9 余量失效） | `session.py:344` |
| 5 | 新增定时器回调方法：身份校验 → `_finish_audio_segment()` + `_register_media_bg` 登记，**不发** `recording.ready`（E8），不建新段；回调整体 try/except + 告警——close 抛异常（磁盘满）时既有契约保留 `_ogg_writer` 引用，重试由下一包 / 入口墙钟检查接管（定时器本身不重排）。回调内 close 是同步磁盘 I/O（flush+EOS），与既有 detach / `_flush_all` 同构同级，可接受。**遥测（三轮 review，R8 缓解）**：各关闭路径按 reason（`content_full` / `wall_clock` / `detach` / `flush`）打 `logger.info`（段序号、内容 ms、墙钟跨度）——wall_clock 出现即抢跑信号，把 R8 的「只能靠 agent_job 计数缺失发现」变为可告警。**本批只打日志、不新增 counter**：recording 服务现无 metrics 接线（`services/recording/*` 无相关 import），且既有 `recording_segments_total` 的 label 只有 `kind`（`shared/observability/metrics.py:140`），按 reason 分解须改 label 或加计数器 → 需要长期比例时另立 metrics 提案 | 新方法 |
| 6 | `attach()` 丢弃当前段时同步 cancel timer（索引校验已保证不误关，cancel 只为不留悬空句柄 ≤ 段长+余量）并清 `_audio_segment_opened_at`（卫生）；detach / `_flush_all` 由 #3 覆盖，无需各写一遍 | `session.py:171-172` |
| 7 | **订正既有 bug**：`publish_recording_ready(duration_ms=60_000)` 硬编码 → 传实际 `ms`。**下游联动（review 新增）**：`duration_ms` 正是 relay 的 `proactive.min_segment_duration_s`（默认 10）门槛输入（`services/agent_relay/session_manager.py:266`）——今天恒 `60_000` 使该门槛形同虚设，改真值即**激活**它（<10s 段不再触发 proactive）→ 故 publish 收窄到内容满路径（E8），使已 publish 段恒 ≥ 段长（默认 ≥60s）、relay 侧零变化 | `session.py:353-363` |
| 8 | 会话配置回执（session.json `config.audio_segment_s`）硬编码 60 → 读配置值 | `session.py:632` |
| 9 | **E7′（三轮 review 改向）**：分析侧 `duration_s` 改取**实测内容时长**。新增 `ogg_content_duration_s(path) -> float \| None`：顺序扫 OGG 页取最后有效 granule，`(granule - pre_skip) / 48000`（RFC 7845）——不插静音 ⇒ granule 即内容时钟（`ogg.py:60-62`）；顺序扫描天然抗截断，**找不到任何 `OggS` 页才返回 None**（页存在但 granule=0 是合法实测值，别再按真值判断）。**pre-skip 来源**：别跨包 import `recording.ogg._OPUS_PRE_SKIP`（lvdata 工具与 recording 服务是两个包）——直接从文件 OpusHead 页读 pre-skip（writer 正是把该常量写进 OpusHead，`ogg.py:224-235`）；减它使 `duration_s` 与播放器口径一致，与 cs `recording_files.duration_ms`（未减）差 6.5ms，round 后不可见。两处审计同表达式：`d = ogg_content_duration_s(row["file_path"])` → `duration_s = round(max(d, 0.0)) if d is not None else _coerce_segment_s(row.get("s_audio_segment_s"))`——**必须 `is not None` 判断**：空段 `d = 0.0`（合法）与解析失败 `None` 语义不同，用真值判断会把「实测 0」误判为缺失回落 config，负 pre-skip 也会 round 出非预期值；回落值须经 `_coerce_segment_s`（非数 / 非有限 / <=0 / 缺失 → 60，`int(inf)` 会炸整批分析）；`round()` 到秒 ⇒ <0.5s 段为 0（App 显示「0:00」，轨道分度由播放器实测覆盖，可接受）——**空文件审计 `:371` 也要改**（mute 尾段 ~1s 正走 `file_size < EMPTY_AUDIO_SIZE` 分支，不改它「1:00」还留一个缺口）；正常审计 `:460`。类型保持 int（round，wire 口径不变，App DTO 是 `Double?` 不吃亏）。payload 经 `doc["duration_s"]`（`:479`）自动跟随；payload 存 JSON 文本（`common.py:124`）→ **零 DDL、不动 sync、无迁移**，backfill 登记的行同得真值（**已分析的历史行不会自动更新**——要 `force` 重跑才生效，本批不主动回填）。**原 E7（读 `files.duration_ms`）已否**：lvdata `files` 表无该列——基表 `ddl.py:211-221` 与 `FILES_ALTERS`（`:159-171`）均无，cs→lvdata sync 也不同步（`_UPSERT_FILES_SQL`，`recording_watch.py:64-74`）；照原方案 `row.get("duration_ms")` 恒 None → 静默失效且单测可绿。同步 `docs/android_timeline_audio_gallery.md:52` 的来源说明（旧对照工具 `tools/data-analysis/lvdata_analyze_audio.py:521`、`:680` 同口径，不在线上链路，另案） | `analysis/audio.py:371`、`:460` + 新工具函数 |

> 单测兼容：既有测试用 `config={}` 构造 session（`services/tests/unit/recording/test_recording_session.py:25`），
> 故段长取值必须 `self.config.get("audio_segment_s", 60)` 带默认；内容满路径的既有断言
> （`test_file_registry.py:145`：`duration_ms == 60_010`）在 #0 后必须保持不变。

---

## 5. 语义变化与影响面

| 维度 | 变化 |
|---|---|
| 段 ts（files/事件） | 口径不变（= 段首创建时刻），**空洞后首包 ≈ 0 误差**（新段 ts = 恢复时刻）；**段内**空洞仍压缩 → 残差 ≤ 段长 + 余量（R4，已知限制，E10 已否决） |
| 段数 | 连续录音不变；**空洞会话增多**——极端（每说一句就 mute）退化成「一句一段」 |
| 分析成本 | 每段一次音频分析（LLM）→ 段数增量即成本增量（略低于此：`file_size < EMPTY_AUDIO_SIZE`(1024B) 的空/极静段直接判空跳过、不打 API，`analysis/audio.py:359`）。**E3 定案：先全部分析并观测**，不设最小内容门槛（门槛会丢极短语音事件，留待有数据后另议） |
| 段落地时效 | ≤ `audio_segment_s + 余量`（E9 = 66s）必然落地（事件循环健康前提，见 R2；原来可能要等 session 关闭 / 下一个包）→ **文件登记 + 分析**更及时（分析取批 = DB 扫描 `select_pending_files`，**不依赖** `recording.ready`）；`recording.ready` 范围见 E8 |
| 下游 UI | gallery 时间头、±10min EventListViewer、hot slot 对齐观感同步改善 |
| 能量包络 | 横轴仍是**内容时间**（段内空洞仍被压缩，残余偏差 ≤ 段长 + 余量）；要按墙钟需客户端补静音（§8-E5 之外的另一条路，未列入本批） |
| App 段卡片时长 | 现状 payload `duration_s` 来自**会话配置值**（`s_audio_segment_s or 60`，`analysis/audio.py:460`、`:479`）→ 墙钟关闭的短段在播放器就绪前显示「1:00 / 1:00」、进度条按 60s 分度（`MylifeRepository.kt:270` → `AudioSegmentCard.kt:57`）。**E7′ 定案**：分析侧实测 ogg granule（§4-9）→ 短段显示真值（含 backfill/存量行） |
| relay proactive | 若墙钟关闭也 publish：激活 10s 门槛（R6）**且**抢占其会话级去重名额（`_mark_processed(recording_session_id)`，`session_manager.py:252`）→ 一个短碎片可挤掉整段会话的 proactive。**E8 定案**不 publish ⇒ 本批 relay 侧零变化。**但前提是内容满路径仍会发生**（E9 余量就是为此：墙钟抢跑 ⇒ 永不 publish ⇒ proactive 静默失效） |
| mute 期间「没录到」 | 不变（无静音填充） |

---

## 6. 测试计划

1. **单测（真实事件循环，不真等 60s）**：session.config 是普通 dict，测试传极小段长即可
   （如 `{"audio_segment_s": 0.05}` + `await asyncio.sleep(...)` 让 timer 真点火）；包路径的墙钟检查
   则「构造后回拨 `_audio_segment_opened_at`」直接触发（`loop.time` 不可注入，别为此造假时钟）。
   **余量（E9）是模块常量**——测试 `monkeypatch.setattr(recording.session, "_AUDIO_WALL_BACKSTOP_MARGIN_MS", 0)`
   才能让极小段长的定时器按时点火（否则 6s 余量会拖慢每个用例）：
   a. **空洞场景**（本问题回归）：建段 → 无包推进 ≥ 段长 → 定时器关闭并登记 → 之后首个包建新段，新段 uuid7 = 该包时刻；
   b. 正常满内容：内容先到 → 关闭 + timer 取消（迟到回调不误关下一段）；
   c. 竞态：timer 到期瞬间恰有包 → 只关一次（幂等）+ 包进新段；
   d. flush / detach：timer 取消、段落地、无 fd/任务泄漏；attach 后残留句柄 fire 时索引校验为 no-op；
   e. `recording.ready` 的 duration = 实际内容时长（非 60_000）；
   f. 永久 mute：只有 mute 前那段落地，之后**不产生空段**；
   g. **收尾归零回归（#0 契约）**：定时器关闭后，下一段的 `duration_ms` / 切段点只按自身内容
      （防 `_audio_segment_ms` 串味；构造「定时器关掉 S=59_980 的段 → 下一段首个包」即暴露旧写法）；
   h. **发布口径**：按 E8 定案断言 publish 只在**内容满**路径发生（墙钟关闭 / detach / `_flush_all` 均不 publish）；
   i. relay 门槛（relay 侧单测，非 session 测试）：`duration_ms < min_segment_duration_s*1000` 不触发 proactive；
    j. **E7′**（lvdata 侧单测）：构造已知内容时长的 ogg → 分析 `duration_s` = 实测值（非 config）。
       **样本要 ≥1s**（如 150 包 × 20ms ≈ 3s → 期望 `3`；实现退化成 config 得 60，断言才区分得开；
       0.4s 级样本 round 后是 0，别用）；截断/畸形文件（无 `OggS` 页）→ 解析失败回落 `s_audio_segment_s`；
       空文件分支（< `EMPTY_AUDIO_SIZE`）同样走实测（得 0）而非直接写 config；
       另给 `ogg_content_duration_s` 自己的用例：末页 granule 取值、减 pre-skip、OpusHead 缺 pre-skip 的畸形头；
    k. **墙钟抢跑回归（E9，二轮 review 核心用例）**：① **默认余量** + 极小段长 + 正常喂包（内容 0.05s ≪ 0.05+6s）
       → 断言**内容满路径先到**且 publish 发出；② 余量置 0 + 放慢喂包节拍（内容增长慢于墙钟）→ 断言墙钟先到、
       **不** publish（锁死 E8 口径，同时证明 E9 余量是 publish 的保证）；③ **单位断言（三轮 review）**：
       直接断言 `call_later` 收到的 delay 实参 ≈ 段长 + 余量/1000（秒）——用例 a–d 把余量 monkeypatch 为 0、
       k① 在 ×1000 bug 下依然绿，二者都抓不到单位错误，必须显式锁。
2. **集成/真机**：mute 数分钟后说话 → `files` 行 ts ≈ 说话时刻；gallery 时间头正确；EventListViewer ±10min 不再空窗；段时长/边界与现状（连续录音）一致。

---

## 7. 风险

| # | 风险 | 缓解 |
|---|---|---|
| R1 | timer 回调 vs 包路径竞态（双 close / 误关新段） | 段序号身份校验 + 既有 close 幂等；两触发源共用同一收尾函数 `_finish_audio_segment()`（§4-0） |
| R2 | 事件循环繁忙 → timer 晚点火 | 包路径墙钟检查兜底（正常情形下误差仍 ≤1 帧）。**前提（三轮 review）**：入口检查在 writer **出队时刻**判定、与 timer 共用同一 loop——循环阻塞时两者都晚，落地时延 = 段长+余量+阻塞时长（无上界）；「≤ 段长+余量」以事件循环健康为前提（余量 6s > 入队超时 5s 兜住正常路径） |
| R3 | 段数/LLM 分析成本上升（mute 频繁场景） | lvdata 侧先按 E3 观测；必要时再评估最小段门槛/批量分析 |
| R4 | 残余误差：**段内**空洞导致语音位置未知（≤ 段长 + 余量） | **定案：记入已知限制，保持现状**。段内空洞（「说 30s→停 40s→再说」）与本问题同源、只是量级小（对 ±10min EventListViewer 够用）；本轮提的「包间隔切段」（E10）已否决，不做新增切段规则。如需精确到秒，走 E5（段内偏移校正，另立方案） |
| R5 | 下游若把「duration ≈ 60s」当不变量 | **grep 已做（2026-09-25，结论）**：cs `recording_files.duration_ms` 全仓**无读取方**（只写不读；注意区分三张表——cs `recording_files` 有该列、lvdata `files` **无**该列、lvdata `recording_sessions.duration_ms` 是 session 级且 `recording_watch.py:273` 显式写 None）——E7′ 后仍**无读取方**（分析侧改走文件实测，不读该列）；`recording.ready.duration_ms` 唯一读取方是 relay 门槛（R6）；payload `duration_s` 原读 session 列配置值（R7/E7′） |
| R6 | E6 改真值**激活** relay 的 10s 门槛：墙钟驱动的短碎片会让 proactive 少触发/不触发 | **E8 定案**不改 publish 范围（墙钟关闭不 publish）→ 已 publish 的段恒 ≥ 段长（≥60s），relay 行为与今天一致。残余≈零回归：今天「内容未满但墙钟到期」的段根本不关闭（也就没有 publish），差别只是空洞前那 ~1s 碎片不再计入首个 publish 段的 60s 内容。真正威胁 publish 的是 R8（墙钟抢跑），由 E9 余量挡住。另（三轮 review）：碎片对 relay 是**双通道污染**——`last_proactive_at` 在 duration 门槛**之前**更新（`session_manager.py:263-264` vs `:266`），即使被 10s 门槛挡掉的碎片也已吃掉 30s debounce 窗口（`config.py:95`）→ E8 结论更硬。relay 侧改段级去重键 + 门槛语义**另立方案** |
| R7 | 短段/空洞段的 App 展示时长（payload `duration_s` = 配置值）与实际内容不符 | **E7′ 定案（三轮 review 改向）**：分析侧实测 ogg granule（§4-9）→ 展示口径与内容一致，backfill/存量行同得真值；仅解析失败回落 config。**原 E7（读 `files.duration_ms`）已否**：lvdata `files` 表无该列（`ddl.py:211-221` + `FILES_ALTERS :159-171`；sync 不带，`recording_watch.py:64-74`），照原方案静默失效 |
| R8 | **墙钟抢在内容条件之前**（包节拍抖动 ≥1 包 / writer 排队滞后 / 采集时钟偏斜）→ 段不经内容满路径 → 该会话 `recording.ready` **静默停发**（relay proactive 无声失效） | **E9 余量 6s**：> 登记入队阻塞 5s（`chat_store.py:176`）+ 10% 时钟偏斜余量；构造上把「内容条件先到」从 1 包余量抬到 ~10%。残余：偏斜 >10% 或 ≥5s 阻塞恰好压在 deadline 上仍可能抢跑，余量是唯一旋钮；**遥测兜底（三轮 review）**：关闭路径按 reason 打点（§4-5），wall_clock 占比 >0 即抢跑告警信号，把「无告警、无日志」变为可观测；彻底解耦可把 publish 判据从「走内容路径」改为「该段内容 ≥ 段长」（与关闭路径无关，但会让 detach 关闭也 publish，超出 E8 口径，本批不做） |

---

## 8. 决策记录（2026-09-25：E1–E9 定案；E10 否决）

| # | 决策 | 说明 |
|---|---|---|
| E1 | 关闭规则 = **两者取先到** | 保留内容上限约束，新增墙钟兜底 |
| E2 | 定时器 = `loop.call_later` + 段身份校验 | 比「每段一个 task + sleep」更轻；身份校验是正确性前提 |
| E3 | 极短段**全部分析**，先观测成本 | 不设最小内容门槛（门槛会丢极短语音事件，待数据后再议） |
| E4 | 段长来源 = `RecordingConfig.audio_segment_s` | 单一出处；同时订正两处硬编码（内容阈值 `:344` = §4-4b、回执 `:632`） |
| E5 | **非本期**：事件 ts 校正 = 段首 + 段内首个有效帧偏移（误差压到 ≤1s） | 需改分析侧 ts 语义，另立方案 |
| E6 | `recording.ready` 的 duration 修正**随本批一起改** | 属同批 bug（publish 恒 60_000；墙钟切段后内容时长可 < 段长，默认即 <60s） |
| E7 | **定案（review 新增；三轮 review 改向为 E7′）**：分析 payload `duration_s`（App 段卡片总长/进度条分度）改取**实际内容时长**——机制 = 分析侧解析 ogg granule 实测（§4-9），解析失败回落 config | §4-9。理由：短段也能显示真值，避免「1:00 / 1:00」。**改向原因**：原方案读 `files.duration_ms`，但 lvdata `files` 表无该列（`ddl.py:211-221`、`FILES_ALTERS :159-171`；sync 不同步）——照原文实施 `row.get()` 恒 None，静默失效且单测可绿；实测路线零 DDL / 不动 sync / 无迁移，backfill/存量行同得真值。需同步 `docs/android_timeline_audio_gallery.md:52`（该文已记「可被 LLM 多输出的同名键覆盖」，改后同规则） |
| E8 | **定案（review 新增）**：墙钟关闭**不**发 `recording.ready`（只有内容满路径 publish，与 detach/flush 同口径） | 理由：relay 用 `recording_session_id` 去重（`_mark_processed`，每 session 首个 ready 生效，同 session 后续基本被丢弃——去重表 LRU 上界 10k）且 10s 门槛——多发短碎片会挤掉该会话唯一名额，且 `last_proactive_at` 在门槛判定**之前**更新（`session_manager.py:263-264`），被门槛挡掉的碎片也已污染 30s debounce 窗口（双通道污染，三轮 review 补强）；分析时效靠 `_register_media` 的 DB 登记已够（分析取批是 DB 扫描）。选「也发」须 relay 侧先把去重键改成段级（另立方案） |
| E9 | **定案（二轮 review）**：墙钟兜底阈值 = `audio_segment_s` + **余量**（`_AUDIO_WALL_BACKSTOP_MARGIN_MS = 6_000`）；定时器与入口墙钟检查**同阈值** | 理由：内容条件与墙钟条件构造上只差约 1 个包时长（3000×20ms：59.98s vs 60.00s），抖动/排队滞后/时钟偏斜都会让墙钟先到；而 publish 只在内容满路径发（E8）⇒ 抢跑 = relay proactive 静默失效（无告警）。6s > 登记入队超时 5s（`chat_store.py:176`）且 = 10% 偏斜余量。代价：空洞段落地推迟到 ≤ 段长+6s（目标只是「不再挂数十分钟」） |
| E10 | **否决（2026-09-25，二轮 review 提案）**：不新增「包间隔切段」（`_handle_audio` 入口按 `now - _last_packet_at` 判据先收尾再建新段） | 收益是段内空洞残差 ≤ 段长 → ≤ 间隔阈值；**否决理由：超出本批范围（新增切段规则）且会增加段数 = E3 成本，保持现状**。段内空洞残差照 R4 记入已知限制；如需精确到秒走 E5（另立方案）。实现不再涉及 `_last_packet_at` |

---

## 9. 实施记录（2026-09-25，按 §4 清单 0→9 + E9 全部落地）

**代码**（3 个文件）：
- `services/recording/session.py`：§4-0 收尾契约 `_finish_audio_segment(reason)`（五个关闭点全改走它，遥测在契约内）、§4-1 字段/常量（`_AUDIO_WALL_BACKSTOP_MARGIN_MS = 6_000`）、§4-2 定时器（`call_later(段长 + 余量/1000)`，单位换算显式）、§4-3 cancel 前置、§4-4 入口墙钟检查（同阈值）、§4-4b 内容阈值取配置（`int(self._audio_segment_s() * 1000)`）、§4-5 回调（身份校验 + try/except，不建段、不 publish）、§4-6 attach 清理、§4-7 publish（真实 `ms`、仅内容满路径）、§4-8 session.json 回执读配置。
- `tools/lvdata-mcp/src/lvdata_mcp/analysis/audio.py`：§4-9 `ogg_content_duration_s()`（顺序扫页 + OpusHead pre-skip，失败 → None）+ `segment_duration_s(row)`（`is not None` 判断 + 夹紧，回落 config），两处审计（空文件 / 正常）改用。
- 实现细节与 §4 的三处差异：① 收尾契约多带 `reason` 参数（遥测与归零同处一地）；② `_audio_segment_s()` 返回 float（单测用 0.05s 驱动真实定时器；非法/<=0 回退 60）；③ 行级表达式抽成 `segment_duration_s(row)`（两处审计复用，不复制表达式）。

**测试**：
- 新增 `services/tests/unit/recording/test_audio_segment_wall_clock.py`（9 例）：§6-1 a（空洞 → 墙钟关闭 + 新段 ts 跟随恢复时刻）、b/k①（内容满优先）、c/d（迟到回调不误关下一段 + 幂等 + detach/finalize cancel + 无 .part 残留）、e/h/k②（publish 仅内容满、`duration_ms` = 真实内容、墙钟抢跑不发）、f/g（永久静默不产生空段、收尾归零回归）、§4-4（回拨 opened_at 触发入口检查）、k③（`call_later` 秒单位断言）、§3.4（config 非法回退）。
- `tools/lvdata-mcp/tests/test_audio.py`：新增 §6-j（`ogg_content_duration_s` 末页 granule / pre-skip / -1 跳过 / 无页 → None / 截断取最后完整页；分析 `duration_s` 实测优先、回落 config、空文件分支 = 0），原 `test_audio_duration_uses_segment_s` 改为回落路径用例。
- 结果：`services` 侧 recording+session 单元 321 passed；`tools/lvdata-mcp` 全量 238 passed；`services` 全量 1400 例中 69 failures/97 errors **全部为既有 Redis/BFF 环境依赖失败**（已用 `git stash` 基线复跑 5 例 hermes 用例确认同样失败，与本批无关）。

**未做（按定案）**：E10 否决（不新增包间隔切段）；存量已分析行的 `duration_s` 需 `force` 重跑才更新（不主动回填）；§6-2 真机验证（mute 数分钟 → files ts ≈ 说话时刻 / gallery 时间头 / EventListViewer 不空窗）待设备侧执行。

### 9.1 回代码 review 修正（2026-09-25）

| 发现 | 级别 | 修正 |
|---|---|---|
| `audio_segment_s` 非有限值（YAML `.inf` / JSON `1e309` / 1e309 类指数笔误）→ `int(inf)` 抛 OverflowError：`_write_session_json` 在 `start()` 里抛 ⇒ **整设备录制起不来**（实测复现）；包路径同样每包抛（写盘已发生，仅告警刷屏） | **严重（已修）** | `_audio_segment_s()` 增加 `math.isfinite` 守卫（非法 → 60）；回归用例在 `test_segment_length_config_guards`（含 `start()` + 首包实跑）；大而有限的值（如 1e9）仍原样透传 |
| 分析侧回落值 `int(row.get("s_audio_segment_s") or 60)` 同源风险：脏 session 值落进 DB（手写/异常 session.json）后 `int(inf)` 会炸掉整批分析（同 2026-08-29 空音频 400 的教训） | 中（已修） | 新增 `_coerce_segment_s()`（非数/非有限/<=0 → 60）+ 用例 `test_segment_duration_coerces_dirty_config` |

**review 方法**（结论：除上表外未发现严重缺陷）：① 40 随机种子 × 60 步的交错 fuzz（喂包/静默/间隔随机）断言四条不变式——writer⟺timer 同生共死、close 数 == 登记数、登记时长之和 == 喂入内容 ms、文件名 ts 单调且无 `.part` 残留；② 解析器对**真实 writer 产物**交叉验证（50/1500/3000/5100 包 × 20/40ms：解析值与写入内容逐毫秒一致，仅差 pre-skip 6.5ms）；③ 全仓 grep `duration_s`/`60_000` 消费方（含离线工具 `lvdata_summary.audio_seconds`、`compare_omni_audio`——取值变实测后语义更准，无结构性依赖）；④ 既有失败用 `git stash` 基线复跑核定为 Redis/BFF 环境依赖（与本批无关）。
