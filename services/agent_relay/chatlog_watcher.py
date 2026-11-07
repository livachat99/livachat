"""ChatlogWatcher — tails a session's chatlog file and nudges proactive adapters.

See design doc §5.3. The watcher resolves the chatlog path for a device,
polls the file for growth, filters lines by chat_session_id, applies the
trigger rules (user line triggers, assistant lines batch, min-interval
debounce), and calls adapter.nudge() with a ChatlogUpdate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent_relay.adapters.base import (
    AgentAdapter,
    AgentContext,
    AgentResponseChunk,
    ChatlogLine,
    ChatlogUpdate,
)
from agent_relay.job_store import JobStore
from agent_relay.metrics import inc_chatlog_event

logger = logging.getLogger("livachat.agent_relay.chatlog_watcher")

_USER_ROLE = "user"
_ASSISTANT_ROLE = "assistant"


async def resolve_chatlog_path(
    lookup: Callable[..., Awaitable[dict | None]],
    *,
    device_id: str,
    base_dir: str,
) -> str | None:
    """Resolve a device's current chatlog path via the recording lookup.

    lookup(device_uuid=...) → {recording_session_id, base_path} | None
    （RecordingService.current_recording_info，进程内，替代原 Redis GET）。
    Builds `{base_dir}/{device_id}/{recording_session_id}/chat/history.jsonl`.
    Returns None if recording is disabled or the lookup misses.
    """
    try:
        info = await lookup(device_uuid=device_id)
    except Exception:
        logger.warning("failed to resolve chatlog path", extra={"device_id": device_id})
        return None
    if not info or not info.get("recording_session_id"):
        return None
    return f"{base_dir.rstrip('/')}/{device_id}/{info['recording_session_id']}/chat/history.jsonl"



def _read_segment(path: Path, start: int, end: int) -> bytes:
    """同步读 chatlog 段（to_thread 调用，避免阻塞事件循环）。"""
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(end - start)

class ChatlogWatcher:
    """Tail a chatlog file and nudge proactive adapters on new user lines."""

    def __init__(
        self,
        *,
        session_id: str,
        device_id: str,
        adapters: list[AgentAdapter],
        context: AgentContext,
        recording_lookup: Callable[..., Awaitable[dict | None]],
        job_store: JobStore | None = None,
        base_dir: str = "/opt/lvdata",
        poll_interval_s: float = 3.0,
        nudge_coalesce_s: float = 2.0,
        nudge_min_interval_s: float = 5.0,
        nudge_max_lines: int = 20,
        nudge_max_bytes: int = 4096,
    ) -> None:
        self._session_id = session_id
        self._device_id = device_id
        self._adapters = adapters
        self._context = context
        self._recording_lookup = recording_lookup
        self._job_store = job_store
        self._base_dir = base_dir.rstrip("/")
        self._poll_interval_s = poll_interval_s
        self._nudge_coalesce_s = nudge_coalesce_s
        self._nudge_min_interval_s = nudge_min_interval_s
        self._nudge_max_lines = nudge_max_lines
        self._nudge_max_bytes = nudge_max_bytes

        self._path: str | None = None
        self._offset = 0
        self._pending: list[ChatlogLine] = []
        self._last_nudge_at = 0.0
        self._nudge_in_flight = False
        self._flushing = False
        self._task: asyncio.Task[None] | None = None
        self._coalesce_task: asyncio.Task[None] | None = None
        # (adapter_name, device_id, session_id, chunk) — adapter_name is needed
        # so nudge pushes keep their own attribution instead of a global default.
        self._push_callback: (
            Callable[[str, str, str, AgentResponseChunk], Awaitable[None]] | None
        ) = None

    # -- lifecycle ------------------------------------------------------------

    def set_push_callback(
        self, cb: Callable[[str, str, str, AgentResponseChunk], Awaitable[None]]
    ) -> None:
        self._push_callback = cb

    def update_context(self, context: AgentContext) -> None:
        """Update the context passed to adapter.nudge()（重复 session.started 的
        App 语言变化；docs/agent_multilingual.md D10）。"""
        self._context = context

    async def start(self) -> bool:
        """Resolve the chatlog path and start polling. Returns False if the
        chatlog cannot be resolved (proactive disabled for this session)."""
        path = await self._resolve_path()
        if path is None:
            logger.warning(
                "chatlog path unresolved, proactive disabled",
                extra={"session_id": self._session_id, "device_id": self._device_id},
            )
            inc_chatlog_event("resolve_failed")
            return False
        self._path = path
        self._offset = self._file_size(path)
        logger.info(
            "chatlog watcher started at EOF",
            extra={"session_id": self._session_id, "path": path, "offset": self._offset},
        )
        inc_chatlog_event("resolved")
        self._task = asyncio.create_task(self._poll_loop(), name=f"chatlog-watcher-{self._session_id}")
        return True

    async def stop(self) -> None:
        for task in (self._coalesce_task, self._task):
            if task is not None:
                task.cancel()
        for task in (self._coalesce_task, self._task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._task = None
        self._coalesce_task = None

    # -- polling --------------------------------------------------------------

    async def _poll_loop(self) -> None:
        assert self._path is not None
        while True:
            try:
                await asyncio.sleep(self._poll_interval_s)
                await self._poll_once()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning(
                    "chatlog watcher poll error",
                    extra={"session_id": self._session_id},
                    exc_info=True,
                )

    async def _poll_once(self) -> None:
        assert self._path is not None
        path = Path(self._path)

        if not path.exists():
            # File missing: recording session may have rolled over (UTC day).
            new_path = await self._resolve_path()
            if new_path is None or new_path == self._path:
                return
            logger.info(
                "chatlog path changed (rollover)",
                extra={"session_id": self._session_id, "old": self._path, "new": new_path},
            )
            inc_chatlog_event("rollover")
            self._path = new_path
            self._offset = self._file_size(new_path)
            await self._notify_path_change()
            return

        size = path.stat().st_size
        if size < self._offset:
            # File truncated: reset to current EOF.
            self._offset = size
            return
        if size == self._offset:
            return

        offset_before = self._offset
        try:
            raw = await asyncio.to_thread(_read_segment, path, offset_before, size)
        except OSError:
            logger.warning("chatlog read failed", extra={"path": str(path)}, exc_info=True)
            return

        # Non-atomic writes can split a multi-byte char at the read boundary:
        # decoding the half char would raise and the offset would never
        # advance, wedging the watcher in an error loop. Rewind to the
        # previous char boundary; the fragment is left for the next poll.
        cut = 0
        while cut < len(raw) and cut < 4:
            try:
                raw[cut:].decode("utf-8")
                break
            except UnicodeDecodeError:
                cut += 1
        else:
            cut = 0
        raw = raw[cut:].decode("utf-8", errors="replace")

        # Only advance past complete lines: a partial trailing line must be
        # left for the next poll (§5.3). Skipping it would move the offset
        # past it forever and the line would never be parsed.
        last_newline = raw.rfind("\n")
        if last_newline < 0:
            return
        self._offset = offset_before + last_newline + 1
        lines = self._parse_lines(raw[: last_newline + 1])
        if lines:
            self._on_new_lines(lines)

    def _parse_lines(self, raw: str) -> list[ChatlogLine]:
        out: list[ChatlogLine] = []
        for raw_line in raw.splitlines():
            try:
                data = json.loads(raw_line)
            except (json.JSONDecodeError, ValueError):
                continue  # partial trailing line, read next poll
            # chat_session_id in the chatlog equals the LivaChat session_id.
            # Empty (unattributable) lines are skipped.
            sid = data.get("chat_session_id", "")
            if sid != self._context.session_id:
                continue
            role = data.get("role", "")
            text = data.get("text", "")
            ts = data.get("ts", 0) or 0
            if role in (_USER_ROLE, _ASSISTANT_ROLE):
                out.append(ChatlogLine(role=role, text=text, ts_ms=int(ts)))
        return out

    # -- trigger rules --------------------------------------------------------

    def _on_new_lines(self, lines: list[ChatlogLine]) -> None:
        self._pending.extend(lines)
        if any(line.role == _USER_ROLE for line in lines):
            if self._can_nudge_now():
                asyncio.create_task(self._flush())
            else:
                self._schedule_coalesce()
        # Assistant-only growth: merge into the pending batch and wait for the
        # next user-triggered flush. Never triggers on its own.

    def _can_nudge_now(self) -> bool:
        if self._nudge_in_flight or self._flushing:
            return False
        return (time.time() - self._last_nudge_at) >= self._nudge_min_interval_s

    def _schedule_coalesce(self, delay: float | None = None) -> None:
        if self._coalesce_task is not None and not self._coalesce_task.done():
            return
        if delay is None:
            delay = self._nudge_coalesce_s
        self._coalesce_task = asyncio.create_task(self._coalesce_worker(delay))

    async def _coalesce_worker(self, delay: float) -> None:
        await asyncio.sleep(delay)
        # Wait until the min-interval / in-flight conditions clear.
        while self._pending and not self._can_nudge_now():
            await asyncio.sleep(0.2)
        if self._pending:
            await self._flush()

    async def _flush(self) -> None:
        if self._flushing or not self._pending:
            return
        if not any(line.role == _USER_ROLE for line in self._pending):
            # Assistant-only batch: wait for a user line before nudging.
            return
        self._flushing = True
        try:
            lines, truncated = self._truncate(self._pending)
            self._pending = []
            update = ChatlogUpdate(
                path=self._path or "",
                chat_session_id=self._context.session_id,
                new_lines=lines,
                truncated=truncated,
                outstanding_jobs=await self._outstanding_jobs(),
            )
            for adapter in self._adapters:
                await self._nudge_adapter(adapter, update)
            self._last_nudge_at = time.time()
        finally:
            self._flushing = False
            if self._pending:
                self._schedule_coalesce(0.0)

    async def _nudge_adapter(self, adapter: AgentAdapter, update: ChatlogUpdate) -> None:
        self._nudge_in_flight = True
        try:
            async for chunk in adapter.nudge(update, self._context):
                if chunk.text and self._push_callback is not None:
                    await self._push_callback(
                        adapter.name, self._device_id, self._session_id, chunk
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "adapter nudge failed",
                extra={"adapter": adapter.name, "session_id": self._session_id},
                exc_info=True,
            )
        finally:
            self._nudge_in_flight = False

    def _truncate(self, lines: list[ChatlogLine]) -> tuple[list[ChatlogLine], bool]:
        truncated = False
        if len(lines) > self._nudge_max_lines:
            lines = lines[-self._nudge_max_lines:]
            truncated = True
        while lines and sum(len(line.text) for line in lines) > self._nudge_max_bytes:
            lines = lines[1:]
            truncated = True
        return lines, truncated

    async def _outstanding_jobs(self) -> list[dict]:
        if self._job_store is None:
            return []
        try:
            # R3: device 维度 —— 新 chat session 必须看到旧 session 派的 job
            return await self._job_store.outstanding_jobs_for_device(self._device_id)
        except Exception:
            logger.warning("failed to query outstanding jobs", exc_info=True)
            return []

    # -- path resolution ------------------------------------------------------

    async def _resolve_path(self) -> str | None:
        return await resolve_chatlog_path(
            self._recording_lookup,
            device_id=self._device_id,
            base_dir=self._base_dir,
        )

    async def _notify_path_change(self) -> None:
        """Tell the main agent the listening target changed (cross-day rollover)."""
        update = ChatlogUpdate(
            path=self._path or "",
            chat_session_id=self._context.session_id,
            new_lines=[],
            truncated=False,
            outstanding_jobs=await self._outstanding_jobs(),
        )
        for adapter in self._adapters:
            await self._nudge_adapter(adapter, update)

    @staticmethod
    def _file_size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
