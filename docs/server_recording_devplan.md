# LivaChat Recording Module — 开发与测试计划

**版本**: v1.0
**日期**: 2026-07-22
**关联设计**: [`docs/server_recording_design.md`](./server_recording_design.md)

---

## 目录

1. [总览与里程碑](#1-总览与里程碑)
2. [Phase 0: Spike 验证](#2-phase-0-spike-验证)
3. [Phase 1: 基础设施](#3-phase-1-基础设施)
4. [Phase 2: 核心写入逻辑](#4-phase-2-核心写入逻辑)
5. [Phase 3: 服务层与会话管理](#5-phase-3-服务层与会话管理)
6. [Phase 4: 集成挂接](#6-phase-4-集成挂接)
7. [Phase 5: 可观测性](#7-phase-5-可观测性)
8. [Phase 6: 测试](#8-phase-6-测试)
9. [依赖关系图](#9-依赖关系图)
10. [风险与回退](#10-风险与回退)

---

## 1. 总览与里程碑

### 1.1 开发顺序

```
Phase 0 (Spike) ──► Phase 1 (基础设施) ──► Phase 2 (核心写入) ──► Phase 3 (服务层)
                                                                        │
                                                                        ▼
                                                              Phase 4 (集成挂接)
                                                                        │
                                                                        ▼
                                                              Phase 5 (可观测性)
                                                                        │
                                                                        ▼
                                                              Phase 6 (测试)
```

### 1.2 里程碑

| 里程碑 | 完成标准 | 估时 |
|--------|---------|------|
| M0: Spike 通过 | 5 项 spike 全部验证通过或确认回退方案可行 | 0.5d |
| M1: 基础设施就绪 | uuid7 + Config + Migration + Repository 可独立运行 | 0.5d |
| M2: 核心写入可用 | OggOpusWriter + ChatLogWriter 单测通过，输出文件可被外部工具解码 | 1.5d |
| M3: 服务层联通 | RecordingSession + RecordingService 单测通过，session 生命周期正确 | 1d |
| M4: 集成完成 | 全部 hook 点接入，`recording.enabled=false` 回归通过 | 1d |
| M5: 可观测性 | Metrics + Logging 就绪，Grafana dashboard 可展示 | 0.5d |
| M6: 测试完成 | 单测 + 集成测试 + e2e 全部通过 | 1d |
| **合计** | | **~6d** |

### 1.3 文件清单

**新增文件**（8 个）：

```
services/recording/
├── __init__.py
├── sink.py              # RecordingSink Protocol + NoOpRecordingSink
├── service.py           # RecordingService (进程级注册表 + ticker + shutdown)
├── session.py           # RecordingSession (队列 + writer task + meta)
├── ogg.py               # OggOpusWriter (OggS/Opus 容器封装)
├── chatlog.py           # ChatLogWriter (JSONL 追加)
shared/persistence/repositories/
└── recording.py         # RecordingRepository (PG CRUD)
services/migrations/versions/
└── 0002_recording_module.py  # Alembic migration
```

**修改文件**（8 个）：

```
shared/config/models.py          # +RecordingConfig, AppConfig 加字段
shared/utils/ids.py              # +uuid7()
shared/models/orm.py             # +RecordingSession, +RecordingFile ORM
gateway/endpoint.py              # 构造 sink, 注入 ConnectionHandler
gateway/connection.py            # _reader media tap, session.start hook, on_connection_closed
session/orchestrator.py          # DialogOrchestrator text hook (2 处)
session/lecture_translate.py     # LectureTranslateEngine text hook (2 处)
session/omni_orchestrator.py     # OmniOrchestrator text hook (2 处)
app/application.py               # lifespan 中初始化 RecordingService + shutdown 挂载
configs/dev/server.yaml          # recording 配置段
```

---

## 2. Phase 0: Spike 验证

> **目标**: 在写任何业务代码之前验证核心技术风险，确认方案可行或确定回退路径。

### S1 — OGG 封装正确性

**验证内容**：自写 OggS muxer 输出的 `.ogg` 文件能否被 `ffprobe` / `opusdec` / `ffplay` 正确解码。

**步骤**：
1. 写一个最小 `OggOpusWriter` 原型（约 80 行），仅实现 OpusHead + OpusTags + 单音频页 + EOS
2. 用 20ms Opus silence packets（或从现有测试 fixture 取真实 packet）写入 10s 的文件
3. 运行验证：
   ```bash
   ffprobe -v error -show_entries format=duration test.ogg
   opusdec test.ogg test.wav
   ffplay test.ogg
   ```

**通过标准**：ffprobe 读取 duration ≈ 10s，opusdec 解码无错误，ffplay 正常播放。

### S2 — 60s 分段边界

**验证内容**：3000 packets (@20ms) 的分段边界是否正确，相邻分段是否无缝。

**步骤**：
1. 在 S1 原型基础上，循环写入 6000 个 packet（2 个完整分段）
2. 验证第一个分段自动关闭、第二个分段自动创建
3. ffprobe 分别检查两个文件的 duration ≈ 60s

**通过标准**：两个文件时长均为 60s ± 0.5s，均可独立解码。

### S3 — OpusHead 延迟写入

**验证内容**：先收到 audio packet 后收到 session.start config 的场景下，缓存+回填策略是否正确。

**步骤**：
1. 模拟时序——先写入 20 个 packet（无 config），再调用 `on_session_config(sample_rate=24000)`
2. 验证生成的文件中 OpusHead 的 `input_sample_rate=24000`
3. 验证缓存 packet 未丢失——ffprobe 显示 duration 正确

**通过标准**：采样率正确写入头部，所有缓存 packet 无丢失，duration 正确。

### S4 — 文件 IO 性能

**验证内容**：20ms/帧 audio + 1fps video 并发写盘对事件循环的影响。

**步骤**：
1. 模拟 writer task：50 audio packets/s (~20ms interval) + 1 video frame/s，持续 5 分钟
2. 测量 event loop 延迟（用 `asyncio.get_event_loop().slow_callback_duration`）
3. 测量队列最大深度

**通过标准**：回调延迟 < 5ms（远低于 20ms 音频帧间隔），队列深度 < 100。

### S5 — 多实例 OGG 分片隔离

**验证内容**：不同实例写同目录不同分片文件是否冲突。

**步骤**：
1. 两个进程同时向同一 session 目录写入不同 UUIDv7 文件名的 OGG 文件
2. 验证无文件覆盖、无写入冲突

**通过标准**：所有文件落盘，文件名无冲突，内容完整。

---

## 3. Phase 1: 基础设施

> **依赖**: Phase 0 全部通过
> **产出**: uuid7 + Config + Migration + Repository 可独立运行

### Task 1.1 — UUIDv7 实现

| 项目 | 内容 |
|------|------|
| **文件** | `shared/utils/ids.py`（追加） |
| **改动量** | ~20 行 |
| **规格** | 设计文档 §A.1 |

**AC**：
- [ ] `uuid7()` 返回格式合法的 UUID string（匹配 `uuid.UUID()` 解析）
- [ ] 连续 10000 次生成全部唯一
- [ ] version 字段 = 7，variant 字段 = 10
- [ ] 两次调用间隔 1ms 时生成的 UUID 字符串排序与时间顺序一致
- [ ] 单测：`tests/unit/test_ids.py`

---

### Task 1.2 — RecordingConfig

| 项目 | 内容 |
|------|------|
| **文件** | `shared/config/models.py`（修改） |
| **改动量** | ~15 行 |

**AC**：
- [ ] `RecordingConfig` 包含设计文档 §8 全部字段及默认值
- [ ] `AppConfig.recording: RecordingConfig = RecordingConfig()` 挂载
- [ ] 环境变量 `LIVACHAT__RECORDING__ENABLED=true` 可覆盖
- [ ] `configs/dev/server.yaml` 增加 `recording:` 段，默认 `enabled: false`

---

### Task 1.3 — DB Migration + ORM

| 项目 | 内容 |
|------|------|
| **文件** | `shared/models/orm.py`（修改）+ `services/migrations/versions/0002_recording_module.py`（新增） |
| **改动量** | ~60 + ~50 行 |

**AC**：
- [ ] `RecordingSession` ORM 类包含设计文档 §6.1 全部列
- [ ] `RecordingFile` ORM 类包含设计文档 §6.2 全部列
- [ ] migration 可 `alembic upgrade head` 和 `alembic downgrade -1`
- [ ] `UNIQUE(device_uuid, session_date)` 约束生效
- [ ] 三个索引创建成功

---

### Task 1.4 — RecordingRepository

| 项目 | 内容 |
|------|------|
| **文件** | `shared/persistence/repositories/recording.py`（新增） |
| **改动量** | ~80 行 |

**方法签名**：

```python
class RecordingRepository:
    async def create(self, session: RecordingSession) -> RecordingSession
    async def get_by_id(self, recording_session_id: str) -> RecordingSession | None
    async def get_active_by_device_date(self, device_uuid: str, session_date: date) -> RecordingSession | None
    async def update_stats(self, recording_session_id: str, **kwargs) -> None
    async def finalize(self, recording_session_id: str, **final_stats) -> None
    async def list_by_device(self, device_uuid: str, *, limit: int = 50) -> list[RecordingSession]
    async def insert_files_batch(self, files: list[RecordingFile]) -> None
```

**AC**：
- [ ] 沿用现有 repository 模式（AsyncSession 注入）
- [ ] `create` 返回带 id 的 ORM 对象
- [ ] `get_active_by_device_date` 按 `device_uuid + session_date` 查询
- [ ] `finalize` 更新 `status='finalized'`、`finalized_at` 和统计字段
- [ ] 单测覆盖全部 CRUD 方法（用 fakeredis 同级的内存 SQLite 或 testcontainers PG）

---

## 4. Phase 2: 核心写入逻辑

> **依赖**: Phase 1 完成
> **产出**: OggOpusWriter + ChatLogWriter 单测通过，输出文件可被外部工具解码

### Task 2.1 — RecordingSink Protocol + NoOpRecordingSink

| 项目 | 内容 |
|------|------|
| **文件** | `services/recording/sink.py`（新增） |
| **改动量** | ~40 行 |
| **规格** | 设计文档 §3.2 |

**AC**：
- [ ] `RecordingSink` Protocol 包含全部 7 个方法
- [ ] `NoOpRecordingSink` 所有方法为空 `pass` / 直接返回
- [ ] `on_media` 签名包含 `kind`, `data`, `duration_ms`, `ts_ms`
- [ ] `on_chat_text` 签名包含 `role`, `text`, `ts_ms`, `ref_id`, `chat_session_id`, `interrupted`

---

### Task 2.2 — OggOpusWriter

| 项目 | 内容 |
|------|------|
| **文件** | `services/recording/ogg.py`（新增） |
| **改动量** | ~180 行 |
| **规格** | 设计文档 §4.2 + §A.2 |

**核心逻辑**：

```
OggOpusWriter
├── open_segment(sample_rate) → 写 OpusHead + OpusTags 页
├── write_packet(opus_bytes, duration_ms) → 封装 OggS page, 累加 granule
│     ├── page payload ≥ 50KB 或 packet count ≥ 255 → flush page
│     └── granule ≥ 2,880,000 → flush + close segment, 返回 True (触发轮转)
├── flush() → flush 当前 page + EOS
└── close() → flush + 关闭文件
```

**AC**：
- [ ] OpusHead 页字段正确：version=1, ch=1, pre_skip=312, sample_rate 可配置
- [ ] 每个 OggS page 的 CRC32 正确（可用 `ffprobe -v error` 校验无 CRC error）
- [ ] 60s 分段边界正确（granule ≥ 2,880,000 触发轮转）
- [ ] page 内 packet ≤ 255，page payload ~50KB 阈值触发 flush
- [ ] EOS 页正确写入
- [ ] `write_packet` 返回 `True` 时表示 segment 已关闭，调用方应创建新 segment
- [ ] 输出文件可用 `ffprobe` 显示正确 duration，`opusdec` 解码为 WAV
- [ ] 单测：`tests/unit/recording/test_ogg.py`
  - 1 个 packet → duration ≈ 20ms
  - 3000 个 20ms packets → duration ≈ 60s，触发轮转
  - 3001 个 packets → 生成两个文件（60s + 20ms）
  - 不同采样率（16000 / 24000）→ OpusHead 字段正确

---

### Task 2.3 — ChatLogWriter

| 项目 | 内容 |
|------|------|
| **文件** | `services/recording/chatlog.py`（新增） |
| **改动量** | ~60 行 |
| **规格** | 设计文档 §4.4 |

**核心逻辑**：

```
ChatLogWriter(filepath)
├── write(entry: dict) → 序列化 JSON + \n, 写入 buffer
│     攒批 flush: 每 20 行或每 5s
├── flush() → 强制写入磁盘
└── close() → flush + 关闭句柄
```

**AC**：
- [ ] 输出格式为合法 JSONL（每行独立 JSON object + `\n`）
- [ ] `entry` dict 包含 `ts`, `utc`, `role`, `text`, `ref_id`, `chat_session_id`，`interrupted` 可选
- [ ] 攒批 flush 逻辑正确（20 行或 5s，先到者触发）
- [ ] `close()` 后文件内容完整、无截断
- [ ] 单测：`tests/unit/recording/test_chatlog.py`
  - 1 行 → close 后文件含 1 行
  - 25 行 → 触发 20 行 flush，close 后 25 行完整
  - interrupted=true 的条目包含该字段

---

### Task 2.4 — session.json 原子写

| 项目 | 内容 |
|------|------|
| **位置** | `services/recording/session.py` 内的 `_write_session_json()` |
| **改动量** | ~30 行 |
| **规格** | 设计文档 §5.2 + §5.3 |

**AC**：
- [ ] 先写 `.tmp` 文件，再 `os.replace(tmp, target)`
- [ ] 写入期间进程崩溃时 target 文件保持旧内容（不被破坏）
- [ ] schema 包含设计文档 §5.3 全部字段

---

## 5. Phase 3: 服务层与会话管理

> **依赖**: Phase 2 完成
> **产出**: RecordingSession + RecordingService 单测通过

### Task 3.1 — RecordingSession

| 项目 | 内容 |
|------|------|
| **文件** | `services/recording/session.py`（新增） |
| **改动量** | ~150 行 |
| **规格** | 设计文档 §3.3 + §4.6 |

**核心逻辑**：

```python
@dataclass
class RecordingSession:
    recording_session_id: str
    device_uuid: str
    session_date: str
    storage_path: Path
    chat_session_ids: set[str]
    status: Literal["active", "idle", "finalized"]

    _queue: asyncio.Queue[RecordingItem]
    _writer_task: asyncio.Task | None
    _ogg_writer: OggOpusWriter | None
    _chat_writer: ChatLogWriter | None

    # 生命周日
    async def start(self)         # 创建 writer task
    async def attach(self)        # idle → active, 重开文件句柄
    async def detach(self)        # active → idle, flush + 关句柄
    async def finalize(self)      # 关闭所有资源, 更新 PG/session.json
```

**AC**：
- [ ] `start()` 创建 writer task、创建目录、写 session.json (status=active)
- [ ] `detach()` → flush + 关闭文件句柄，status 变为 idle
- [ ] `attach()` → idle 状态下重开 history.jsonl (append)、OGG 开新分段
- [ ] `finalize()` → flush + 关句柄 + PG UPDATE + session.json (status=finalized)
- [ ] writer task 正确分发 audio/video/chat/close 事件
- [ ] 单测：`tests/unit/recording/test_session.py`

---

### Task 3.2 — RecordingService

| 项目 | 内容 |
|------|------|
| **文件** | `services/recording/service.py`（新增） |
| **改动量** | ~150 行 |
| **规格** | 设计文档 §3.3 + §4.1 + §4.5 + §4.7 |

**核心逻辑**：

```python
class RecordingService:
    _sessions: dict[str, RecordingSession]   # device_uuid → session
    _redis: RedisStore
    _pg_factory: Callable
    _cfg: RecordingConfig
    _ticker_task: asyncio.Task | None

    async def acquire(device_uuid) → RecordingSink  # L1 流程
    async def _get_or_create(device_uuid) → RecordingSession
    async def _ticker()                              # 60s: meta flush + rollover + idle 回收
    async def shutdown()                             # GracefulShutdown 回调
```

**AC**：

L1 创建流程：
- [ ] Redis 命中（date 匹配） + 注册表有 idle session → re-attach
- [ ] Redis 命中 + 注册表无 → PG 恢复 → 重建 RecordingSession
- [ ] Redis 未命中 → PG 命中 → 复用 + 回写 Redis
- [ ] 全部未命中 → 创建新 session（uuid7 + 目录 + session.json + PG + Redis）
- [ ] PG 冲突（并发创建）→ `ON CONFLICT` 后 SELECT 已有行

Ticker（60s）：
- [ ] 活跃 session 的 `session.json` 周期刷盘
- [ ] 跨天检测：session.session_date != today → finalize 旧 session + 建新 session
- [ ] idle session 超过 `idle_session_ttl_s` → 从注册表移除 + PG 更新统计

Shutdown：
- [ ] `shutdown()` 遍历所有 session → finalize
- [ ] 在 PG/Redis pool 关闭前执行

降级：
- [ ] Redis 不可用时走 PG → 本地模式
- [ ] PG 不可用时走纯本地模式（记 degraded metric）

单测：
- [ ] `tests/unit/recording/test_service.py` — 覆盖 L1 全部路径
- [ ] `tests/unit/recording/test_service_rollover.py` — freezegun 跨天
- [ ] `tests/unit/recording/test_service_degraded.py` — Redis/PG 降级

---

## 6. Phase 4: 集成挂接

> **依赖**: Phase 3 完成
> **产出**: 全部 hook 点接入，`recording.enabled=false` 回归通过

### Task 4.1 — Gateway 层 media tap

| 项目 | 内容 |
|------|------|
| **文件** | `gateway/connection.py`（修改）+ `gateway/endpoint.py`（修改） |
| **改动量** | ~35 行 |

**改动点**：

`gateway/connection.py` — `_reader()` 中，在 seq 校验通过后、`_inbound.put(msg)` 前：

```python
# 现有代码: self._log_inbound(msg); await self._inbound.put(msg)
# 新增:
if self._sink is not None:
    if isinstance(msg, ClientAudio):
        opus_bytes = base64.b64decode(msg.payload.data)
        self._sink.on_media(kind="audio", data=opus_bytes,
                           duration_ms=msg.payload.duration_ms, ts_ms=msg.timestamp)
    elif isinstance(msg, ClientVideo):
        jpeg_bytes = base64.b64decode(msg.payload.data)
        self._sink.on_media(kind="video", data=jpeg_bytes, ts_ms=msg.timestamp)
    elif isinstance(msg, ClientSessionStart):
        sample_rate = msg.payload.config.get("audio_sample_rate", 16000)
        self._sink.on_session_config(audio_sample_rate=sample_rate)
        self._sink.on_session_start(chat_session_id=msg.payload.session_id)
```

`ConnectionHandler.__init__` 增加 `sink: RecordingSink | None = None` 参数。`run()` 的 finally 中调用 `await self._sink.on_connection_closed()`。

`gateway/endpoint.py` — 创建 ConnectionHandler 前：

```python
if app.state.recording_service is not None:
    sink = await app.state.recording_service.acquire(device_id)
else:
    sink = NoOpRecordingSink()
handler = ConnectionHandler(..., sink=sink)
```

**AC**：
- [ ] audio msg → base64 decode → `on_media(kind="audio", ...)` 调用
- [ ] video msg → base64 decode → `on_media(kind="video", ...)` 调用
- [ ] session.start → `on_session_config` + `on_session_start` 调用
- [ ] 其他消息类型（ping/pong/stop 等）不触发 hook
- [ ] `sink=None` 时行为与现状完全一致（`if self._sink is not None` 短路）
- [ ] 连接关闭时 `on_connection_closed` 被调用

---

### Task 4.2 — Orchestrator 层 text hook

| 项目 | 内容 |
|------|------|
| **文件** | `session/orchestrator.py` + `session/lecture_translate.py` + `session/omni_orchestrator.py` |
| **改动量** | ~16 行 |

**改动点**：

`session/orchestrator.py` — `DialogOrchestrator.__init__` 加 `recording_sink` 参数：

```python
# _send_transcription 中 is_final=True 分支 (1 行)
self._recording_sink.on_chat_text(
    role="user", text=text, ts_ms=now_ms(),
    ref_id=transcription_id, chat_session_id=self._ctx.session_id)

# _start_thinking 中 full_text 聚合完成处 (1 行)
self._recording_sink.on_chat_text(
    role="assistant", text=full_text, ts_ms=now_ms(),
    ref_id=message_id, chat_session_id=self._ctx.session_id,
    interrupted=was_interrupted)
```

`session/lecture_translate.py` — (2 行) 源语 final + 译文 final
`session/omni_orchestrator.py` — (2 行) 用户转写 + AI 文本

**AC**：
- [ ] `recording.enabled=false` 时注入 NoOpRecordingSink，原有测试全部通过
- [ ] translate 模式记录译文（非原文）
- [ ] lecture 模式源语和译文分别记录为 user/assistant
- [ ] omni 模式文本事件正确记录
- [ ] 所有 hook 调用在 try/except 外——sink 内部吞异常

---

### Task 4.3 — Application 装配

| 项目 | 内容 |
|------|------|
| **文件** | `app/application.py`（修改） |
| **改动量** | ~15 行 |

**改动点**：

```python
# lifespan 中:
if cfg.recording.enabled:
    recording_service = RecordingService(
        redis_store=redis_store,
        pg_session_factory=pg_session_factory,
        config=cfg.recording,
    )
    app.state.recording_service = recording_service
else:
    app.state.recording_service = None

# GracefulShutdown 中, 在 PG/Redis pool 关闭之前:
if app.state.recording_service is not None:
    await app.state.recording_service.shutdown()
```

**AC**：
- [ ] `recording.enabled=false` 时 `app.state.recording_service is None`
- [ ] `recording.enabled=true` 时 `RecordingService` 正确初始化
- [ ] shutdown 顺序：连接关闭 → recording flush → PG/Redis pool 关闭

---

## 7. Phase 5: 可观测性

> **依赖**: Phase 4 完成
> **产出**: Metrics + Logging 就绪

### Task 5.1 — Prometheus Metrics

| 项目 | 内容 |
|------|------|
| **文件** | `services/recording/metrics.py`（新增，或在 recording 各模块内定义） |
| **改动量** | ~40 行 |

**指标清单**（设计文档 §11.1）：

```python
recording_events_total       = Counter("livachat_recording_events_total", ..., ["kind"])
recording_dropped_total      = Counter("livachat_recording_dropped_total", ..., ["kind", "reason"])
recording_bytes_written_total = Counter("livachat_recording_bytes_written_total", ..., ["kind"])
recording_segments_total     = Counter("livachat_recording_segments_total", ..., ["kind"])
recording_session_active     = Gauge("livachat_recording_session_active", ...)
recording_write_errors_total = Counter("livachat_recording_write_errors_total", ..., ["stage"])
recording_queue_depth        = Gauge("livachat_recording_queue_depth", ..., ["device_hash"])
```

**AC**：
- [ ] 所有指标在 `/metrics` 端点可见
- [ ] `recording_session_active` 反映注册表中 session 数量
- [ ] `recording_dropped_total` 在队列满时递增
- [ ] `recording_write_errors_total` 在磁盘满/PG 失败时递增

---

### Task 5.2 — Structlog Logging

| 项目 | 内容 |
|------|------|
| **文件** | 各 recording 模块内 |
| **改动量** | ~20 行 |

**AC**：
- [ ] 使用 `structlog.get_logger("livachat.recording")`
- [ ] session 创建/finalize/rollover → INFO
- [ ] audio segment rotated → DEBUG
- [ ] write failed → WARNING (含 device_id, recording_session_id)
- [ ] queue full → WARNING (throttled, 每秒最多 1 条)
- [ ] degraded mode → WARNING

---

## 8. Phase 6: 测试

> **依赖**: Phase 5 完成
> **产出**: 全部测试通过

### Task 6.1 — 单元测试

| 测试文件 | 覆盖模块 | 关键用例 |
|----------|---------|---------|
| `tests/unit/test_ids.py` | uuid7 | 格式合法、唯一性、version/variant、时序有序 |
| `tests/unit/recording/test_ogg.py` | OggOpusWriter | 单 packet、60s 边界(2999/3000/3001)、不同采样率、CRC32、EOS |
| `tests/unit/recording/test_chatlog.py` | ChatLogWriter | 单行、20 行 flush、interrupted、close 完整性 |
| `tests/unit/recording/test_session.py` | RecordingSession | start/detach/attach/finalize 生命周期、writer 分发 |
| `tests/unit/recording/test_service.py` | RecordingService | L1 全部路径（Redis hit/PG hit/新建/并发冲突） |
| `tests/unit/recording/test_service_rollover.py` | RecordingService | freezegun 跨天 rollover |
| `tests/unit/recording/test_service_degraded.py` | RecordingService | Redis 不可用、PG 不可用、本地模式 |
| `tests/unit/recording/test_sink.py` | NoOpRecordingSink | 所有方法不抛异常 |
| `tests/unit/recording/test_repo.py` | RecordingRepository | CRUD 全方法 |

### Task 6.2 — 集成测试

| 测试文件 | 覆盖场景 |
|----------|---------|
| `tests/integration/test_recording_l1.py` | L1 三路径集成（Redis + PG + fakeredis/freezegun）|
| `tests/integration/test_recording_e2e_flow.py` | 模拟 WS 连接 → 发 audio/video/text → 验证文件落盘 |
| `tests/integration/test_recording_reconnect.py` | 断连 → 重连 → 同一 recording session 追加 |

**AC**：
- [ ] fakeredis + 测试 PG（docker-compose）环境可运行
- [ ] 端到端流程：模拟 audio 50 packets + video 10 frames + text 5 条 → 等待 flush → 验证目录结构和文件内容
- [ ] OGG 文件可被 ffprobe 解码
- [ ] history.jsonl 每行合法 JSON
- [ ] PG recording_sessions 行与 session.json 统计一致

### Task 6.3 — E2E 测试

| 测试文件 | 覆盖场景 |
|----------|---------|
| `tests/e2e-backend/test_recording.py` | 起 uvicorn + 真实 WS 客户端 → 完整对话 → 验证落盘 |

**AC**：
- [ ] `recording.enabled=false` → 目录不创建、无文件产生
- [ ] `recording.enabled=true` → 目录结构正确、OGG 可 ffplay、JSONL 格式正确
- [ ] 跨天场景（freezegun 冻结时间）→ 两个 recording session 目录均存在

### Task 6.4 — 回归测试

**AC**：
- [ ] 现有 `services/tests/` 全部用例通过（`recording.enabled=false` 时行为不变）
- [ ] `make test` 全绿

---

## 9. 依赖关系图

```
Phase 0 (Spike)
    │
    ▼
Phase 1 ─────────────────────────────────────┐
    │                                         │
    ├─ 1.1 uuid7                              │
    ├─ 1.2 RecordingConfig                    │
    ├─ 1.3 Migration + ORM ──► 1.4 Repository │
    │                                         │
    ▼                                         │
Phase 2 ─────────────────────────────────────┐
    │                                         │
    ├─ 2.1 Sink Protocol                      │
    ├─ 2.2 OggOpusWriter ◄── S1,S2 验证结果   │
    ├─ 2.3 ChatLogWriter                      │
    └─ 2.4 session.json 原子写                │
    │                                         │
    ▼                                         │
Phase 3 ─────────────────────────────────────┐
    │                                         │
    ├─ 3.1 RecordingSession ◄── 2.2, 2.3, 2.4│
    └─ 3.2 RecordingService ◄── 1.4, 3.1     │
    │                                         │
    ▼                                         │
Phase 4 ─────────────────────────────────────┐
    │                                         │
    ├─ 4.1 Gateway tap ◄── 3.2, 2.1          │
    ├─ 4.2 Orchestrator hook ◄── 2.1          │
    └─ 4.3 Application 装配 ◄── 3.2           │
    │                                         │
    ▼                                         │
Phase 5 (可观测性)                             │
    │                                         │
    ▼                                         │
Phase 6 (测试) ─── 依赖全部 Phase 完成
```

### 并行化机会

- **Phase 1 内**：1.1, 1.2 可并行；1.3 → 1.4 串行
- **Phase 2 内**：2.1 可最早做；2.2, 2.3, 2.4 可并行（都只依赖 2.1 接口定义）
- **Phase 3 内**：3.1 做完才能做 3.2
- **Phase 4 内**：4.1, 4.2, 4.3 做完 3.2 后可并行
- **Phase 6 内**：全部单测文件可并行写

---

## 10. 风险与回退

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| OGG 封装自写有 bug，输出文件不被标准工具解码 | 中 | 高 — AI pipeline 无法消费 | S1/S2 spike 先行验证；备选引入 `pyogg` 做 muxer |
| 文件 IO 阻塞事件循环导致对话延迟增加 | 低 | 高 — 违反零侵入约束 | S4 spike 验证 `asyncio.to_thread` 效果；监控 event loop 延迟 |
| `on_media` tap 在 gateway 层增加了每个消息的处理开销 | 低 | 中 — base64 decode ~µs 级 | 实测验证；可加 sampling（如每 N 帧记录一帧） |
| PG migration 与现有 schema 冲突 | 低 | 中 — 影响部署 | 新增表无冲突；`devices.id` FK 使用 ON DELETE SET NULL |
| 多实例同时创建同一 device 当日 recording session | 低 | 低 — 仅首次连接有此窗口 | PG UNIQUE 约束兜底 + ON CONFLICT DO NOTHING |
| 磁盘空间耗尽 | 中 | 中 — recording 停录但不影响对话 | 熔断机制 + 磁盘水位告警（§11.3）；Phase 2 加自动清理 |

### 回退方案

如果 Phase 0 Spike 全部失败或实现过程中发现自写 OGG 不可行：

1. **OGG 回退**：引入 `pyogg` 或直接存裸 Opus 流（`.opus` 文件 + 外部元信息描述采样率和分段时间），由 AI pipeline 侧自行封装
2. **性能回退**：如 `asyncio.to_thread` 不满足，回退到 `aiofiles`（已评估为可行方案）
3. **整体回退**：`recording.enabled=false` 恢复原始行为，recording 模块不加载任何代码路径

---

*本文档为 Recording Module 的开发与测试执行计划，与 [`docs/server_recording_design.md`](./server_recording_design.md) 配套使用。实现过程中如偏离本计划，需同步更新。*
