from __future__ import annotations

import asyncio

from shared.observability.logging import get_logger

# structlog BoundLogger：kwargs 进事件字典；标准 logging 的 extra 会被 structlog
# foreign_pre_chain 丢弃（prev/received/msg_type 打不出来，历史教训）
logger = get_logger("livachat.gateway.seq")

# 乱序容忍的消息类型：video 帧相互独立、逐帧处理，丢帧代价（画面卡顿 + 客户端
# ack 缺口）高于乱序代价。客户端 video 的取号/编码在发送闸口外完成（Android
# SessionManager.encodeAndSendOutsideGate），推流期 seq 回退属常态。
_TOLERATED_TYPES = frozenset({"video"})


class InboundSeqGuard:
    """Detects sequence-number regression on client-sent frames.

    A regressed ``seq`` is logged, dropped, and counted as a metric. Types in
    ``_TOLERATED_TYPES`` are logged but still processed, and do not move the
    watermark.
    """

    def __init__(self) -> None:
        self._prev_seq: int | None = None

    def check(self, seq: int, *, msg_type: str = "?") -> bool:
        """Return True if *seq* is valid (>= previous), False if regressed."""
        if self._prev_seq is not None and seq < self._prev_seq:
            if msg_type in _TOLERATED_TYPES:
                # 不更新 _prev_seq：水位保持严格单调，容忍不打开后续真回退的口子
                logger.info(
                    "seq regression tolerated",
                    prev=self._prev_seq,
                    received=seq,
                    msg_type=msg_type,
                )
                return True
            logger.warning(
                "seq regression detected",
                prev=self._prev_seq,
                received=seq,
                msg_type=msg_type,
            )
            _inc_seq_error_metric()
            return False
        self._prev_seq = seq
        return True

    def reset(self) -> None:
        """Reset the guard for a new session."""
        self._prev_seq = None


class OutboundSeqAllocator:
    """Monotonic per-connection outbound sequence number allocator."""

    def __init__(self) -> None:
        self._seq: int = 0
        self._lock = asyncio.Lock()

    async def next(self) -> int:
        async with self._lock:
            self._seq += 1
            return self._seq


def _inc_seq_error_metric() -> None:
    try:
        from shared.observability.metrics import get_registry

        get_registry().errors_total().labels(code="seq_regress").inc()
    except Exception:
        pass
