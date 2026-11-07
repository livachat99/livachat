"""Unit tests for recording.chatlog — JSONL chat history writer."""

import json
import tempfile
from pathlib import Path

from recording.chatlog import ChatLogWriter


class TestChatLogWriter:
    def test_single_entry(self):
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        w.write(role="user", text="Hello", ts_ms=1712345683500, ref_id="t1", chat_session_id="s1")
        w.close()

        with open(tmp) as f:
            lines = f.readlines()
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["role"] == "user"
        assert obj["text"] == "Hello"
        assert obj["ref_id"] == "t1"
        assert obj["chat_session_id"] == "s1"
        assert "ts" in obj
        assert "utc" in obj

        tmp.unlink()
        tmp.parent.rmdir()

    def test_multiple_entries(self):
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        for i in range(5):
            w.write(role="user", text=f"msg{i}", ts_ms=1712345683500 + i * 1000,
                    chat_session_id="s")
        w.close()

        with open(tmp) as f:
            lines = f.readlines()
        assert len(lines) == 5

        tmp.unlink()
        tmp.parent.rmdir()

    def test_interrupted_flag(self):
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        w.write(role="assistant", text="Interrupted...", ts_ms=1712345683500,
                ref_id="m1", chat_session_id="s1", interrupted=True)
        w.close()

        with open(tmp) as f:
            obj = json.loads(f.readline())
        assert obj["interrupted"] is True

        tmp.unlink()
        tmp.parent.rmdir()

    def test_write_with_source(self):
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        w.write(role="assistant", text="agent result", ts_ms=1712345683500,
                ref_id="msg_agent_job_jk1", chat_session_id="s1", source="agent_job")
        w.close()

        with open(tmp) as f:
            obj = json.loads(f.readline())
        assert obj["source"] == "agent_job"
        assert obj["ref_id"] == "msg_agent_job_jk1"

        tmp.unlink()
        tmp.parent.rmdir()

    def test_write_without_source_omits_key(self):
        """LLM reply 行不带 source key（向后兼容，读取方 .get()）。"""
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        w.write(role="assistant", text="plain reply", ts_ms=1712345683500,
                ref_id="m1", chat_session_id="s1")
        w.close()

        with open(tmp) as f:
            obj = json.loads(f.readline())
        assert "source" not in obj

        tmp.unlink()
        tmp.parent.rmdir()

    def test_write_with_images(self):
        """images 行字段：非空才写（fd_pipeline_llm_v2）。"""
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        w.write(role="user", text="看看", ts_ms=1712345683500, ref_id="t1",
                chat_session_id="s1",
                images=["../image/0193f2c1-7a2b-7e3d-9c4a-1b2c3d4e5f60.jpg"])
        w.write(role="user", text="无图", ts_ms=1712345683600, chat_session_id="s1")
        w.close()

        with open(tmp) as f:
            objs = [json.loads(line) for line in f]
        assert objs[0]["images"] == ["../image/0193f2c1-7a2b-7e3d-9c4a-1b2c3d4e5f60.jpg"]
        assert "images" not in objs[1]

        tmp.unlink()
        tmp.parent.rmdir()

    def test_write_with_agent_fields(self):
        """agent job 行扩展字段（recording_data_process.md §2.5）：非空才写。"""
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        w.write(role="assistant", text="结果", ts_ms=1712345683500,
                ref_id="msg_agent_job_jk1", chat_session_id="s1",
                source="agent_job", agent_name="hermes", agent_id="小七",
                job_key="jk-1")
        w.write(role="assistant", text="普通回复", ts_ms=1712345683600,
                chat_session_id="s1")
        w.close()

        with open(tmp) as f:
            objs = [json.loads(line) for line in f]
        assert objs[0]["agent_name"] == "hermes"
        assert objs[0]["agent_id"] == "小七"
        assert objs[0]["job_key"] == "jk-1"
        for key in ("agent_name", "agent_id", "job_key"):
            assert key not in objs[1]

        tmp.unlink()
        tmp.parent.rmdir()

    def test_write_flushes_immediately(self):
        # 直写模式：write 后不 close 文件即已落盘（无需等待 flush 条件）
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        w.write(role="assistant", text="last message", ts_ms=1712345683500 + 999_000,
                chat_session_id="s")
        with open(tmp) as f:
            lines = f.readlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["text"] == "last message"
        w.close()

        tmp.unlink()
        tmp.parent.rmdir()

    def test_close_without_open(self):
        w = ChatLogWriter(Path("/tmp/nonexistent/test.jsonl"))
        w.close()  # should not raise

    def test_flush_empty(self):
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        w = ChatLogWriter(tmp)
        w.open()
        w.flush()  # should not raise
        w.close()

        with open(tmp) as f:
            assert f.read() == ""

        tmp.unlink()
        tmp.parent.rmdir()
