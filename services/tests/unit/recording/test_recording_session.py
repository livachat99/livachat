"""Unit tests: RecordingSession lifecycle with concurrent connections.

A session is shared per device; detach (one connection dropping) must NOT
stop the writer — frames from a still-active connection would otherwise
fill the queue and be dropped.
"""

from __future__ import annotations

import asyncio

import pytest

from recording.ogg import OggOpusWriter
from recording.session import RecordingSession, _RecordingItem
from recording.sink import ChatImageFrame


def _make_session(tmp_path) -> RecordingSession:
    return RecordingSession(
        recording_session_id="rec-1",
        device_uuid="dev-1",
        session_date="2026-08-10",
        storage_path=tmp_path / "rec-1",
        config={},
    )


async def _feed(s: RecordingSession, n: int, chunk: int = 100) -> None:
    for i in range(n):
        s.on_media(kind="audio", data=b"\x00" * 20, duration_ms=20, ts_ms=0)
        if i % chunk == 0:
            await asyncio.sleep(0)  # let the writer consume


async def test_detach_keeps_writer_consuming(tmp_path):
    s = _make_session(tmp_path)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        await _feed(s, 200)
        # One connection drops while another is still active.
        await s.detach()
        assert s.status == "idle"
        await _feed(s, 3000)  # > queue maxsize (2048); must not drop
        await asyncio.sleep(0.2)  # writer drains the queue

        assert s._writer_task is not None and not s._writer_task.done()
        assert s.audio_frames >= 3000  # no frames dropped after detach
    finally:
        await s.finalize()


async def test_detach_flushes_audio_segment(tmp_path):
    s = _make_session(tmp_path)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        await _feed(s, 50)
        await asyncio.sleep(0.05)
        segments_before = s.audio_segments
        await s.detach()
        assert s.audio_segments == segments_before + 1  # half segment flushed
        assert s._ogg_writer is None
        # 原子写：正式 .ogg 存在，无 .part 残留
        audio_dir = s.storage_path / "audio"
        assert any(p.suffix == ".ogg" for p in audio_dir.iterdir())
        assert not any(p.name.endswith(".part") for p in audio_dir.iterdir())
    finally:
        await s.finalize()


async def test_close_segment_keeps_reference_on_error(tmp_path, monkeypatch):
    """P1 回归：close 抛异常（磁盘满）时保留 _ogg_writer 引用，重试可成功。

    失败即置 None 会 fd 泄漏 + _flush_all 失去重试机会（.part 永残）。
    """
    s = _make_session(tmp_path)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        await _feed(s, 20)
        await asyncio.sleep(0.05)
        assert s._ogg_writer is not None

        real_close = OggOpusWriter.close

        def boom_close(_self):
            raise OSError("disk full")

        monkeypatch.setattr(OggOpusWriter, "close", boom_close)
        with pytest.raises(OSError):
            s._close_audio_segment()
        assert s._ogg_writer is not None  # 引用保留，可重试

        monkeypatch.setattr(OggOpusWriter, "close", real_close)
        final = s._close_audio_segment()  # 重试成功
        assert final is not None and final.suffix == ".ogg"
        assert final.is_file()
        assert s._ogg_writer is None
    finally:
        await s.finalize()


async def test_attach_reuses_running_writer(tmp_path):
    s = _make_session(tmp_path)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        await s.detach()
        writer_task = s._writer_task
        await s.attach()
        # Writer must not be duplicated while the old one is still running.
        assert s._writer_task is writer_task
        assert not s._writer_task.done()
        assert s.status == "active"
    finally:
        await s.finalize()


async def test_session_json_written_once_and_static(tmp_path):
    """静态站位文件（session_json_refactor §3.A/§3.D）：start 后仅 7 保留键；
    timezone 上报与 finalize 均不改动文件（内容 + mtime 不变）。"""
    import json
    import re

    s = _make_session(tmp_path)
    await s.start()
    path = tmp_path / "rec-1" / "session.json"
    content = path.read_text()
    payload = json.loads(content)
    assert set(payload) == {
        "schema_version", "recording_session_id", "device_uuid",
        "user_uuid", "session_date", "started_at", "config",
    }
    # started_at 规范格式：毫秒 + Z（§3.A.1）
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z",
                        payload["started_at"])
    mtime = path.stat().st_mtime_ns

    s.on_session_config(audio_sample_rate=16000, timezone="+08:00")
    await s.finalize()

    assert path.read_text() == content
    assert path.stat().st_mtime_ns == mtime


