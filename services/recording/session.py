"""RecordingSession — per-device per-UTC-day writer task + state machine."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from recording.chatlog import ChatLogWriter
from recording.file_registry import uuid7_to_ms
from recording.ogg import OggOpusWriter
from recording.sink import ChatImageFrame
from shared.utils.ids import uuid7

logger = logging.getLogger("livachat.recording")

# Max Opus packets to buffer before session.start config arrives (~2s)
_MAX_BUFFERED_PACKETS = 100

# 墙钟兜底余量（E9）：内容条件构造上只领先约 1 个包时长（3000×20ms：59.98s vs 60.00s），
# 无余量时包节拍抖动 / writer 排队滞后 / 采集时钟偏斜都会让墙钟先到——而 publish 只在
# 内容满路径发 ⇒ relay proactive 静默失效。取 6s：> 登记入队超时 5s + ~10% 时钟偏斜余量。
_AUDIO_WALL_BACKSTOP_MARGIN_MS = 6_000
_DEFAULT_AUDIO_SEGMENT_S = 60


def _now_iso_ms() -> str:
    """规范时间戳：ISO 8601 UTC、'T'、毫秒、'Z'（session_json_refactor §3.A.1）。"""
    return (datetime.now(tz=UTC)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


@dataclass(slots=True)
class _BufferedPacket:
    data: bytes
    duration_ms: int


@dataclass(slots=True)
class _RecordingItem:
    kind: Literal["audio", "video", "asr_final", "llm_final", "transcribe_final", "close"]
    data: bytes = b""
    duration_ms: int = 0
    ts_ms: int = 0
    role: str = ""
    text: str = ""
    ref_id: str = ""
    chat_session_id: str = ""
    interrupted: bool = False
    source: str = ""
    frame_id: int = 0
    frames: list[ChatImageFrame] = field(default_factory=list)
    agent_name: str = ""
    agent_id: str = ""
    job_key: str = ""


@dataclass
class RecordingSession:
    """Per-device per-UTC-day recording state.

    Lifecycle: active → idle → active (re-attach) → finalized
    """

    recording_session_id: str
    device_uuid: str
    session_date: str  # YYYY-MM-DD
    storage_path: Path
    config: dict  # RecordingConfig relevant fields

    # session.start 上报的客户端时区（规范形）；缺失/无效维持 UTC（timezone-support.md §10）
    timezone: str = "UTC"

    # Agent relay publisher (optional)
    agent_publisher: Any = None

    # recording_files 登记器（RecordingFileRegistry）；None = 不登记（本地/测试模式）
    file_registry: Any = None

    # Runtime state
    _queue: asyncio.Queue[_RecordingItem] = field(default_factory=lambda: asyncio.Queue(maxsize=2048))
    _writer_task: asyncio.Task[None] | None = None
    _status: Literal["active", "idle", "finalized"] = "active"
    _detached_at: float | None = None  # monotonic time when detached (for idle TTL)
    _started_at_iso: str = ""  # captured once at start(), never overwritten

    # Audio state
    _ogg_writer: OggOpusWriter | None = None
    _audio_sample_rate: int = 16000
    _audio_segment_index: int = 0
    _audio_segment_ms: int = 0
    _audio_packets_buffered: list[_BufferedPacket] = field(default_factory=list)
    _session_config_received: bool = False
    _last_segment_path: str = ""  # path of last completed OGG segment
    _audio_segment_opened_at: float | None = None  # monotonic 建段时刻（E9 墙钟兜底）
    _audio_timer: asyncio.TimerHandle | None = None  # 墙钟兜底句柄（段序号作身份）

    # Chat state
    _chat_writer: ChatLogWriter | None = None
    _transcribe_writer: ChatLogWriter | None = None
    _translate_writer: ChatLogWriter | None = None

    # Stats
    chat_session_ids: set[str] = field(default_factory=set)
    audio_segments: int = 0
    audio_frames: int = 0
    video_frames: int = 0
    chat_messages: int = 0
    total_bytes: int = 0

    # -- public API (RecordingSink impl) ----------------------------------

    async def start(self) -> None:
        """Create directories, session.json (first write only), launch the writer."""
        self._started_at_iso = _now_iso_ms()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        (self.storage_path / "audio").mkdir(exist_ok=True)
        (self.storage_path / "video").mkdir(exist_ok=True)
        (self.storage_path / "image").mkdir(exist_ok=True)
        (self.storage_path / "chat").mkdir(exist_ok=True)

        self._chat_writer = ChatLogWriter(self.storage_path / "chat" / "history.jsonl")
        self._chat_writer.open()
        self._transcribe_writer = ChatLogWriter(self.storage_path / "chat" / "transcribe.jsonl")
        self._transcribe_writer.open()
        self._translate_writer = ChatLogWriter(self.storage_path / "chat" / "translate.jsonl")
        self._translate_writer.open()

        # 幂等首写（session_json_refactor §3.A.2a）：server 重启/长断线后
        # acquire() 会对同一会话再次 start()——已存在则跳过写盘并回读
        # started_at，不覆盖、不 bump updated_at；登记照常（record 为
        # INSERT OR IGNORE，幂等且兜住「已写盘未登记」的崩溃窗口）。
        session_json_path = self.storage_path / "session.json"
        if session_json_path.exists():
            self._read_started_at(session_json_path)
        else:
            self._write_session_json()
        await self._register_session_json()
        await self._register_logs()
        self._writer_task = asyncio.create_task(self._run_writer())
        logger.info(
            "recording session started",
            extra={
                "recording_session_id": self.recording_session_id,
                "device_uuid": self.device_uuid,
                "session_date": self.session_date,
            },
        )

    async def attach(self) -> None:
        """Re-attach after reconnect: re-open chat log, reset audio segment.

        The writer task is device-scoped and survives detach — other
        concurrent connections may still be feeding the queue. Only create it
        if it is not already running.
        """
        self._status = "active"
        self._detached_at = None
        if not self._started_at_iso:
            self._started_at_iso = _now_iso_ms()
        if self._chat_writer is None:
            self._chat_writer = ChatLogWriter(self.storage_path / "chat" / "history.jsonl")
            self._chat_writer.open()
        if self._transcribe_writer is None:
            self._transcribe_writer = ChatLogWriter(self.storage_path / "chat" / "transcribe.jsonl")
            self._transcribe_writer.open()
        if self._translate_writer is None:
            self._translate_writer = ChatLogWriter(self.storage_path / "chat" / "translate.jsonl")
            self._translate_writer.open()
        await self._register_logs()
        # Audio always starts a new segment on re-attach
        self._ogg_writer = None
        self._audio_segment_ms = 0
        if self._audio_timer is not None:
            self._audio_timer.cancel()
            self._audio_timer = None
        self._audio_segment_opened_at = None
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._run_writer())
        logger.info(
            "recording session re-attached",
            extra={"recording_session_id": self.recording_session_id},
        )

    async def detach(self) -> None:
        """Suspend on disconnect: close the audio segment, mark idle.

        The writer keeps running: the session is shared per device and a
        concurrent connection may still be producing frames (stopping the
        writer here would fill the queue and drop their audio).
        """
        self._status = "idle"
        self._detached_at = time.monotonic()
        finished = self._finish_audio_segment("detach")
        if finished is not None:
            segment_path, ms = finished
            await self._register_media("audio", segment_path, duration_ms=ms or None)
        logger.info(
            "recording session detached",
            extra={"recording_session_id": self.recording_session_id},
        )

    async def finalize(self) -> None:
        """Permanently close the recording session."""
        self._status = "finalized"
        await self._enqueue_close()
        if self._writer_task is not None:
            await self._writer_task
            self._writer_task = None
        logger.info(
            "recording session finalized",
            extra={"recording_session_id": self.recording_session_id},
        )

    # -- RecordingSink methods (non-blocking) -----------------------------

    def on_session_start(self, *, chat_session_id: str) -> None:
        self.chat_session_ids.discard(None)  # clean up stale null from old sessions
        if chat_session_id:
            self.chat_session_ids.add(chat_session_id)

    def on_session_config(self, *, audio_sample_rate: int, timezone: str = "UTC") -> None:
        self._audio_sample_rate = audio_sample_rate
        if timezone:
            self.timezone = timezone
        self._session_config_received = True
        # Flush buffered packets if any
        if self._audio_packets_buffered:
            logger.debug(
                "flushing buffered audio packets",
                extra={"count": len(self._audio_packets_buffered)},
            )
            # Re-queue buffered packets via the regular path
            for bp in self._audio_packets_buffered:
                self._queue.put_nowait(_RecordingItem(
                    kind="audio", data=bp.data, duration_ms=bp.duration_ms, ts_ms=0,
                ))
            self._audio_packets_buffered.clear()

    def on_media(
        self, *, kind: Literal["audio", "video"], data: bytes, duration_ms: int = 0, ts_ms: int = 0
    ) -> None:
        try:
            self._queue.put_nowait(_RecordingItem(
                kind=kind, data=data, duration_ms=duration_ms, ts_ms=ts_ms,
            ))
        except asyncio.QueueFull:
            logger.warning("recording queue full, dropping frame", extra={"kind": kind})

    def on_chat_text(
        self, *, role: Literal["user", "assistant"], text: str, ts_ms: int,
        ref_id: str = "", chat_session_id: str = "", interrupted: bool = False,
        source: str | None = None, frames: Sequence[ChatImageFrame] | None = None,
        agent_name: str = "", agent_id: str = "", job_key: str = "",
    ) -> None:
        logger.debug(
            f"recording on_chat_text enqueued role={role} len={len(text)} ts_ms={ts_ms} "
            f"ref_id={ref_id} chat_session_id={chat_session_id} rec={self.recording_session_id} "
            f"n_frames={len(frames) if frames else 0}"
        )
        try:
            self._queue.put_nowait(_RecordingItem(
                kind="asr_final" if role == "user" else "llm_final",
                role=role, text=text, ts_ms=ts_ms,
                ref_id=ref_id, chat_session_id=chat_session_id, interrupted=interrupted,
                source=source or "",
                frames=list(frames) if frames else [],
                agent_name=agent_name, agent_id=agent_id, job_key=job_key,
            ))
        except asyncio.QueueFull:
            logger.warning(
                f"recording queue full, dropping text role={role} rec={self.recording_session_id}"
            )

    def on_transcribe_text(
        self, *, text: str, ts_ms: int,
        ref_id: str = "", chat_session_id: str = "",
    ) -> None:
        """Non-blocking. Transcribe-mode ASR final → chat/transcribe.jsonl.

        与 on_chat_text 纪律一致：调用方不 await、不 try/except，队列满丢弃并告警。
        """
        logger.debug(
            f"recording on_transcribe_text enqueued len={len(text)} ts_ms={ts_ms} "
            f"ref_id={ref_id} rec={self.recording_session_id}"
        )
        try:
            self._queue.put_nowait(_RecordingItem(
                kind="transcribe_final",
                role="user", text=text, ts_ms=ts_ms,
                ref_id=ref_id, chat_session_id=chat_session_id,
                source="transcribe",
            ))
        except asyncio.QueueFull:
            logger.warning(
                f"recording queue full, dropping transcribe text rec={self.recording_session_id}"
            )

    async def on_connection_closed(self) -> None:
        await self.detach()

    # -- writer task -------------------------------------------------------

    async def _run_writer(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                try:
                    if item.kind == "audio":
                        await self._handle_audio(item)
                    elif item.kind == "video":
                        await self._handle_video(item)
                    elif item.kind in ("asr_final", "llm_final", "transcribe_final"):
                        await self._handle_text(item)
                    elif item.kind == "close":
                        break
                except Exception:
                    logger.warning("recording write failed", exc_info=True)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            self._flush_all()

    async def _handle_audio(self, item: _RecordingItem) -> None:
        # Buffer packets if session.start config not yet received
        if not self._session_config_received:
            if len(self._audio_packets_buffered) < _MAX_BUFFERED_PACKETS:
                self._audio_packets_buffered.append(_BufferedPacket(
                    data=item.data, duration_ms=item.duration_ms,
                ))
            return

        # 墙钟兜底（E9）：定时器被事件循环延迟时的入口检查，阈值与定时器同源
        if self._ogg_writer is not None and self._audio_segment_opened_at is not None \
                and time.monotonic() - self._audio_segment_opened_at \
                >= self._audio_segment_s() + _AUDIO_WALL_BACKSTOP_MARGIN_MS / 1000:
            finished = self._finish_audio_segment("wall_clock")
            if finished is not None:
                segment_path, ms = finished
                await self._register_media("audio", segment_path, duration_ms=ms or None)

        # Lazy-init OGG writer on first packet
        if self._ogg_writer is None:
            await self._rotate_audio_segment()

        assert self._ogg_writer is not None
        self._ogg_writer.write_packet(item.data, duration_ms=item.duration_ms)
        self.audio_frames += 1
        self._audio_segment_ms += item.duration_ms

        # Rotate at segment length（E4 单一出处，默认 60s）
        if self._audio_segment_ms >= int(self._audio_segment_s() * 1000):
            finished = self._finish_audio_segment("content_full")
            if finished is not None:
                segment_path, ms = finished
                await self._register_media("audio", segment_path, duration_ms=ms or None)

                # Agent Relay: publish recording.ready for completed segment
                # （E6：传实际内容时长；E8：只在本路径发）
                if self.agent_publisher is not None:
                    session_id = next(iter(self.chat_session_ids), "")
                    asyncio.create_task(
                        self.agent_publisher.publish_recording_ready(
                            session_id=session_id,
                            device_id=self.device_uuid,
                            recording_id=self.recording_session_id,
                            manifest_path=str(segment_path),
                            duration_ms=ms,
                        )
                    )

    async def _handle_video(self, item: _RecordingItem) -> None:
        filename = f"{uuid7()}.jpg"
        filepath = self.storage_path / "video" / filename

        # Write via to_thread to avoid blocking event loop
        await asyncio.to_thread(self._write_file_atomic, filepath, item.data)
        await self._register_media("video", filepath)
        self.video_frames += 1
        self.total_bytes += len(item.data)

    async def _handle_text(self, item: _RecordingItem) -> None:
        if item.kind == "transcribe_final":
            if self._transcribe_writer is None:
                logger.warning(
                    f"recording transcribe writer is None, dropping text rec={self.recording_session_id}"
                )
                return
            written = self._transcribe_writer.write(
                role=item.role,
                text=item.text,
                ts_ms=item.ts_ms,
                ref_id=item.ref_id,
                chat_session_id=item.chat_session_id,
                source=item.source or None,
            )
            if written:
                await self._touch_log(self._transcribe_writer)
            # 不计 chat_messages：transcribe 文本轨独立于对话文本轨
            logger.debug(
                f"recording transcribe text written len={len(item.text)} ts_ms={item.ts_ms} "
                f"rec={self.recording_session_id}"
            )
            return
        if item.source in ("translate", "lecture"):
            # 翻译文本轨独立落 translate.jsonl，不进 history.jsonl（与 transcribe.jsonl
            # 同语义）。source 区分 dialogue translate（"translate"）与 lecture 同传
            # （"lecture"），行写自身 source。
            if self._translate_writer is None:
                logger.warning(
                    f"recording translate writer is None, dropping text rec={self.recording_session_id}"
                )
                return
            written = self._translate_writer.write(
                role=item.role,
                text=item.text,
                ts_ms=item.ts_ms,
                ref_id=item.ref_id,
                chat_session_id=item.chat_session_id,
                source=item.source or None,
            )
            if written:
                await self._touch_log(self._translate_writer)
            logger.debug(
                f"recording translate text written role={item.role} len={len(item.text)} ts_ms={item.ts_ms} "
                f"rec={self.recording_session_id}"
            )
            return
        if self._chat_writer is None:
            logger.warning(
                f"recording chat writer is None, dropping text role={item.role} rec={self.recording_session_id}"
            )
            return
        # 帧先落盘、行后写（同一 item 内串行）→ 行内每个 path 在写入时刻必然存在
        images = await self._write_item_images(item.frames) if item.frames else []
        written = self._chat_writer.write(
            role=item.role,
            text=item.text,
            ts_ms=item.ts_ms,
            ref_id=item.ref_id,
            chat_session_id=item.chat_session_id,
            interrupted=item.interrupted,
            source=item.source or None,
            images=images or None,
            agent_name=item.agent_name or None,
            agent_id=item.agent_id or None,
            job_key=item.job_key or None,
        )
        if written:
            await self._touch_log(self._chat_writer)
        self.chat_messages += 1
        logger.debug(
            f"recording chat text written role={item.role} len={len(item.text)} ts_ms={item.ts_ms} "
            f"n_images={len(images)} rec={self.recording_session_id}"
        )

    async def _write_item_images(self, frames: Sequence[ChatImageFrame]) -> list[str]:
        """帧落 image/<uuid7>.jpg，返回相对 chat/ 的行内路径列表；单帧失败跳过该帧
        （无悬空引用），不阻断行写入。"""
        images: list[str] = []
        for f in frames:
            filename = f"{uuid7()}.jpg"
            try:
                await asyncio.to_thread(
                    self._write_file_atomic, self.storage_path / "image" / filename, f.data
                )
            except Exception:
                logger.warning(
                    "recording image write failed, frame skipped",
                    extra={"recording_session_id": self.recording_session_id},
                    exc_info=True,
                )
                continue
            images.append(f"../image/{filename}")
        return images

    # -- helpers -----------------------------------------------------------

    async def _enqueue_close(self) -> None:
        try:
            self._queue.put_nowait(_RecordingItem(kind="close"))
        except asyncio.QueueFull:
            pass

    # -- recording_files 登记（recording_data_process.md §2.1）--------------

    async def _register_logs(self) -> None:
        """登记三个 chat jsonl 行（start/attach 开启 writer 后调用）。

        jsonl 的逐条 append 只走 touch（bump）——touch 对不存在的行是**静默
        跳过**（ChatStore touch_missing_row），首次登记必须在此完成，否则
        lvdata 永远看不到 chat 文件。幂等（INSERT OR IGNORE），attach 重开
        writer 后重复调用无副作用。
        """
        reg = self.file_registry
        if reg is None:
            return
        for writer, kind in (
            (self._chat_writer, "chat_history"),
            (self._transcribe_writer, "chat_transcribe"),
            (self._translate_writer, "chat_translate"),
        ):
            if writer is None:
                continue
            try:
                size = writer.path.stat().st_size
            except OSError:
                continue
            await reg.record(kind=kind, path=writer.path, size_bytes=size)

    async def _register_session_json(self) -> None:
        """session.json 首次登记；此后文件不再变动（无 touch 路径）。

        不因文件已存在而跳过登记（§3.A.2a「登记照常」）：record 为
        INSERT OR IGNORE（冲突不 bump，幂等零副作用），且 start() 内写盘与
        登记间存在崩溃窗口（已写盘未登记）——跳过登记会使该会话对 lvdata
        永久不可见（仅剩 backfill reconcile 兜底）。
        """
        reg = self.file_registry
        if reg is None:
            return
        path = self.storage_path / "session.json"
        try:
            size = path.stat().st_size
        except OSError:
            return
        await reg.record(kind="session", path=path, size_bytes=size)

    async def _register_media(self, kind: str, path: Path,
                              *, duration_ms: int | None = None) -> None:
        """audio/video 文件登记：size 现取，ts_ms 由 uuid7 文件名解析。"""
        reg = self.file_registry
        if reg is None:
            return
        try:
            size = path.stat().st_size
        except OSError:
            logger.warning("recording file stat failed, registration skipped",
                           extra={"path": str(path)})
            return
        await reg.record(kind=kind, path=path, size_bytes=size,
                         ts_ms=uuid7_to_ms(path.stem), duration_ms=duration_ms)

    async def _touch_log(self, writer: ChatLogWriter) -> None:
        """chat jsonl append 后 touch（逐条 bump，§2.1 不变式）。"""
        reg = self.file_registry
        if reg is None:
            return
        try:
            size = writer.path.stat().st_size
        except OSError:
            return
        await reg.touch(path=writer.path, size_bytes=size)

    def _register_media_bg(self, kind: str, path: Path,
                           *, duration_ms: int | None = None) -> None:
        """fire-and-forget 登记（sync 收尾路径用；失败由 registry 内部告警）。"""
        if self.file_registry is None:
            return
        try:
            asyncio.get_running_loop().create_task(
                self._register_media(kind, path, duration_ms=duration_ms))
        except RuntimeError:
            pass

    def _audio_segment_s(self) -> float:
        """段长（秒，E4 单一出处）：config 缺失/非法回退默认。

        非法 = 非数 / <=0（call_later(0) 会退化成一包一段）/ **非有限**——YAML `.inf`
        或 JSON `1e309` 会一路走到 `int(inf)`，让 `_write_session_json` 与包路径
        双双抛 OverflowError（前者直接让 start() 失败 ⇒ 整设备录制起不来）。
        取 float 是为了单测能用极小值（如 0.05s）驱动真实定时器。
        """
        try:
            value = float(self.config.get("audio_segment_s", _DEFAULT_AUDIO_SEGMENT_S))
        except (TypeError, ValueError):
            return float(_DEFAULT_AUDIO_SEGMENT_S)
        if not math.isfinite(value) or value <= 0:
            return float(_DEFAULT_AUDIO_SEGMENT_S)
        return value

    def _finish_audio_segment(self, reason: str) -> tuple[Path, int] | None:
        """段收尾契约（全部关闭点共用；§4-0）：close → 内容时长 → 归零 → 计数 → 遥测。

        reason: content_full / wall_clock / detach / flush——wall_clock 占比 >0 即抢跑
        告警信号（R8），故收尾与打点必须同处一地。调用方各自决定
        `await _register_media(...)` 还是 `_register_media_bg(...)`；publish（E8）
        只在内容满路径由调用方发。
        """
        opened_at = self._audio_segment_opened_at
        path = self._close_audio_segment()
        if path is None:
            self._audio_segment_opened_at = None
            return None
        ms = self._audio_segment_ms
        self._audio_segment_ms = 0
        self._audio_segment_opened_at = None
        if ms > 0:
            self.audio_segments += 1
        logger.info(
            "audio segment closed",
            extra={
                "reason": reason,
                "file": path.name,
                "index": self._audio_segment_index,
                "content_ms": ms,
                "wall_span_ms": int((time.monotonic() - opened_at) * 1000)
                if opened_at is not None else -1,
                "recording_session_id": self.recording_session_id,
            },
        )
        return path, ms

    async def _rotate_audio_segment(self) -> None:
        # .part 中间态：流式写入期间 scanner 不可见，close 后 mv 成正式 .ogg
        filename = f"{uuid7()}.ogg.part"
        filepath = self.storage_path / "audio" / filename
        self._ogg_writer = OggOpusWriter(filepath, sample_rate=self._audio_sample_rate)
        self._ogg_writer.open()
        self._audio_segment_index += 1
        self._audio_segment_opened_at = time.monotonic()
        if self._audio_timer is not None:
            # 防御性 cancel：正常路径都已 cancel，不显式依赖该不变式
            self._audio_timer.cancel()
        index = self._audio_segment_index
        self._audio_timer = asyncio.get_running_loop().call_later(
            self._audio_segment_s() + _AUDIO_WALL_BACKSTOP_MARGIN_MS / 1000,
            self._on_audio_segment_timeout, index,
        )
        logger.debug(
            "audio segment rotated",
            extra={"file": filename, "index": index},
        )

    def _on_audio_segment_timeout(self, index: int) -> None:
        """墙钟兜底（E9）：段内不再有包 → 按「段长 + 余量」收尾；不建新段、不发 publish（E8）。

        身份校验（writer 存在 + 段序号相同）保证迟到回调不会误关「下一段」；
        close 抛异常（磁盘满）时既有契约保留 _ogg_writer 引用，重试交给下一包 /
        入口墙钟检查，定时器本身不重排。
        """
        if self._ogg_writer is None or self._audio_segment_index != index:
            return
        try:
            finished = self._finish_audio_segment("wall_clock")
        except Exception:
            logger.warning(
                "audio wall-clock close failed",
                extra={"index": index, "recording_session_id": self.recording_session_id},
                exc_info=True,
            )
            return
        if finished is not None:
            path, ms = finished
            self._register_media_bg("audio", path, duration_ms=ms or None)

    def _close_audio_segment(self) -> Path | None:
        """close Ogg writer + mv .part → 正式名；返回最终路径（publish 用）。

        失败时保留 _ogg_writer 引用（全部成功后才置 None）：close 抛异常
        （如磁盘满）时 _flush_all/finalize 可重试，避免 fd 泄漏与 .part
        永残；重试时 close 幂等（_f is None 直接 return）。
        """
        if self._audio_timer is not None:
            # 先 cancel 再短路：writer-None 分支同样要清掉句柄（E9）
            self._audio_timer.cancel()
            self._audio_timer = None
        writer = self._ogg_writer
        if writer is None:
            return None
        tmp_path = writer._path
        writer.close()                       # flush + EOS 页 + close
        final_path = tmp_path.with_suffix("")  # xxx.ogg.part → xxx.ogg
        os.replace(tmp_path, final_path)
        self._ogg_writer = None
        return final_path

    def _flush_all(self) -> None:
        finished = self._finish_audio_segment("flush")
        if finished is not None:
            last_segment, ms = finished
            self._register_media_bg("audio", last_segment, duration_ms=ms or None)
        if self._chat_writer is not None:
            self._chat_writer.close()
            self._chat_writer = None
        if self._transcribe_writer is not None:
            self._transcribe_writer.close()
            self._transcribe_writer = None
        if self._translate_writer is not None:
            self._translate_writer.close()
            self._translate_writer = None

    @staticmethod
    def _write_file_atomic(filepath: Path, data: bytes) -> None:
        part = filepath.with_suffix(filepath.suffix + ".part")
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(part, "wb") as f:
            f.write(data)
        os.replace(part, filepath)

    # -- session.json ------------------------------------------------------

    def _write_session_json(self) -> None:
        """静态站位文件（7 键）：仅在 start() 首写一次，此后不再重写。

        timezone/status/last_active_at/finalized_at 已移除（session_json_refactor
        §1.1）——身份语义留本文件，在线/结束状态不再落盘。
        """
        payload = {
            "schema_version": 1,
            "recording_session_id": self.recording_session_id,
            "device_uuid": self.device_uuid,
            "user_uuid": None,
            "session_date": self.session_date,
            "started_at": self._started_at_iso,
            "config": {
                "audio_container": "ogg",
                "audio_codec": "opus",
                "audio_segment_s": int(self._audio_segment_s()),
                "video_format": "jpeg-sequence",
                "image_enabled": False,
                "ai_audio_recorded": False,
            },
            # stats / chat_session_ids / analysis_* 由 lvdata-mcp 分析并写入
            # 其数据库（inventory / agent_jobs / audio / video），此处不再记录
        }
        # Atomic write
        tmp_path = self.storage_path / "session.json.tmp"
        target_path = self.storage_path / "session.json"
        with open(tmp_path, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, target_path)

    def _read_started_at(self, path: Path) -> None:
        """跳过首写时回读盘上 started_at，保持内存态与盘一致（§3.A.2a）。"""
        try:
            started = json.loads(path.read_text(encoding="utf-8")).get("started_at")
        except Exception:
            started = None
        if isinstance(started, str) and started:
            self._started_at_iso = started
        else:
            logger.warning(
                "session.json unreadable, started_at kept as generated",
                extra={"recording_session_id": self.recording_session_id},
            )

    # -- properties --------------------------------------------------------

    @property
    def status(self) -> str:
        return self._status
