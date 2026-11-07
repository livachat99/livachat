"""PushPublisher — 进程内推送投递（chatserver_no_redis.md §3.2.2 + §4.6）。

替代 `push:{instance_id}` Redis 通道：relay 与 gateway 同进程，直接经
ConnectionManager 查 handler 并 enqueue_outbound，进程内投递即终态。
- 离线（无 handler）→ 返回 False（语义与今 resolve_instance is None 相同）；
- 背压兜底（§4.6）：`asyncio.wait_for(enqueue_outbound, 2.0)`——客户端卡死
  且出站队列积满时，job 投递与状态扫描协程不被拖住；超时按投递失败返回
  False（done job 结果后续经 list_pending_delivery 补投，ai_text 丢弃）；
- agent job 结果入史（fd_pipeline_llm_v2）：PushMessage 且 job_key 非空且
  is_final → 帧后 handler.record_agent_job_result（先帧后写，客户端可见性
  优先；原 push_subscriber.py:105-112 行为迁入）；
- max_reply_bytes 截断逻辑保留（只截 ai_text，job_status 载荷有界不截）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from agent_relay.config import AgentRelayConfig
from agent_relay.metrics import inc_push, observe_push_latency
from agent_relay.models import JobStatusPushMessage, PushMessage
from shared.utils.time import now_ms

logger = logging.getLogger("livachat.agent_relay.publisher")

# §4.6：enqueue 阻塞上限——客户端卡死 + 出站队列积满时，投递协程最多等
# 2s 即按失败返回，防止 job 投递与状态扫描循环被拖住
_ENQUEUE_TIMEOUT_S = 2.0


class PushPublisher:
    """Publishes agent ai_text / job_status messages directly to the device
    connection handler (in-process, no Redis)."""

    def __init__(self, conn_mgr: Any, cfg: AgentRelayConfig) -> None:
        self._conn_mgr = conn_mgr
        self._max_bytes = cfg.max_reply_bytes
        self._enqueue_timeout_s = _ENQUEUE_TIMEOUT_S

    # -- public API -----------------------------------------------------------

    async def publish(self, msg: PushMessage | JobStatusPushMessage) -> bool:
        """Deliver a push message to the device's live connection.

        Returns True if enqueued, False if offline / backpressure timeout /
        error. max_reply_bytes truncation applies to ai_text text only.
        """
        handler = self._conn_mgr.get_handler(msg.device_id)
        if handler is None:
            inc_push("offline")
            logger.debug(
                "device offline, dropping agent reply",
                extra={"device_id": msg.device_id, "message_id": getattr(msg, "message_id", "")},
            )
            return False

        # Enforce max length (without mutating the original message)
        if isinstance(msg, PushMessage):
            text = msg.text
            if len(text) > self._max_bytes:
                msg = msg.model_copy(update={"text": text[: self._max_bytes]})

        data = json.loads(msg.to_json())
        if msg.kind == "job_status":
            frame = self._build_job_status_frame(data)
        else:
            frame = self._build_ai_text_frame(data)

        t0 = time.monotonic()
        try:
            await asyncio.wait_for(
                handler.enqueue_outbound(frame), timeout=self._enqueue_timeout_s
            )
        except TimeoutError:
            inc_push("backpressure_timeout")
            logger.warning(
                "push backpressure timeout, message dropped",
                extra={
                    "device_id": msg.device_id,
                    "message_id": getattr(msg, "message_id", ""),
                    "kind": getattr(msg, "kind", "ai_text"),
                },
            )
            return False
        except Exception:
            inc_push("error")
            logger.warning(
                "failed to enqueue push message",
                extra={"device_id": msg.device_id, "message_id": getattr(msg, "message_id", "")},
                exc_info=True,
            )
            return False
        observe_push_latency(time.monotonic() - t0)
        inc_push("ok")
        logger.debug(
            "push message enqueued",
            extra={
                "device_id": msg.device_id,
                "kind": getattr(msg, "kind", "ai_text"),
                "message_id": getattr(msg, "message_id", ""),
                "agent_name": getattr(msg, "agent_name", ""),
            },
        )

        # agent job 结果入史（fd_pipeline_llm_v2）：先帧后写，客户端可见性优先。
        # JobStatusPushMessage 无 job_key 字段，getattr 恒 None，无需 kind 检查。
        job_key = getattr(msg, "job_key", None)
        if job_key and getattr(msg, "is_final", True):
            handler.record_agent_job_result(
                text=getattr(msg, "text", ""),
                message_id=getattr(msg, "message_id", "") or f"msg_agent_job_{job_key}",
                agent_name=getattr(msg, "agent_name", "") or "",
                agent_id=getattr(msg, "agent_id", "") or "",
                job_key=job_key,
            )
        return True

    # -- frame construction（自 gateway/push_subscriber.py 迁入）--------------

    @staticmethod
    def _build_ai_text_frame(data: dict[str, Any]) -> str:
        """Build an ai_text frame conforming to wire protocol.

        Frame includes seq placeholder (replaced by writer) and timestamp.
        Optional fields (source, agent_name, reply_to, proactive) are
        included when present.
        """
        payload: dict[str, Any] = {
            "text": data.get("text", ""),
            "message_id": data.get("message_id", ""),
            "index": data.get("index", 0),
            "is_final": data.get("is_final", True),
        }

        # Optional agent-specific fields
        for field in ("source", "agent_name", "reply_to"):
            val = data.get(field)
            if val:
                payload[field] = val
        if data.get("proactive"):
            payload["proactive"] = True

        frame: dict[str, Any] = {
            "seq": 1,  # placeholder — writer assigns real seq at dequeue
            "timestamp": now_ms(),
            "type": "ai_text",
            "payload": payload,
        }
        return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _build_job_status_frame(data: dict[str, Any]) -> str:
        """Build a job_status frame conforming to wire protocol.

        payload.jobs is whitelist-passed per item (job_id / agent_name /
        summary / started_at_ms / running_time_ms); internal fields are
        never forwarded — same style as _build_ai_text_frame.
        """
        jobs: list[dict[str, Any]] = []
        for item in data.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            jobs.append({
                "job_id": item.get("job_id", ""),
                "agent_name": item.get("agent_name", "hermes"),
                "summary": item.get("summary", ""),
                "started_at_ms": item.get("started_at_ms", 0),
                "running_time_ms": item.get("running_time_ms", 0),
            })

        frame: dict[str, Any] = {
            "seq": 1,  # placeholder — writer assigns real seq at dequeue
            "timestamp": now_ms(),
            "type": "job_status",
            "payload": {"jobs": jobs},
        }
        return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
