"""PushPublisher（进程内投递，§3.2.2 + §4.6）单元测试。

覆盖：在线投递（帧构造 + job_key 入史）、离线 False、截断、job_status 帧、
背压超时按失败返回 + metric、handler 异常按失败返回。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_relay.config import AgentRelayConfig
from agent_relay.models import JobStatusItem, JobStatusPushMessage, PushMessage
from agent_relay.publisher import PushPublisher


class FakeHandler:
    """最小 ConnectionHandler 替身：记录入队帧与 agent job 结果入史调用。"""

    def __init__(self, *, block: bool = False, raise_on_enqueue: bool = False):
        self.enqueued: list[str] = []
        self.job_results: list[dict] = []
        self._block = block
        self._raise = raise_on_enqueue

    async def enqueue_outbound(self, frame: str) -> None:
        if self._raise:
            raise RuntimeError("handler crashed")
        if self._block:
            await asyncio.Event().wait()  # 模拟客户端卡死（队列积满）
        self.enqueued.append(frame)

    def record_agent_job_result(self, *, text: str, message_id: str,
                                agent_name: str = "", agent_id: str = "",
                                job_key: str = "") -> None:
        self.job_results.append({
            "text": text, "message_id": message_id,
            "agent_name": agent_name, "agent_id": agent_id, "job_key": job_key,
        })


class FakeConnMgr:
    def __init__(self, handler: FakeHandler | None, device_id: str = "dev-1"):
        self._handler = handler
        self._device_id = device_id

    def get_handler(self, device_id: str) -> FakeHandler | None:
        return self._handler if device_id == self._device_id else None


@pytest.fixture
def cfg():
    return AgentRelayConfig()


def _msg(**overrides) -> PushMessage:
    kwargs = {
        "device_id": "dev-1",
        "session_id": "sess-1",
        "message_id": "msg_agent_01",
        "text": "hello",
        "agent_name": "hermes",
    }
    kwargs.update(overrides)
    return PushMessage(**kwargs)


class TestPushPublisher:
    @pytest.mark.asyncio
    async def test_publish_to_online_handler(self, cfg):
        handler = FakeHandler()
        publisher = PushPublisher(FakeConnMgr(handler), cfg)
        ok = await publisher.publish(_msg())
        assert ok is True
        assert len(handler.enqueued) == 1
        frame = json.loads(handler.enqueued[0])
        assert frame["type"] == "ai_text"
        assert frame["payload"]["text"] == "hello"
        assert frame["payload"]["agent_name"] == "hermes"
        assert frame["payload"]["message_id"] == "msg_agent_01"

    @pytest.mark.asyncio
    async def test_device_offline_returns_false(self, cfg):
        publisher = PushPublisher(FakeConnMgr(None), cfg)
        ok = await publisher.publish(_msg(device_id="dev-offline"))
        assert ok is False

    @pytest.mark.asyncio
    async def test_truncates_long_text(self, cfg):
        cfg = AgentRelayConfig(max_reply_bytes=10)
        handler = FakeHandler()
        publisher = PushPublisher(FakeConnMgr(handler), cfg)
        ok = await publisher.publish(_msg(text="x" * 100))
        assert ok is True
        frame = json.loads(handler.enqueued[0])
        assert len(frame["payload"]["text"]) <= 10

    @pytest.mark.asyncio
    async def test_job_result_ingested_after_frame(self, cfg):
        """fd_pipeline_llm_v2：PushMessage 带 job_key 且 is_final → 帧后入史；
        message_id 原值优先（delivery 恒传确定性幂等键 msg_agent_job_{key}）。"""
        handler = FakeHandler()
        publisher = PushPublisher(FakeConnMgr(handler), cfg)
        ok = await publisher.publish(
            _msg(text="结果", job_key="job-9", message_id="msg_agent_job_job-9"))
        assert ok is True
        assert handler.enqueued  # 先帧
        assert handler.job_results == [
            {"text": "结果", "message_id": "msg_agent_job_job-9",
             "agent_name": "hermes", "agent_id": "", "job_key": "job-9"}
        ]

    @pytest.mark.asyncio
    async def test_job_result_forwards_agent_identity(self, cfg):
        """agent_name/agent_id/job_key 原样转发（delivery 注入 adapter nickname，
        doc §2.5：落 chatlog 行供 lvdata 铸造 agent_job 事件）。"""
        handler = FakeHandler()
        publisher = PushPublisher(FakeConnMgr(handler), cfg)
        ok = await publisher.publish(_msg(
            text="结果", job_key="job-9", message_id="m1",
            agent_name="openclaw", agent_id="nick-1"))
        assert ok is True
        assert handler.job_results == [
            {"text": "结果", "message_id": "m1",
             "agent_name": "openclaw", "agent_id": "nick-1", "job_key": "job-9"}
        ]

    @pytest.mark.asyncio
    async def test_job_result_message_id_fallback(self, cfg):
        """message_id 为空 → fallback msg_agent_job_{job_key}（原 push_subscriber
        行为，push_subscriber.py:105-112）。"""
        handler = FakeHandler()
        publisher = PushPublisher(FakeConnMgr(handler), cfg)
        ok = await publisher.publish(_msg(text="结果", job_key="job-9", message_id=""))
        assert ok is True
        assert handler.job_results == [
            {"text": "结果", "message_id": "msg_agent_job_job-9",
             "agent_name": "hermes", "agent_id": "", "job_key": "job-9"}
        ]

    @pytest.mark.asyncio
    async def test_job_status_frame(self, cfg):
        handler = FakeHandler()
        publisher = PushPublisher(FakeConnMgr(handler), cfg)
        msg = JobStatusPushMessage(
            device_id="dev-1",
            jobs=[JobStatusItem(job_id="j1", summary="s1", started_at_ms=1000)],
        )
        ok = await publisher.publish(msg)
        assert ok is True
        frame = json.loads(handler.enqueued[0])
        assert frame["type"] == "job_status"
        assert frame["payload"]["jobs"][0]["job_id"] == "j1"
        # job_status 无 job_key → 不触发入史
        assert handler.job_results == []

    @pytest.mark.asyncio
    async def test_backpressure_timeout_returns_false(self, cfg):
        """§4.6：客户端卡死 + 出站队列积满 → enqueue 阻塞 → wait_for 超时按
        失败返回。"""
        handler = FakeHandler(block=True)
        publisher = PushPublisher(FakeConnMgr(handler), cfg)
        # 缩短超时便于测试
        publisher._enqueue_timeout_s = 0.05
        ok = await publisher.publish(_msg())
        assert ok is False
        assert handler.enqueued == []

    @pytest.mark.asyncio
    async def test_handler_error_returns_false(self, cfg):
        handler = FakeHandler(raise_on_enqueue=True)
        publisher = PushPublisher(FakeConnMgr(handler), cfg)
        ok = await publisher.publish(_msg())
        assert ok is False


def test_record_agent_job_result_signature_chain():
    """回归：publisher 以 agent_name/agent_id/job_key 调用；委托层与实现必须同签名。

    2026-09-15 曾因 gateway `ConnectionHandler`（委托层）漏改致 TypeError：
    投递被判失败、job 卡 done（重投）+ 结果未入 history（lvdata agent_job 事件缺失）。
    omni 模式自 2026-09-27 起由 DialogOrchestrator 的 `_omni_active` 分支承接
    （原 OmniOrchestrator 已删除，docs/fd_pipeline_omni_mode.md §4 D2/D7）。
    """
    import inspect

    from gateway.connection import ConnectionHandler
    from session.orchestrator import DialogOrchestrator

    for fn in (ConnectionHandler.record_agent_job_result,
               DialogOrchestrator.record_agent_job_result):
        params = inspect.signature(fn).parameters
        for name in ("text", "message_id", "agent_name", "agent_id", "job_key"):
            assert name in params, (fn.__qualname__, name)