async def test_on_chat_text_source_passthrough(tmp_path):
    """source 参数经队列 → _handle_text → ChatLogWriter 透传（fd_pipeline_llm_v2）。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        s.on_chat_text(role="assistant", text="agent result", ts_ms=1712345683500,
                       ref_id="msg_agent_job_jk1", chat_session_id="s1",
                       source="agent_job")
        await asyncio.sleep(0.05)  # writer 消费
    finally:
        await s.finalize()

    lines = (tmp_path / "rec-1" / "chat" / "history.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["role"] == "assistant"
    assert obj["text"] == "agent result"
    assert obj["ref_id"] == "msg_agent_job_jk1"
    assert obj["source"] == "agent_job"
    assert s.chat_messages == 1


async def test_on_chat_text_without_source_writes_no_key(tmp_path):
    """既有调用点不传 source → 行无 source key（向后兼容）。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        s.on_chat_text(role="user", text="hi", ts_ms=1712345683500,
                       ref_id="tr-1", chat_session_id="s1")
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    lines = (tmp_path / "rec-1" / "chat" / "history.jsonl").read_text().strip().splitlines()
    obj = json.loads(lines[0])
    assert "source" not in obj


# ---------------------------------------------------------------------------
# chatlog 携带 image（fd_pipeline_llm_v2）
# ---------------------------------------------------------------------------


def _cif(data: bytes = b"\xff\xd8jpeg1") -> ChatImageFrame:
    return ChatImageFrame(data=data, width=640, height=480, orientation=90, camera_facing="front")


async def test_on_chat_text_with_frames_writes_images_and_row(tmp_path):
    """帧 → image/<uuid7>.jpg（bytes 一致）+ 行 images 相对路径可解析且存在。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        s.on_chat_text(role="user", text="看看这个", ts_ms=1712345685000,
                       ref_id="trans_abcd12345678", chat_session_id="s1",
                       frames=[_cif()])
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    base = tmp_path / "rec-1"
    lines = (base / "chat" / "history.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    (rel,) = obj["images"]
    assert rel.startswith("../image/") and rel.endswith(".jpg")
    img_path = (base / "chat" / rel).resolve()
    assert img_path.is_file()
    assert img_path.read_bytes() == b"\xff\xd8jpeg1"
    # 无 .part 残留（_write_file_atomic 落定）
    assert not any(p.name.endswith(".part") for p in (base / "image").iterdir())
    assert s.chat_messages == 1


async def test_on_chat_text_multiple_frames(tmp_path):
    """K>1 天然兼容：多帧 → 多文件，行内路径一一对应。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        s.on_chat_text(role="user", text="两帧", ts_ms=1712345685100,
                       ref_id="trans_abcd12345678", chat_session_id="s1",
                       frames=[_cif(b"one"), _cif(b"two")])
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    base = tmp_path / "rec-1"
    obj = json.loads((base / "chat" / "history.jsonl").read_text().strip())
    assert len(obj["images"]) == 2
    bytes_written = [((base / "chat" / rel).resolve()).read_bytes() for rel in obj["images"]]
    assert bytes_written == [b"one", b"two"]


