"""ChatLogWriter — JSONL append writer for chat/history.jsonl.

直写模式：每条记录立即追加写盘并 flush，不做批量缓冲——final ASR/LLM
文本到达即落盘，避免"最后一条消息停留在 buffer"导致进程退出时丢失。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("livachat.recording")


class ChatLogWriter:
    """Append-only JSONL writer with immediate write-through."""

    def __init__(self, path: Path):
        self._path = path
        self._f = None

    # -- public API -------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> None:
        self._f = open(self._path, "a", encoding="utf-8")  # noqa: SIM115  # 流式 writer 跨多次 write 持 fd

    def write(
        self,
        *,
        role: str,
        text: str,
        ts_ms: int,
        ref_id: str = "",
        chat_session_id: str = "",
        interrupted: bool = False,
        source: str | None = None,
        images: list[str] | None = None,
        agent_name: str | None = None,
        agent_id: str | None = None,
        job_key: str | None = None,
    ) -> bool:
        sec, ms = divmod(ts_ms, 1000)
        utc_str = datetime.fromtimestamp(sec, tz=UTC).strftime(
            f"%Y-%m-%dT%H:%M:%S.{ms:03d}Z"
        )
        entry: dict[str, object] = {
            "ts": ts_ms,
            "utc": utc_str,
            "role": role,
            "text": text,
            "ref_id": ref_id,
            "chat_session_id": chat_session_id,
        }
        if interrupted:
            entry["interrupted"] = True
        if source:
            entry["source"] = source
        if images:
            entry["images"] = list(images)
        # agent job 行扩展（recording_data_process.md §2.5）：lvdata 侧据此铸造
        # agent_job 事件（event_ref 用 job_key，与旧 jobs_watch 事件对齐）
        if agent_name:
            entry["agent_name"] = agent_name
        if agent_id:
            entry["agent_id"] = agent_id
        if job_key:
            entry["job_key"] = job_key

        if self._f is None:
            logger.warning(f"chatlog write skipped, writer not open path={self._path}")
            return False
        self._f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._f.flush()
        return True

    def flush(self) -> None:
        """幂等：直写模式下无缓冲，保留以兼容调用方。"""
        if self._f is not None:
            self._f.flush()

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None
