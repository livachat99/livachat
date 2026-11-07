# LivaChat Server Recording Module 设计文档 (Phase 1)

**版本**: v2.0
**状态**: draft
**日期**: 2026-07-22
**适用范围**: LivaChat 后端服务 Recording 附加模块
**关联文档**:
- Server 设计：[`docs/server_design.md`](./server_design.md)
- 产品需求：[`docs/livachat_prd.md`](./livachat_prd.md)
- WS 协议：[`docs/ws-protocol-design.md`](./ws-protocol-design.md)

> **范围声明**：本文档描述 **Recording Phase 1**——将客户端上传的 audio / video 与对话文本落盘到本地文件系统，供未来独立的 AI data pipeline 离线分析。
>
> **明确不做（Phase 1）**：视频 mp4 封装（仅存 JPEG 帧序列）、~~image/ 目录写入（保留为空）~~〔2026-09-11 启用：AF 快照挂图副本，见 §4.4/§5.1〕、ai_audio 记录、外部 AI pipeline 通知（仅定义 seam）、磁盘清理/保留策略、存储加密、per-device 运行时开关。

---

## 目录

1. [Recording 描述](#1-recording-描述)
2. [Recording 架构](#2-recording-架构)
3. [Recording Components](#3-recording-components)
4. [Main Logics](#4-main-logics)
5. [Media Storage](#5-media-storage)
6. [PostgreSQL DB Schema](#6-postgresql-db-schema)
7. [Redis Key 设计](#7-redis-key-设计)
8. [配置设计](#8-配置设计)
9. [与现有 Server 的集成点](#9-与现有-server-的集成点)
10. [错误处理与背压](#10-错误处理与背压)
11. [可观测性](#11-可观测性)
12. [测试策略](#12-测试策略)
13. [技术待验证项](#13-技术待验证项)
14. [未来演进](#14-未来演进)
15. [附录](#15-附录)

---

## 1. Recording 描述

### 1.1 定位

Recording Module 是挂载在现有 Gateway/Session 链路上的**旁路数据落盘服务**。它在不影响实时对话链路的前提下，把每个 device 当天的全部上行媒体与对话文本持久化到本地文件系统，并把 recording session 元信息写入 PostgreSQL，供后续 AI data pipeline 构建 knowledge base / tasks / 中长期记忆。

### 1.2 核心概念

| 概念 | 定义 |
|------|------|
| **recording session** | 数据落盘的逻辑分桶。**每个 device 每个 UTC 自然日对应一个 recording session**，`recording_session_id` 为 **UUIDv7**。与 chat session 完全无关，不参与断线重连的 session resume。 |
| **device_uuid** | 客户端设备唯一标识（协议 `device_id`，gateway 已校验 UUID v4）。Phase 1 作为存储根目录；登录体系完成后迁移为 `user_uuid`（见 §14）。 |
| **跨连接连续** | 同一 device 当天多次连接/断线重连，**复用同一个 recording session**，数据追加到同一目录。 |
| **跨天 rollover** | 到达 UTC 日界时 finalize 当前 session，下一个媒体/文本事件触发新 recording session 创建。 |

### 1.3 记录内容

| 数据 | 来源 | 落盘 | 说明 |
|------|------|------|------|
| 用户音频 | C2S `audio` 消息（裸 Opus packet） | `audio/{uuidv7}.ogg`，**60s 一个分段** | 服务端封装标准 OggS 容器，无转码 |
| 用户视频帧 | C2S `video` 消息（JPEG，~1fps） | `video/{uuidv7}.jpg`，**每帧一个文件** | 原帧落盘，不抽帧不压缩 |
| 图片 | （无独立协议消息） | `image/{uuid7}.jpg`，AF 快照帧落盘；user 行 `images` 引用 | 2026-09-11 启用（fd_pipeline_llm_v2 §9.3） |
| ASR 文本 | `transcription(is_final=true)` | `chat/history.jsonl` 追加一行 | 服务端 UTC 时间戳 |
| LLM 文本 | `ai_text` 一轮回复聚合的 `full_text` | `chat/history.jsonl` 追加一行 | 被打断的回复记录已生成部分并标记 |
| ~~ai_audio~~ | S2C TTS 音频 | **不记录** | 需求明确排除 |

**覆盖的 pipeline 模式**：chat / translate / lecture / omni **全部模式**。媒体记录在 gateway 层 tap（与模式无关），文本记录在各 orchestrator 的文本产出点 hook（见 §9）。

### 1.4 关键设计约束

| 约束 | 说明 |
|------|------|
| **零侵入实时链路** | 所有 hook 只做"拷贝引用 + 入队"（O(1)，无 I/O、无编解码），磁盘写入在独立 writer task 中异步完成。recording 任何失败不得影响对话链路。 |
| **少量改动** | 对现有 codebase 的改动仅限 §9 列出的挂接点，主体代码在新目录 `services/recording/` 下。 |
| **单写者** | 同一 device 同一时刻只有一个活跃连接（`ConnectionManager` kick 旧连接），故一个 recording session 只有一个 writer，无文件写竞争。多实例部署下通过 Redis + PG 唯一约束协调 session id（见 §4.1）。 |

---

## 2. Recording 架构

### 2.1 总体架构

```
                        ┌──────────────────────── LivaChat Server (单进程) ────────────────────────┐
                        │                                                                          │
 Client ──WSS──► Gateway│  ConnectionHandler._reader                                               │
  audio/video           │       │ ① media tap (copy ref, base64 decode, put_nowait, O(1))          │
                        │       ▼                                                                  │
  session.* ───────────►│  ┌────────────────────────────────────────────────────────────────────┐  │
                        │  │ RecordingService (process-wide)                                     │  │
                        │  │  ┌──────────────────────────────────────────────────────────────┐  │  │
                        │  │  │ RecordingSession (per device_uuid per UTC day)               │  │  │
                        │  │  │  inbound Queue ──► writer task (asyncio.to_thread for disk)  │  │  │
                        │  │  │     ├─ OggOpusWriter ──► audio/{uuidv7}.ogg (60s 轮转)       │  │  │
                        │  │  │     ├─ JpegWriter ─────► video/{uuidv7}.jpg                  │  │  │
                        │  │  │     ├─ ChatLogWriter ──► chat/history.jsonl (JSONL)          │  │  │
                        │  │  │     └─ MetaStore ──────► session.json (原子更新)              │  │  │
                        │  │  └──────────────────────────────────────────────────────────────┘  │  │
                        │  │        │                              │                             │  │
                        │  │        ▼                              ▼                             │  │
                        │  │  RecordingRepository (PG)      RedisStore (current key)             │  │
                        │  └────────────────────────────────────────────────────────────────────┘  │
                        │       ▲                    ▲                                             │
                        │       │ ② text hook        │ ② text hook                                 │
                        │  DialogOrchestrator   OmniOrchestrator (含 LectureTranslateEngine)       │
                        │  (asr final / llm final)  (omni / lecture 文本事件)                      │
                        └──────────────────────────────────────────────────────────────────────────┘
                                                 │
                                                 ▼
                                  /opt/lvdata/{device_uuid}/{recording_session_uuid}/
```

### 2.2 分层与依赖规则

沿用 `server_design.md` §2.2 的分层规则。`recording` 是与 `gateway` / `session` 平级的新 module：

```
gateway / session / omni  ──►  recording (RecordingSink 接口)  ──►  shared.persistence (PG/Redis)
                                      │
                                      ▼
                                本地文件系统 (base_dir)
```

- 现有模块只依赖 `recording` 暴露的 **`RecordingSink` Protocol**（§3.2），默认注入 **NoOpRecordingSink**，recording disabled 时零开销。
- `recording` 不反向依赖 `gateway` / `session` 的任何实现。

### 2.3 并发模型

- **每个活跃连接**持有（或复用）一个 `RecordingSession`，内含一条有界 `asyncio.Queue` 和一个 **writer task**。
- hook 调用方只做非阻塞 `put_nowait`；队列满时按 §10 drop 策略丢弃并计数，**绝不 await**。
- writer task 串行消费队列，文件写通过 `asyncio.to_thread` 卸载到线程池，避免阻塞事件循环。
- 磁盘 I/O 顺序化（单写者），无锁。

---

## 3. Recording Components

### 3.1 组件清单

| ID | 组件 | 文件 | 职责 |
|----|------|------|------|
| R1 | `RecordingConfig` | `shared/config/models.py`（扩展） | recording 全部配置（§8） |
| R2 | `RecordingSink` (Protocol) / `NoOpRecordingSink` | `services/recording/sink.py` | hook 接口：`on_session_start()` / `on_media()` / `on_chat_text()` / `on_session_config()` / `on_connection_closed()` |
| R3 | `RecordingService` | `services/recording/service.py` | 进程级注册表：`device_uuid → RecordingSession`；session 解析/创建；跨天 rollover ticker；shutdown flush |
| R4 | `RecordingSession` | `services/recording/session.py` | 单 device 单日的队列 + writer task + MetaStore；目录管理；idle/re-attach 状态 |
| R5 | `OggOpusWriter` | `services/recording/ogg.py` | 裸 Opus packet → 标准 OggS 容器封装（OpusHead/OpusTags/音频页 + CRC32），60s granule 轮转，page flush 策略 |
| R6 | `ChatLogWriter` | `services/recording/chatlog.py` | `chat/history.jsonl` JSONL 追加写，批量 flush |
| R7 | `RecordingRepository` | `shared/persistence/repositories/recording.py` | `recording_sessions` / `recording_files` 表读写 |
| R8 | `uuid7()` | `shared/utils/ids.py`（扩展） | UUIDv7 生成（纯 Python ~20 行，不新增依赖） |

> 相比早期草案合并了 `SegmentRotator` → `OggOpusWriter`（分段轮转逻辑内聚在 writer 中）和 `SessionMetaStore` → `RecordingSession`（session.json 读写约 50 行，不必独立文件）。

### 3.2 RecordingSink 接口（对现有代码的唯一暴露面）

```python
class RecordingSink(Protocol):
    async def start_for_connection(self, *, device_uuid: str) -> None: ...

    def on_session_start(self, *, chat_session_id: str) -> None:
        """chat session 开始时关联到当前 recording session。"""

    def on_session_config(self, *, audio_sample_rate: int) -> None:
        """session.start 时告知采样率（Ogg OpusHead 需要）。"""

    def on_media(self, *, kind: Literal["audio", "video"], data: bytes,
                 duration_ms: int = 0, ts_ms: int) -> None:
        """非阻塞。data 为原始 Opus packet / JPEG bytes（已解 base64）。
        duration_ms 仅 audio 有值（来自协议 payload.duration_ms）。"""

    def on_chat_text(self, *, role: Literal["user", "assistant"], text: str,
                     ts_ms: int, ref_id: str = "", chat_session_id: str = "",
                     interrupted: bool = False) -> None:
        """非阻塞。ASR final / LLM full_text。"""

    async def on_connection_closed(self) -> None:
        """flush 并挂起 writer；当天内重连复用同一 RecordingSession。"""
```

- 所有 `on_*` 方法**同步非阻塞**（内部 `put_nowait`），调用方无需 await、无需 try/except。
- `NoOpRecordingSink` 全部方法为空实现，`recording.enabled=false` 时注入。

### 3.3 RecordingService 职责边界

- `get_or_create_session(device_uuid)`：当天 session 解析（§4.1）。
- 维护 `device_uuid → RecordingSession` 注册表；连接关闭时 session 进入 **idle**（writer 挂起、文件句柄关闭、状态保留在内存 + Redis）；当天重连时 re-attach。
- 后台 ticker（60s 周期）：UTC 跨天检测（兜底，主路径是事件驱动 rollover，§4.5）；idle session 超过 `idle_session_ttl_s` 后从注册表回收。（2026-09-15 session_json_refactor：session.json 静态化后不再有周期刷盘职责）
- `shutdown()`：flush 所有活跃 session，挂在 `GracefulShutdown` 流程中（`app/lifespan.py`）。

---

## 4. Main Logics

### 4.1 L1 — Recording session 解析与创建

**触发**：WS 连接鉴权通过、`ConnectionHandler` 建立后。

```
device_uuid + today = UTC date
        │
        ▼
① Redis GET livachat:recording:device:{device_uuid}:current
        │  value = {"session_id": "<uuidv7>", "session_date": "YYYY-MM-DD"}
        ├─ 命中且 session_date == today
        │     ├─ 注册表中有 idle session ──► re-attach（重开文件句柄，audio 开新分段）
        │     └─ 注册表中无（进程重启/超时回收）──► 从 PG 恢复元信息，重建 RecordingSession
        └─ 未命中 / 过期 / 日期不符
                ▼
② PG SELECT recording_sessions WHERE device_uuid=? AND session_date=today
        ├─ 命中 ──► 复用（跨实例/跨重启恢复），回写 Redis
        └─ 未命中
                ▼
③ 创建：session_id = uuid7()
        PG INSERT ... ON CONFLICT (device_uuid, session_date) DO NOTHING
        └─ 冲突（多实例并发首连）──► SELECT 已有行并使用之（唯一约束保证幂等）
        创建目录骨架 + session.json（静态首写；幂等——已存在则跳过，2026-09-15 session_json_refactor）+ 回写 Redis（TTL 48h）
```

- **幂等保证**：PG `UNIQUE(device_uuid, session_date)` 是最终防线，Redis 只是快速路径。
- **三级降级**：Redis 不可用 → 走 PG；PG 也不可用 → 本地降级模式（内存生成 uuid7 直接落盘，仅失去跨实例协调）。

### 4.2 L2 — 音频记录（Opus → OggS 60s 分段）

```
audio msg (tap 点: ConnectionHandler._reader, seq 校验通过后)
   │  base64 decode → opus packet (bytes) + duration_ms + ts_ms
   ▼
on_media(kind="audio", data=packet, duration_ms=duration_ms, ts_ms)
   │  put_nowait → queue
   ▼
writer task
   │
   ▼
OggOpusWriter (lazy init on first packet)
   │
   ├─ 首个 audio packet 到达时：
   │     ├─ 若尚未收到 session.start ──► 缓存 packet（最多 100 个），等待 config
   │     └─ 已有采样率配置 ──► 开新分段文件 audio/{uuid7()}.ogg
   │           写 OpusHead 页 (version=1, ch=1, rate=config 采样率, pre_skip=312)
   │           写 OpusTags 页（vendor="LivaChat Recording"）
   │           写入缓存的所有 packet
   │
   ├─ 每个 packet 封装为 OggS page：
   │     granule_position += 48000Hz 采样数 (packet 20ms → 960)
   │     page 累计达到以下任一条件时 flush 到磁盘：
   │       - page payload ≥ 50,000 bytes（推荐阈值，避免单页过大）
   │       - page packet count ≥ 255（OggS 单页上限，RFC 3533）
   │     CRC32 计算并写入 page header
   │     文件写入通过 asyncio.to_thread 卸载
   │
   └─ 累计 granule ≥ 60s（2,880,000 @ 48kHz，即 3000 个 20ms packet）
          → 写 EOS 页，关闭当前分段
          → 轮转新文件 {新 uuid7()}.ogg
          → RecordingRepository 批量插 recording_files 行
```

- **无转码**：packet 原样进容器，零质量损失、零编解码 CPU。
- **采样率来源**：`on_session_config()`（session.start 的 `config.audio_sample_rate`，16000 或 24000）。**OpusHead 延迟写入**——首个 audio packet 到达时若尚未收到 `session.start`，缓存 packet 等待 config（最多 100 个 ≈ 2s），收到后立即写入正确的 OpusHead + 缓存数据。若 100 个后仍未收到 config，按默认 16000 写入，后续 config 到来时若采样率不符则轮转新分段。
- **Opus 时间基准**：Ogg granule 固定 48kHz 时钟（RFC 7845），与输入采样率无关。每 20ms packet → granule 步进 960。
- **断连处理**：当前未满 60s 的分段写 EOS 页正常关闭；重连后**开启新分段**，不续写旧文件。
- **OGG CRC32**：Phase 1 包含 CRC32，使用 `binascii.crc32`（Python 标准库，开销极小）。

#### OggS Page 格式参数

| 参数 | 值 | 说明 |
|------|-----|------|
| pre_skip | 312 | RFC 7845 推荐值（19.5ms @ 16kHz） |
| channel_count | 1 | mono |
| max_packets_per_page | 255 | RFC 3533 硬限制 |
| page_flush_bytes | ~50,000 | VBR 帧大小不固定（32kbps≈80B, 64kbps≈160B） |
| granule 步进 | 960 | 20ms × 48kHz |
| 60s granule 阈值 | 2,880,000 | 3000 packets × 960 |

### 4.3 L3 — 视频帧记录（JPEG 逐帧）

```
video msg (tap 点同 L2)
   │  base64 decode → jpeg bytes + ts_ms
   ▼
writer task → video/{uuid7()}.jpg
   │  uuid7 时间戳 ≈ 服务端接收时间，ls 即按时间排序
   │  每 20 帧批量 fsync + 批量插 recording_files
   ▼
更新统计（video_frames++, total_bytes）
```

- Phase 1 **不抽帧、不压缩、不打包 mp4**，原帧落盘。
- 与 orchestrator 的 SPEAKING 丢帧逻辑无关：**tap 在 gateway 层，所有上传帧都记录**（包括 SPEAKING 期间被对话逻辑丢弃的帧）。
- 文件先写 `.part` 后缀，关闭后 `os.replace` 为正式文件名，避免 pipeline 读到半截文件。

### 4.4 L4 — 对话文本记录（chat/history.jsonl）

**hook 点与内容**（覆盖全部模式）：

| 模式 | user 行来源 | assistant 行来源 |
|------|------------|------------------|
| chat / translate | `DialogOrchestrator._send_transcription` final 分支 | `_start_thinking` 中 `full_text` 聚合完成处（translate 记译文） |
| lecture | `LectureTranslateEngine` 源语转写 final 发送点 | `LectureTranslateEngine` 译文 final 发送点 |
| omni | `OmniOrchestrator` 用户转写文本事件发送点 | `OmniOrchestrator` AI 文本事件发送点 |

**文件格式**：**JSONL**（每行一个 JSON 对象 + `\n`），UTF-8，便于 AI pipeline 流式解析。

```jsonl
{"ts":1712345683500,"utc":"2026-07-21T08:01:23.500Z","role":"user","text":"今天天气真不错","ref_id":"trans_a1b2c3","chat_session_id":"sess_abc123","images":["../image/0193f2c1-7a2b-7e3d-9c4a-1b2c3d4e5f60.jpg"]}
{"ts":1712345684000,"utc":"2026-07-21T08:01:24.000Z","role":"assistant","text":"今天天气确实不错，适合出去走走。","ref_id":"msg_xyz789","chat_session_id":"sess_abc123"}
{"ts":1712345789000,"utc":"2026-07-21T08:03:09.000Z","role":"assistant","text":"我觉得你可以先…","ref_id":"msg_xyz790","chat_session_id":"sess_abc123","interrupted":true}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | int (ms) | UTC 毫秒时间戳 |
| `utc` | string | ISO 8601 可读辅助字段 |
| `role` | string | `"user"` (ASR) / `"assistant"` (LLM) |
| `text` | string | 文本内容 |
| `ref_id` | string | `transcription_id` 或 `message_id` |
| `chat_session_id` | string | 关联的 chat session_id |
| `interrupted` | bool | （可选，仅 assistant）是否被打断 |
| `images` | string[] | （可选，仅 user）AF 快照帧的文件路径数组，相对 jsonl 所在目录（`chat/`），形如 `../image/<uuid7>.jpg`；无图/模式不适用/写失败降级 → 字段省略（fd_pipeline_llm_v2 §10.3，2026-09-11 新增） |

- **images 不变式**：行内每个 path 在行写入时刻必然存在（帧文件先写、行后写，同一 writer item 内串行）；帧写失败跳过该帧、全部失败则省略字段——无悬空引用。
- **与 `video/` 的关系**：同一帧会被 `video/` tap 与 `image/` AF 副本双份落盘（K=1，量小，有意为之）——`image/` 是 turn 级稳定引用，独立于 `video/` 的生命周期与清理策略。

- **时间戳**：服务端接收/生成时间的 UTC ISO8601（毫秒），不用客户端时钟。
- **打断**：`full_text` 在 interrupt 时已生成多少记多少，`interrupted: true` 标记。
- writer 攒批 flush（每 5s 或每 20 行，取先到者），断连/shutdown 强制 flush。
- 只记 final：ASR interim、LLM 流式中间 token 均不记。

### 4.5 L5 — 跨天 rollover（UTC 日界）

主路径为**事件驱动**（lazy rollover），避免空转定时器：

```
任意 on_media / on_chat_text / on_session_start 事件到达
   │  event_utc_date != session.session_date
   ▼
finalize 当前 session：
   ├─ 关闭所有文件句柄（session.json 静态化，不再重写/写 finalized_at——2026-09-15 session_json_refactor）
   ├─ PG UPDATE recording_sessions SET status='finalized', 统计字段, finalized_at
   ├─ Redis DEL current key
   └─ （seam）未来在此触发 AI pipeline 通知，Phase 1 仅置 analysis_status='pending'
然后按 L1 流程创建新日期 session，事件路由到新 session
```

兜底：`RecordingService` 60s ticker 检查活跃 session 的日期，防止"跨天后长时间无事件"导致旧 session 迟迟不 finalize。

### 4.6 L6 — 断连与重连

| 事件 | 行为 |
|------|------|
| 连接关闭（正常/异常） | `on_connection_closed()`：队列 drain + flush、关闭分段文件与 history.jsonl 句柄、PG 更新统计（session.json 静态化后不再刷盘——2026-09-15 session_json_refactor）。RecordingSession 保留在注册表（idle）。 |
| 当天重连 | L1 命中同一 session_id → re-attach：重开 history.jsonl（append 模式），音频**开新分段**续录。 |
| 进程重启 | 注册表丢失；下次连接走 L1 的 PG 路径恢复 session_id，目录复用、文件追加/新建。 |
| idle 超时（默认 3600s） | RecordingService ticker 从注册表移除 idle session，释放内存；再次连接时从 PG 重建。 |

### 4.7 L7 — Graceful shutdown

挂入 `app/lifespan.py` 的 `GracefulShutdown.shutdown()`（连接关闭之后、PG/Redis pool 关闭之前）：

1. 等所有连接关闭触发的 flush 完成（复用 L6，最多等 `_SHUTDOWN_TIMEOUT`）；
2. `RecordingService.shutdown()`：剩余 idle session 全部 flush + PG 更新；
3. 再关闭 PG engine / Redis client（调整现有顺序，保证 recording 的 PG 写在 pool 关闭前完成）。

---

## 5. Media Storage

### 5.1 目录布局

```text
{base_dir}/                                                  # 默认 /opt/lvdata，配置项
└── {device_uuid}/                                           # UUID v4，WS 鉴权参数 device_id
    └── {recording_session_uuid}/                            # UUIDv7，每 UTC 日一个
        ├── session.json                                     # recording session 元信息（§5.3）
        ├── audio/
        │   ├── {uuidv7}.ogg                                 # 60s/段，Opus in OggS，文件名时间序
        │   └── ...
        ├── video/
        │   ├── {uuidv7}.jpg                                 # 每帧一个文件，文件名时间序
        │   └── ...
        ├── image/                                           # AF 快照挂图副本（user 行 images 引用）
        │                                                    #   2026-09-11 启用，见 fd_pipeline_llm_v2 §9.3
        └── chat/
            └── history.jsonl                                # JSONL, ASR + LLM final text
```

- Phase 1 使用 `device_uuid`；登录体系完成后迁移为 `{user_uuid}`（§14）。
- 目录权限 `0750`，文件 `0640`，属主为 server 运行用户。

### 5.2 文件命名与写规则

| 规则 | 说明 |
|------|------|
| 文件名 | 全部 UUIDv7：时间有序、无碰撞、`ls` 即按时间排序 |
| 原子写 | `session.json` 用 `*.tmp` + `os.replace`；媒体文件先写 `.part` 后缀、关闭后 rename（避免 pipeline 读到半截文件） |
| flush 策略 | ogg 每页即写、每 5s fsync；jpg 每 20 帧 fsync；history.jsonl 每 5s 或 20 行 flush |
| 追加 vs 新建 | 重连后 audio 开新分段；history.jsonl append；session.json 覆盖更新 |

### 5.3 session.json 结构

**2026-09-15 静态化（session_json_refactor.md）**：仅在 `start()` 首写一次
（已存在则跳过——server 重启/断线重连不覆盖）；不再周期刷新、finalize 不重写。
`timezone`/`status`/`last_active_at`/`finalized_at`（及更早的 stats/
chat_session_ids/analysis_status/analyzed_at）全部移除——身份语义留本文件，
在线/结束状态不再落盘。`started_at` 规范为毫秒 + `Z`。

```json
{
  "schema_version": 1,
  "recording_session_id": "01982f3c-7a2b-7e3d-9c4a-1b2c3d4e5f60",
  "device_uuid": "3f6b2c1a-....",
  "user_uuid": null,
  "session_date": "2026-07-21",
  "started_at": "2026-07-21T00:12:03.112Z",
  "config": {
    "audio_container": "ogg",
    "audio_codec": "opus",
    "audio_segment_s": 60,
    "video_format": "jpeg-sequence",
    "image_enabled": false,
    "ai_audio_recorded": false
  }
}
```

> **`config.image_enabled`（2026-09-11 明确）**：该字段是"独立 image 消息摄取"能力的
> 预留位（协议至今无独立 image 消息），与 chatlog 携带 image 无关——AF 快照挂图副本
> （§4.4/§5.1）不读此开关、恒落盘；维持 `false` 占位不变。

- `analysis_status` / `analyzed_at` 是给外部 AI pipeline 的 **seam**（Phase 1 固定 `pending`）。
- 写时机：创建时全量写；之后每 60s 及状态变化时增量更新（原子写：先写 `.tmp` 再 `os.replace`）。

### 5.4 容量估算（单 device 连续使用 1 小时）

| 数据 | 码率/大小 | 每小时 |
|------|----------|--------|
| audio (Opus 32kbps CBR) | ~4KB/s | ~14 MB |
| video (1fps JPEG，均值 ~100KB/帧，quality 60–80) | ~100KB/s | ~360 MB |
| chat 文本 | 忽略 | < 1 MB |
| **合计** | | **~370 MB/device·小时** |

> 视频帧占绝对大头。Phase 1 不做清理策略（§14），部署时需按 DAU × 平均在线时长规划磁盘，并加磁盘水位监控（§11）。

---

## 6. PostgreSQL DB Schema

新增两张表（`shared/models/orm.py` 扩展 + alembic migration）。

### 6.1 `recording_sessions`

| 列 | 类型 | 约束/默认 | 说明 |
|----|------|----------|------|
| `id` | `UUID` | PK，应用层生成 `uuid7()` | 即 recording_session_id |
| `device_uuid` | `Text` | NOT NULL | 冗余存储，避免 join |
| `device_id` | `UUID` | FK → `devices.id` ON DELETE SET NULL | 与现有 devices 表关联 |
| `user_id` | `UUID` | FK → `users.id` ON DELETE SET NULL，NULL | Phase 2 预留 |
| `session_date` | `Date` | NOT NULL | UTC 日期 |
| `status` | `Text` | NOT NULL，默认 `'active'` | `active` / `finalized` |
| `base_path` | `Text` | NOT NULL | 绝对路径 |
| `started_at` | `DateTime(tz)` | NOT NULL，默认 now | |
| `last_active_at` | `DateTime(tz)` | NOT NULL，默认 now | 每次 flush 更新 |
| `finalized_at` | `DateTime(tz)` | NULL | |
| `audio_segments` | `Integer` | 默认 0 | |
| `audio_duration_ms` | `BIGINT` | 默认 0 | |
| `video_frames` | `Integer` | 默认 0 | |
| `image_count` | `Integer` | 默认 0 | |
| `chat_messages` | `Integer` | 默认 0 | |
| `total_bytes` | `BIGINT` | 默认 0 | |
| `analysis_status` | `Text` | 默认 `'pending'` | Phase 1 seam：`pending` / `notified` / `done` |
| `created_at` / `updated_at` | `DateTime(tz)` | 默认 now | |

**约束与索引**：

```sql
UNIQUE (device_uuid, session_date)          -- L1 幂等创建的最终防线
INDEX idx_rec_sessions_device_time (device_uuid, session_date DESC)
INDEX idx_rec_sessions_status_date  (status, session_date)   -- AI pipeline 扫 pending
INDEX idx_rec_sessions_user_time    (user_id, session_date)  -- Phase 2 预留
```

### 6.2 `recording_files`

| 列 | 类型 | 约束/默认 | 说明 |
|----|------|----------|------|
| `id` | `UUID` | PK，`uuid7()` | 与文件名一致，可直接定位文件 |
| `recording_session_id` | `UUID` | FK → `recording_sessions.id` ON DELETE CASCADE | |
| `kind` | `Text` | NOT NULL | `audio` / `video` / `image` / `chat` |
| `rel_path` | `Text` | NOT NULL | 相对 session 目录路径，如 `audio/01982f....ogg` |
| `size_bytes` | `BIGINT` | 默认 0 | |
| `duration_ms` | `Integer` | NULL | 仅 audio |
| `width` / `height` | `Integer` | NULL | 仅 video/image（从消息 payload 获取） |
| `started_at` | `DateTime(tz)` | NOT NULL | 段首帧/首行时间 |
| `created_at` | `DateTime(tz)` | 默认 now | |

**索引**：

```sql
INDEX idx_rec_files_session_kind (recording_session_id, kind, started_at)
```

> `recording_files` 按"分段关闭后批量插入"，文本（chat）只记一行（history.jsonl 整体）。若运营上不需要逐文件检索，Phase 1 也可只建 `recording_sessions`——建议一次建齐，成本很低。

---

## 7. Redis Key 设计

沿用 `RedisStore` 的 `_k()` 前缀机制。Phase 1 仅需 **1 个 key**：

| Key | 类型 | Value | TTL | 说明 |
|-----|------|-------|-----|------|
| `livachat:recording:device:{device_uuid}:current` | STRING | JSON：`{"session_id": "<uuidv7>", "session_date": "YYYY-MM-DD"}` | 48h | 当天活跃 recording session 快速路径（L1 ①）。日期不匹配即视为 miss。 |

设计说明：

- **不需要** per-session 分布式锁：单写者前提（§1.4）+ PG 唯一约束兜底已足够。
- **不需要** 统计类 key：统计落 session.json + PG，Redis 不做双写。
- TTL 48h > 24h，保证跨天前后仍可读；日期字段判断失效，不依赖 TTL 做正确性。

---

## 8. 配置设计

`shared/config/models.py` 新增 `RecordingConfig`，挂到 `AppConfig.recording`：

```python
class RecordingConfig(BaseModel):
    enabled: bool = False                    # 全局开关，默认关
    base_dir: str = "/opt/lvdata"            # dev 可覆盖为 ./.lvdata
    audio_segment_s: int = 60                # 音频分段时长
    queue_maxsize: int = 2048                # 每连接事件队列上限
    chat_flush_interval_s: float = 5.0
    chat_flush_lines: int = 20
    fsync_interval_s: float = 5.0
    meta_flush_interval_s: float = 60.0      # ticker 周期（跨天 finalize/idle TTL）；session.json 已静态化
    idle_session_ttl_s: int = 3600           # 注册表中 idle session 回收
```

对应 YAML（`configs/dev/server.yaml`）：

```yaml
recording:
  enabled: false
  base_dir: /opt/lvdata
  audio_segment_s: 60
  queue_maxsize: 2048
  chat_flush_interval_s: 5.0
  chat_flush_lines: 20
  fsync_interval_s: 5.0
  meta_flush_interval_s: 60.0
  idle_session_ttl_s: 3600
```

环境变量：`LIVACHAT__RECORDING__ENABLED=true`

### Python 依赖

无新增第三方依赖。OGG 容器自实现，UUIDv7 用标准库 `uuid` + `os.urandom` + `time`，CRC32 用 `binascii.crc32`。

---

## 9. 与现有 Server 的集成点

全部改动清单：

| # | 文件 | 改动 | 量级 |
|---|------|------|------|
| 1 | `shared/config/models.py` | `+RecordingConfig`，`AppConfig` 加 `recording` 字段 | ~15 行 |
| 2 | `shared/utils/ids.py` | `+uuid7()` 纯 Python 实现 | ~20 行 |
| 3 | `shared/models/orm.py` | `+RecordingSession`、`+RecordingFile` 两个 ORM 类 | ~60 行 |
| 4 | `services/migrations/versions/` | alembic 新增 migration | 新文件 |
| 5 | `shared/persistence/repositories/recording.py` | `+RecordingRepository` | 新文件 |
| 6 | `gateway/endpoint.py` | 构造 sink（enabled→`RecordingService.acquire()`，否则 NoOp），注入 `ConnectionHandler` | ~10 行 |
| 7 | `gateway/connection.py` | `_reader` 中 seq 校验通过后、`_inbound.put` 前 **media tap**：`audio`/`video` 解 base64 调 `sink.on_media()`；`session.start` 调 `on_session_config()` + `on_session_start()`；`run()` finally 调 `on_connection_closed()` | ~25 行 |
| 8 | `session/orchestrator.py` | 构造参数 `+recording_sink`；`_send_transcription` final 分支与 `_start_thinking` full_text 处各加 1 行 hook | ~8 行 |
| 9 | `session/lecture_translate.py` | 源语/译文 final 发送点各加 1 行 hook | ~4 行 |
| 10 | `session/omni_orchestrator.py` | 用户转写/AI 文本事件发送点各加 1 行 hook | ~4 行 |
| 11 | `app/application.py` / `app/lifespan.py` | 启动时建 `RecordingService` 挂 `app.state`；`GracefulShutdown` 在 PG/Redis 关闭前调用其 `shutdown()` | ~15 行 |
| 12 | `configs/dev/server.yaml` | 新增 `recording:` 段 | ~10 行 |

**现有核心逻辑净改动：~25 行**（#6 + #7 + #8 + #9 + #10 中的 hook 调用行）。

**关键原则**：

- 所有 hook 都是 `sink.on_*()` 单行调用；sink 内部吞掉一切异常，**绝不允许 recording 异常冒泡到对话链路**。
- `recording.enabled=false` 时注入 `NoOpRecordingSink`，行为与现状逐字节一致。
- 不改动任何现有行为逻辑、协议消息、状态机。

---

## 10. 错误处理与背压

| 场景 | 策略 |
|------|------|
| 队列满 | `put_nowait` 失败 → **丢新事件**，`recording_dropped_total{kind}` 计数 + 限速 error 日志。音频连续性受损时当前 ogg 分段照常关闭（granule 空洞由 pipeline 侧容忍）。 |
| 磁盘满 / 写失败 | writer 捕获 `OSError` → 该 RecordingSession 标记 `failed`，停止后续写入（熔断），metric + alert 日志；**对话链路无感**。每 60s 尝试恢复一次。 |
| Redis 不可用 | L1 降级走 PG；PG 也不可用 → 本地降级模式（§4.1），只落盘，记 degraded 日志与 metric。 |
| PG 写失败 | 重试 3 次（指数退避）后放弃并计数；**文件系统数据不丢**，DB 仅元信息，可事后补扫。 |
| 进程崩溃 | 未 fsync 的数据丢失（最多 ~5s 媒体 + ~5s 文本，可接受）；`.part` 文件启动时不清理（留作排查），AI pipeline 侧忽略 `.part`。 |
| OGG 封装异常 | 丢弃当前 segment，close 当前 OGG 文件（不完整但仍可被 `opusdec` 部分解码），创建新 segment 继续。 |

**延迟预算**：tap 路径新增开销 = 1 次 base64 decode（~µs 级）+ 1 次 `put_nowait` ≈ < 50µs，对端到端 < 1s 无影响。OGG 封装全部在 writer task 内，与对话链路隔离。

---

## 11. 可观测性

### 11.1 Metrics

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `recording_events_total` | Counter | `kind` (audio/video/text) | 入队事件数 |
| `recording_dropped_total` | Counter | `kind`, `reason` | 丢弃数（队列满/熔断） |
| `recording_bytes_written_total` | Counter | `kind` | 落盘字节 |
| `recording_segments_total` | Counter | `kind` | 分段文件数 |
| `recording_session_active` | Gauge | — | 注册表中 session 数（活跃 + idle） |
| `recording_write_errors_total` | Counter | `stage` (fs/pg/redis) | 写错误 |
| `recording_queue_depth` | Gauge | `device_hash` | 队列水位采样 |

### 11.2 Logging

使用现有 structlog，新增 logger `livachat.recording`，自动注入 `device_id`、`recording_session_id` contextvar。

关键日志事件：

| 事件 | 级别 |
|------|------|
| recording session created / finalized / day rollover | INFO |
| audio segment rotated | DEBUG |
| recording write failed | WARNING |
| queue full, dropping frame | WARNING (throttled) |
| circuit breaker engaged / recovery attempt | INFO |
| degraded mode (Redis/PG unavailable) | WARNING |

### 11.3 运维告警

- 磁盘水位 > 80%（容量见 §5.4）
- `recording_dropped_total` 持续增长
- `recording_write_errors_total > 0`

---

## 12. 测试策略

沿用 `services/tests/` 现有结构（pytest，unit/integration/e2e marker，fakeredis/freezegun 已在依赖中）：

| 层 | 用例 |
|----|------|
| unit | `uuid7()` 单调性与版本位；OggS muxer 输出可用 ffprobe 解码且时长正确；60s 轮转边界（2999/3000/3001 packets）；`ChatLogWriter` JSONL 格式与批量 flush；session.json 原子写；NoOpSink 零开销 |
| unit (rollover) | freezegun 冻结时间跨 UTC 日界：事件触发 rollover、finalize 字段、新 session 创建、Redis key 刷新 |
| unit (error) | 队列满 drop 计数；sink 异常不冒泡；磁盘满熔断 + 恢复；OGG 封装异常后重建 |
| integration | fakeredis + PG（docker-compose）：L1 三路径（Redis 命中/PG 命中/新建）、并发创建幂等（唯一约束）、断连重连续录、三级降级路径 |
| e2e | `tests/e2e-backend` 起 uvicorn + 模拟客户端：完整对话后校验目录结构、ogg 可 ffplay 解码、history.jsonl 行格式、PG 行与统计一致；`recording.enabled=false` 时目录不创建 |

---

## 13. 技术待验证项（Spike）

以下项需在实现前 spike 验证：

| 编号 | 项 | 验证内容 | 失败回退 |
|------|----|----------|----------|
| S1 | OGG 封装正确性 | 自写 OggS muxer 输出的 `.ogg` 文件能否被 `ffprobe` / `opusdec` / `ffplay` 正确解码和时间轴识别 | 修复封装逻辑，CRC32 确保正确 |
| S2 | 60s 分段边界 | 3000 packets @ 20ms 是否被 ffprobe 识别为 60s 整 | 调整 granule 计算 |
| S3 | OpusHead 延迟写入 | session.start 晚于首个 audio packet 时缓存+回填策略是否正确 | 若序列化无异常则可接受 |
| S4 | 文件 IO 性能 | 20ms/帧 audio + 1fps video 并发写盘的延迟影响 | 加大 queue + 批量 flush |
| S5 | 多实例 OGG 分片 | 不同实例写同目录不同分片文件，文件名 UUIDv7 隔离 | 预期无问题 |

---

## 14. 未来演进

| 方向 | 触发条件 | 预留设计 |
|------|---------|---------|
| **user_uuid 存储根** | 登录用户体系完成 | 目录改为 `{base_dir}/{user_uuid}/{recording_session_uuid}/`；`recording_sessions.user_id` 已建列 |
| **AI pipeline 通知** | Phase 2 | seam 已留：`recording_sessions.analysis_status` + `session.json.analysis_status`；finalize 时调用 `PipelineNotifier` 接口 |
| **视频 mp4 / image 目录** | AI pipeline 需要 | 届时在 writer task 内增加离线 muxer 或直接由 pipeline 消费 JPEG 序列 |
| **数据保留/清理** | 磁盘压力或合规要求 | 独立 janitor 服务按 `session_date` 清理；DB 行与目录同事务删除 |
| **存储加密** | 合规评审 | 文件层加密（AES-256-GCM）加在 writer 层，hook 接口不变 |
| **对象存储** | 多实例/长期归档 | `MediaStore` 抽象（本地 FS 实现先行），S3 实现替换 |
| **per-device 运行时开关** | 用户 consent | `session.start.config.record_consent` 字段，在 `on_session_config` 中判断 |
| **Omni / Lecture orchestrator** | Phase 2 | hook 接口已预留，各 orchestrator 文本产出点已标注 |

---

## 15. 附录

### A.1 UUIDv7 实现

```python
import os
import time
import uuid

def uuid7() -> str:
    """生成 UUIDv7 字符串（时间有序，RFC 9562）。

    格式：48-bit Unix ms timestamp | 4-bit version(7) | 12-bit rand_a
          | 2-bit variant(10) | 62-bit rand_b
    """
    timestamp_ms = int(time.time() * 1000)
    timestamp_bytes = timestamp_ms.to_bytes(6, "big")

    rand_bytes = os.urandom(10)

    # version 7
    rand_a = int.from_bytes(rand_bytes[0:2], "big")
    rand_a = (rand_a & 0x0FFF) | 0x7000

    # variant 10
    rand_b = int.from_bytes(rand_bytes[2:8], "big")
    rand_b = (rand_b & 0x3FFFFFFFFFFFFFFF) | 0x8000000000000000

    b = timestamp_bytes + rand_a.to_bytes(2, "big") + rand_b.to_bytes(8, "big")
    return str(uuid.UUID(bytes=b))
```

- Python 3.12 stdlib 无 uuid7（3.14 引入），纯标准库实现，不新增第三方依赖。
- 文件名/主键时间有序；每 ms 可生成大量不冲突 id（74bit 随机）。

### A.2 OggS/Opus 封装要点

- 文件 = `OpusHead` 页 + `OpusTags` 页 + 音频页序列 + EOS 页；每页带 `granule_position`（48kHz 时钟累计采样数）、`page_seq`、CRC32。
- 20ms packet ⇒ granule 步进 960（与输入采样率 16k/24k 无关）。
- 60s ⇒ granule 2,880,000（= 3000 个 20ms packet）轮转。
- `OpusHead.input_sample_rate` 从 `session.start` 的 `payload.config.audio_sample_rate` 获取（16000 或 24000）。
- `pre_skip` = 312（RFC 7845 推荐值，对应 19.5ms @ 16kHz）。
- `channel_count` = 1（mono）。
- 单个 OGG page 的 max_packets 上限为 255（RFC 3533），推荐按 page payload ~50KB 阈值 flush（Opus VBR 帧大小不固定：32kbps ≈ 80 bytes, 64kbps ≈ 160 bytes）。
- 分段独立成链（每文件完整头页），pipeline 可单文件解码。

### A.3 已确认决策记录

| 决策点 | 结论 | 日期 |
|--------|------|------|
| video mp4 编码方案 | Phase 1 **不出 mp4**，JPEG 帧序列直接落 `video/`，AI pipeline 自行处理 | 2026-07-21 |
| image/ 目录 | ~~Phase 1 **保留为空目录**（协议无独立 image 消息）~~〔2026-09-11 启用：AF 快照帧副本，user 行 `images` 引用〕 | 2026-09-11 |
| day 边界时区 | **UTC 自然日** | 2026-07-21 |
| 覆盖模式 | chat / translate / lecture / omni **全部模式**的音视频 + 文本 | 2026-07-21 |
| 音频容器 | 服务端封装**标准 .ogg**（自写 OggS muxer，无转码，含 CRC32） | 2026-07-21 |
| history 格式 | **JSONL**（含 ref_id、chat_session_id、interrupted 标记） | 2026-07-22 |
| history 时间戳 | 服务端 UTC ISO8601（ms），不用客户端时钟 | 2026-07-21 |
| 打断的 assistant 文本 | 记录已生成部分 + `"interrupted": true` | 2026-07-21 |
| media tap 点 | **gateway 层**（ConnectionHandler._reader），覆盖全部模式 | 2026-07-22 |
| text hook 点 | **各 orchestrator** 文本产出点分别 hook | 2026-07-22 |
| OGG CRC32 | Phase 1 **包含**（`binascii.crc32`，开销极小） | 2026-07-22 |
| Python 依赖 | **不新增**（OGG 自实现、UUIDv7 标准库、CRC32 binascii） | 2026-07-22 |

### A.4 术语表

| 术语 | 说明 |
|------|------|
| recording session | 每 device 每 UTC 日的数据落盘分桶，UUIDv7 标识 |
| OggS | Ogg 封装格式页结构（RFC 3533） |
| granule position | Ogg 页的可解码时间位置（Opus 固定 48kHz 时钟） |
| tap | 在现有链路上拷贝数据引用的旁路挂接点 |
| rollover | UTC 跨天时 finalize 旧 session、创建新 session 的动作 |
| idle session | 连接断开后保留在注册表中、等待重连 re-attach 的 RecordingSession |
| JSONL | JSON Lines，每行一个独立 JSON 对象 + `\n` |

---

*本文档为 Recording Module Phase 1 的技术设计契约，合并了 d4、g52、k3 三个草案的优点。实现前需完成 §13 的 spike 验证。若实现时与本文档偏离，需同步更新本文档并记录决策。*