async def test_frame_write_failure_skips_only_failed_frame(tmp_path, monkeypatch):
    """单帧写失败跳过该帧 → images 只含成功帧（无悬空引用）；行照写。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    real = RecordingSession._write_file_atomic
    calls = {"n": 0}

    def flaky(filepath, data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real(filepath, data)

    monkeypatch.setattr(RecordingSession, "_write_file_atomic", staticmethod(flaky))
    try:
        s.on_chat_text(role="user", text="两帧", ts_ms=1712345685200,
                       ref_id="trans_abcd12345678", chat_session_id="s1",
                       frames=[_cif(b"bad"), _cif(b"good")])
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    base = tmp_path / "rec-1"
    obj = json.loads((base / "chat" / "history.jsonl").read_text().strip())
    assert len(obj["images"]) == 1
    assert ((base / "chat" / obj["images"][0]).resolve()).read_bytes() == b"good"


async def test_all_frames_write_failure_omits_images(tmp_path, monkeypatch):
    """全部帧写失败：行照写文本、省略 images（文本不因图失败而丢失）。"""
    import json

    s = _make_session(tmp_path)
    await s.start()

    def boom(filepath, data):
        raise OSError("disk full")

    monkeypatch.setattr(RecordingSession, "_write_file_atomic", staticmethod(boom))
    try:
        s.on_chat_text(role="user", text="仍要文本", ts_ms=1712345685300,
                       ref_id="trans_abcd12345678", chat_session_id="s1",
                       frames=[_cif()])
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    obj = json.loads((tmp_path / "rec-1" / "chat" / "history.jsonl").read_text().strip())
    assert obj["text"] == "仍要文本"
    assert "images" not in obj


def test_queue_full_drops_text_and_frames_together(tmp_path):
    """队列满整项丢弃：文本与图同 item——要么都有要么都没有（不落任何文件）。"""
    s = _make_session(tmp_path)
    s._queue = asyncio.Queue(maxsize=1)
    s._queue.put_nowait(_RecordingItem(kind="video", data=b"blocker"))

    s.on_chat_text(role="user", text="丢弃", ts_ms=1712345685400,
                   ref_id="trans_abcd12345678", chat_session_id="s1",
                   frames=[_cif()])

    assert s._queue.qsize() == 1  # 未入队（整项丢弃）
    assert not (tmp_path / "rec-1" / "image").exists()


# ---------------------------------------------------------------------------
# transcribe.jsonl（fd_pipeline_transcribe_mode.md §4.5 / §10 用例 12-14）
# ---------------------------------------------------------------------------


async def test_on_transcribe_text_writes_transcribe_jsonl(tmp_path):
    """用例 12：start() 后 transcribe.jsonl 存在；on_transcribe_text 条目写入
    该文件，history.jsonl 无对应行；chat_messages 计数不增。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        assert (tmp_path / "rec-1" / "chat" / "transcribe.jsonl").exists()
        s.on_transcribe_text(text="现场会议内容", ts_ms=1712345683600,
                             ref_id="trans_abcd12345678", chat_session_id="s1")
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    t_lines = (tmp_path / "rec-1" / "chat" / "transcribe.jsonl").read_text().strip().splitlines()
    assert len(t_lines) == 1
    obj = json.loads(t_lines[0])
    assert obj["role"] == "user"
    assert obj["text"] == "现场会议内容"
    assert obj["ref_id"] == "trans_abcd12345678"
    assert obj["source"] == "transcribe"
    assert obj["ts"] == 1712345683600
    assert obj["utc"]
    assert obj["chat_session_id"] == "s1"

    h_lines = (tmp_path / "rec-1" / "chat" / "history.jsonl").read_text().strip()
    assert h_lines == ""  # history.jsonl 无转录行
    assert s.chat_messages == 0  # 转录文本轨不计入对话计数


