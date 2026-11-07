from __future__ import annotations

import pytest

from session.manager import SessionManager
from shared.persistence.chat_store import ChatStore
from shared.utils.time import now_ms

pytestmark = pytest.mark.integration


class TestResume:
    async def test_resume_id_keeps_session_restores_chat_store(self, tmp_path):
        """D1（chatserver_no_redis.md §4.1）：resume 统一走 chat store restore——
        session_id 延续（resume_id 保留）、restored 恒 False、epoch 恒 0、
        WorkingMemory 从 messages 表恢复。"""
        store = ChatStore(str(tmp_path / "chat_server.db"))
        await store.start()
        # 预置今日历史（asr + llm + agent_job 各一行）
        store.on_device(device_uuid="d1", user_id="u1",
                        client_version="t", timezone="UTC")
        store.on_message(device_uuid="d1", session_id="s-old", kind="asr",
                         role="user", content="hello", ts_ms=now_ms())
        store.on_message(device_uuid="d1", session_id="s-old", kind="llm",
                         role="assistant", content="hi", ts_ms=now_ms())
        store.on_message(
            device_uuid="d1", session_id="s-old", kind="agent_job",
            role="assistant", content="[Agent job result]\n明天晴 22 度",
            ref_id="msg_agent_job_jk1", ts_ms=now_ms())
        await store._queue.join()
        await store.stop()

        mgr = SessionManager(chat_store=store)
        sid = "sess_resume_1"

        ctx2, history2 = await mgr.start(device_id="d1", resume_id=sid)
        assert ctx2.session_id == sid          # resume_id 延续（现状不变）
        assert ctx2.restored is False          # D1：恒 False
        assert ctx2.epoch == 0                 # D1：恒 0
        assert len(history2) == 3
        job = next(e for e in history2.get_recent() if e.get("source") == "agent_job")
        assert job["ref_id"] == "msg_agent_job_jk1"
        assert job["content"].startswith("[Agent job result]")
        assert history2.has_agent_job("msg_agent_job_jk1")

    async def test_resume_without_history_restores_empty(self, tmp_path):
        store = ChatStore(str(tmp_path / "chat_server.db"))
        await store.start()
        await store.stop()
        mgr = SessionManager(chat_store=store)

        ctx2, history2 = await mgr.start(device_id="d1", resume_id="sess_x")
        assert ctx2.restored is False
        assert history2.is_empty
