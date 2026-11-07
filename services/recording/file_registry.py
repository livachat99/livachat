"""recording_files 登记适配器（recording_data_process.md §2.1）。

由 RecordingService 按会话构造并注入 RecordingSession：

- record()：文件**首次落盘后**登记新行（先落盘、后登记，永不反向）；
- touch()：文件内容变更（jsonl append）后更新 size + bump updated_at（严格
  单调由 ChatStore 保证）；session.json 静态化后无 touch 路径。

纪律：
- rel_path 相对 data root（`<device>/<session>/...`），与 lvdata 侧
  `file_path = root / rel_path` 推导一致；
- 失败（超时/异常）只告警、不向上抛（文件已在盘上，不得打断录制热路径），
  并落本地 reconcile 清单 `<root>/recording_files_reconcile.jsonl`（一行一失败，
  backfill CLI 兜底补登记，doc §2.1）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("livachat.recording.file_registry")

RECONCILE_FILENAME = "recording_files_reconcile.jsonl"


def uuid7_to_ms(stem: str) -> int | None:
    """uuid7 文件名 stem → epoch ms（前 48 bit）；非 uuid7 返回 None。"""
    try:
        return int(stem.replace("-", "")[:12], 16)
    except (ValueError, IndexError):
        return None


class RecordingFileRegistry:
    def __init__(self, chat_store: Any, data_root: Path, *,
                 device_uuid: str, recording_session_id: str) -> None:
        self._chat_store = chat_store
        self._root = Path(data_root)
        self._device_uuid = device_uuid
        self._recording_session_id = recording_session_id

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._root))
        except ValueError:
            # root 外的路径（配置错位）：告警 + 原样落库，便于对拍时暴露
            logger.warning(
                "recording file outside data root, storing absolute path",
                extra={"path": str(path), "root": str(self._root)},
            )
            return str(path)

    async def record(self, *, kind: str, path: Path, size_bytes: int,
                     ts_ms: int | None = None,
                     duration_ms: int | None = None) -> None:
        try:
            await self._chat_store.record_file(
                device_uuid=self._device_uuid,
                recording_session_id=self._recording_session_id,
                kind=kind,
                rel_path=self._rel(path),
                size_bytes=size_bytes,
                ts_ms=ts_ms,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error(
                "recording file registration failed",
                extra={"kind": kind, "rel_path": str(path)},
                exc_info=True,
            )
            self._reconcile(op="record", path=path, error=e)

    async def touch(self, *, path: Path, size_bytes: int) -> None:
        try:
            await self._chat_store.touch_file(
                rel_path=self._rel(path), size_bytes=size_bytes,
            )
        except Exception as e:
            logger.error(
                "recording file touch failed",
                extra={"rel_path": str(path)},
                exc_info=True,
            )
            self._reconcile(op="touch", path=path, error=e)

    def _reconcile(self, *, op: str, path: Path, error: Exception) -> None:
        """失败落本地 reconcile 清单（一行一失败；best-effort，再失败仅告警）。"""
        try:
            entry = json.dumps({
                "ts": datetime.now(tz=UTC).isoformat(timespec="milliseconds"),
                "op": op,
                "rel_path": self._rel(path),
                "error": str(error)[:300],
            }, ensure_ascii=False)
            with open(self._root / RECONCILE_FILENAME, "a",
                      encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:  # noqa: BLE001 — 清单本身失败不向上抛
            logger.error("reconcile list append failed",
                         extra={"rel_path": str(path)}, exc_info=True)