async def test_on_transcribe_text_mixed_with_chat_text(tmp_path):
    """转录与对话文本混写：各行归各自文件，chat_messages 只计对话。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        s.on_transcribe_text(text="旁听语音", ts_ms=1712345683700, ref_id="tr-t1", chat_session_id="s1")
        s.on_chat_text(role="user", text="对话发言", ts_ms=1712345683800,
                       ref_id="tr-c1", chat_session_id="s1")
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    t_lines = (tmp_path / "rec-1" / "chat" / "transcribe.jsonl").read_text().strip().splitlines()
    h_lines = (tmp_path / "rec-1" / "chat" / "history.jsonl").read_text().strip().splitlines()
    assert [json.loads(line)["text"] for line in t_lines] == ["旁听语音"]
    assert [json.loads(line)["text"] for line in h_lines] == ["对话发言"]
    assert s.chat_messages == 1


async def test_on_chat_text_translate_routes_to_translate_jsonl(tmp_path):
    """translate 模式：user 原话 + assistant 译文落 chat/translate.jsonl
    （source="translate"），history.jsonl 无对应行，chat_messages 不增。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        assert (tmp_path / "rec-1" / "chat" / "translate.jsonl").exists()
        s.on_chat_text(role="user", text="你好世界", ts_ms=1712345684100,
                       ref_id="trans_abcd12345678", chat_session_id="s1",
                       source="translate")
        s.on_chat_text(role="assistant", text="Hello world", ts_ms=1712345684200,
                       ref_id="msg_abc123456789", chat_session_id="s1",
                       source="translate")
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    lines = (tmp_path / "rec-1" / "chat" / "translate.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    user_obj, assistant_obj = (json.loads(line) for line in lines)
    assert user_obj["role"] == "user"
    assert user_obj["text"] == "你好世界"
    assert user_obj["source"] == "translate"
    assert assistant_obj["role"] == "assistant"
    assert assistant_obj["text"] == "Hello world"
    assert assistant_obj["source"] == "translate"
    assert assistant_obj["ref_id"] == "msg_abc123456789"

    assert (tmp_path / "rec-1" / "chat" / "history.jsonl").read_text().strip() == ""
    assert s.chat_messages == 0


async def test_lecture_source_rows_route_to_translate_jsonl(tmp_path):
    """lecture 同传行（source="lecture"）与 dialogue translate（source="translate"）
    同落 translate.jsonl，行 source 各自保留可区分；history.jsonl 无对应行。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        s.on_chat_text(role="user", text="对话译原文", ts_ms=1712345684600,
                       ref_id="tr-d1", chat_session_id="s1", source="translate")
        s.on_chat_text(role="user", text="演讲原文", ts_ms=1712345684700,
                       ref_id="tr-l1", chat_session_id="s1", source="lecture")
        s.on_chat_text(role="assistant", text="同传译文", ts_ms=1712345684800,
                       ref_id="msg-l1", chat_session_id="s1", source="lecture")
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    lines = (tmp_path / "rec-1" / "chat" / "translate.jsonl").read_text().strip().splitlines()
    objs = [json.loads(line) for line in lines]
    assert [(o["role"], o["source"]) for o in objs] == [
        ("user", "translate"), ("user", "lecture"), ("assistant", "lecture"),
    ]
    assert objs[2]["text"] == "同传译文"
    assert (tmp_path / "rec-1" / "chat" / "history.jsonl").read_text().strip() == ""
    assert s.chat_messages == 0


async def test_translate_mixed_with_chat_and_agent_rows(tmp_path):
    """translate/chat/agent_job 混写：各行归各自文件，chat_messages 只计对话轨。"""
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        s.on_chat_text(role="user", text="翻译内容", ts_ms=1712345684300,
                       ref_id="tr-t1", chat_session_id="s1", source="translate")
        s.on_chat_text(role="user", text="普通对话", ts_ms=1712345684400,
                       ref_id="tr-c1", chat_session_id="s1")
        s.on_chat_text(role="assistant", text="agent 结果", ts_ms=1712345684500,
                       ref_id="msg_agent_job_x", chat_session_id="s1",
                       source="agent_job")
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    t_lines = (tmp_path / "rec-1" / "chat" / "translate.jsonl").read_text().strip().splitlines()
    h_lines = (tmp_path / "rec-1" / "chat" / "history.jsonl").read_text().strip().splitlines()
    assert [json.loads(line)["text"] for line in t_lines] == ["翻译内容"]
    assert [json.loads(line)["text"] for line in h_lines] == ["普通对话", "agent 结果"]
    assert s.chat_messages == 2  # 对话轨 = chat + agent_job；translate 不计


async def test_transcribe_writer_attach_reopen_and_finalize(tmp_path):
    """用例 14：attach() 重开后 writer 续写不丢；finalize 后文件完整关闭。

    detach 只关音频段、文本 writer 保持存活（重连续写）；attach 在 writer 已
    flush 关闭（置 None 模拟）时重开并续写。
    """
    import json

    s = _make_session(tmp_path)
    await s.start()
    try:
        s.on_transcribe_text(text="第一条", ts_ms=1712345683900, ref_id="tr-1", chat_session_id="s1")
        await asyncio.sleep(0.05)
        await s.detach()
        assert s.status == "idle"
        # detach 后 writer 仍存活 → 直接续写不丢
        s.on_transcribe_text(text="第二条", ts_ms=1712345683930, ref_id="tr-2", chat_session_id="s1")
        # 模拟 writer 已被 flush 关闭的 idle 态：attach 应重开并续写
        s._chat_writer = None
        s._transcribe_writer = None
        await s.attach()
        assert s._chat_writer is not None and s._transcribe_writer is not None
        s.on_transcribe_text(text="第三条", ts_ms=1712345683950, ref_id="tr-3", chat_session_id="s1")
        await asyncio.sleep(0.05)
    finally:
        await s.finalize()

    lines = (tmp_path / "rec-1" / "chat" / "transcribe.jsonl").read_text().strip().splitlines()
    assert [json.loads(line)["text"] for line in lines] == ["第一条", "第二条", "第三条"]
    assert s._transcribe_writer is None  # finalize → _flush_all 关闭
    assert s._chat_writer is None
