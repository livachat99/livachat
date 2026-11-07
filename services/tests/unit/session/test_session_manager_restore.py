"""SessionManager chat store restore 单测（chatserver_sqlite_store.md §3.6）。

去 Redis 后（chatserver_no_redis.md §3.4/D1）：chat store restore 是唯一
恢复路径——restored 恒 False、epoch 恒 0；客户端带 resume_id 时 session_id
延续；restore 失败不阻断会话建立。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from session.manager import SessionManager
from shared.persistence.chat_store import ChatStore
from shared.utils.time import now_ms


async def _seeded_store(tmp_path) -> ChatStore:
    """启动真实 ChatStore 并写入今日一行 asr（含图）、一行 llm、一行 agent_job。"""
    store = ChatStore(str(tmp_path / "chat_server.db"))
    await store.start()
    store.on_device(device_uuid="dev-1", user_id="u1",
                    client_version="android-1", timezone="Asia/Shanghai")
    store.on_message(device_uuid="dev-1", session_id="s-old", kind="asr",
                     role="user", content="你好", ts_ms=now_ms(),
                     images=["data:image/jpeg;base64,AAA"])
    store.on_message(device_uuid="dev-1", session_id="s-old", kind="llm",
                     role="assistant", content="你好！", ref_id="msg_1", ts_ms=now_ms())
    store.on_message(device_uuid="dev-1", session_id="s-old", kind="agent_job",
                     role="assistant", content="[Agent job result]\n完成", ref_id="msg_j1",
                     ts_ms=now_ms())
    await store._queue.join()
    await store.stop()
    return store


class TestRestoreFromChatStore:
    async def test_non_resume_restores_history(self, tmp_path):
        store = await _seeded_store(tmp_path)
        mgr = SessionManager(chat_store=store)

        ctx, history = await mgr.start(
            device_id="dev-1", timezone="Asia/Shanghai",
        )

        assert ctx.restored is False
        entries = history.get_recent()
        assert [e["role"] for e in entries] == ["user", "assistant", "assistant"]
        # asr → user，images 原样
        assert entries[0]["content"] == "你好"
        assert entries[0]["images"] == ["data:image/jpeg;base64,AAA"]
        # llm → assistant
        assert entries[1]["content"] == "你好！"
        # agent_job → assistant + source/ref_id（has_agent_job 去重语义保留）
        assert entries[2]["content"] == "[Agent job result]\n完成"
        assert entries[2]["source"] == "agent_job"
        assert entries[2]["ref_id"] == "msg_j1"
        assert history.has_agent_job("msg_j1")

    async def test_empty_store_leaves_history_empty(self, tmp_path):
        store = ChatStore(str(tmp_path / "chat_server.db"))
        await store.start()
        await store.stop()
        mgr = SessionManager(chat_store=store)

        _, history = await mgr.start(device_id="dev-1", timezone="UTC")

        assert history.is_empty

    async def test_resume_id_keeps_session_and_restores_from_chat_store(self, tmp_path):
        """D1：客户端带 resume_id 重连 → session_id 延续（resume_id 保留）、
        restored 恒 False、WorkingMemory 从 chat store 恢复。"""
        store = await _seeded_store(tmp_path)
        mgr = SessionManager(chat_store=store)

        ctx, history = await mgr.start(
            device_id="dev-1", user_id="u1", timezone="UTC", resume_id="sess_resume",
        )

        assert ctx.session_id == "sess_resume"  # resume_id 延续（L109 现状不变）
        assert ctx.restored is False  # D1：恒 False
        assert ctx.epoch == 0  # D1：恒 0
        entries = history.get_recent()
        assert len(entries) == 3  # chat store 恢复（比旧 snapshot 的 6 条更多维度）

    async def test_restore_failure_does_not_block_session(self, tmp_path):
        """load_day 抛异常 → 只 log，会话正常建立。"""
        store = ChatStore(str(tmp_path / "chat_server.db"))
        await store.start()
        await store.stop()
        store.load_day = MagicMock(side_effect=RuntimeError("corrupt db"))
        mgr = SessionManager(chat_store=store)

        ctx, history = await mgr.start(device_id="dev-1", timezone="UTC")

        assert ctx.session_id.startswith("sess_")
        assert history.is_empty
