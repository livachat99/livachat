from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from avdetector.base import AvCheckTimeout
from avdetector.providers.alibaba_avdetector import (
    _MAX_WS_FRAME_BYTES,
    _RESIZE_EDGE_LADDER,
    _SEND_LIMIT,
    AlibabaAvDetectorProvider,
    _RealtimeAvDetectorStream,
)
from shared.utils.errors import ProviderError


class FakeWs:
    """最小 ws 替身：recv/async-for 从 incoming 取；send 记录并按脚本自动回应。"""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict] = []
        self.script_on_response: list[dict] = []
        self.script_on_commit: list[dict] = []
        self.closed = False
        self.protocol = MagicMock()
        self.protocol.state.name = "OPEN"

    def preload(self, *frames: dict) -> None:
        for f in frames:
            self.incoming.put_nowait(json.dumps(f))

    async def recv(self) -> str:
        return await self.incoming.get()

    def __aiter__(self):
        async def gen():
            while True:
                yield await self.incoming.get()

        return gen()

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        t = msg.get("type")
        if t == "response.create":
            for f in self.script_on_response:
                self.incoming.put_nowait(json.dumps(f))
        elif t == "input_audio_buffer.commit":
            for f in self.script_on_commit:
                self.incoming.put_nowait(json.dumps(f))

    async def close(self) -> None:
        self.closed = True


def _stream(ws: FakeWs) -> _RealtimeAvDetectorStream:
    update = {"event_id": "evt_session_update", "type": "session.update", "session": {}}
    return _RealtimeAvDetectorStream(ws, update, "wss://x", "key")


class TestHandshake:
    async def test_open_payload_manual_text_only(self) -> None:
        fake = FakeWs()
        fake.preload({"type": "session.created"}, {"type": "session.updated"})
        provider = AlibabaAvDetectorProvider(base_url="wss://x", api_key="k", model="m")

        async def fake_connect(*args, **kwargs):
            return fake

        with patch("avdetector.providers.alibaba_avdetector.websockets.connect", fake_connect):
            stream = await provider.open(
                sample_rate=16000, system_prompt="sys", generation_opts={"temperature": 0.2}
            )
        try:
            upd = next(m for m in fake.sent if m["type"] == "session.update")
            cfg = upd["session"]
            assert cfg["modalities"] == ["text"]
            assert cfg["turn_detection"] is None
            assert cfg["instructions"] == "sys"
            assert cfg["audio"]["input"]["format"]["sample_rate"] == 16000
            assert cfg["temperature"] == 0.2
        finally:
            await stream.close()

    async def test_open_handshake_mismatch_raises(self) -> None:
        fake = FakeWs()
        fake.preload({"type": "error", "error": {"message": "bad"}})
        provider = AlibabaAvDetectorProvider(base_url="wss://x", api_key="k", model="m")

        async def fake_connect(*args, **kwargs):
            return fake

        with (
            patch("avdetector.providers.alibaba_avdetector.websockets.connect", fake_connect),
            pytest.raises(ProviderError, match="session.created"),
        ):
            await provider.open(sample_rate=16000)
        assert fake.closed


class TestCheck:
    async def test_check_collects_text_transcripts_usage(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        await stream._start_receiver()
        ws.script_on_response = [
            {"type": "input_audio_buffer.committed"},
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": ""},
            {"type": "response.created", "response_id": "r1"},
            {"type": "response.text.delta", "delta": '{"event":"咳嗽"'},
            {"type": "response.text.delta", "delta": ',"desc":"听到咳嗽"}'},
            {"type": "response.done", "response": {"usage": {"total_tokens": 10}}},
        ]
        r = await stream.check(timeout_s=2)
        assert r.text == '{"event":"咳嗽","desc":"听到咳嗽"}'
        assert r.transcripts == [""]
        assert r.usage == {"total_tokens": 10}
        await stream._cancel_receiver()

    async def test_check_speech_transcript_is_veto_signal(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        await stream._start_receiver()
        ws.script_on_response = [
            {"type": "conversation.item.input_audio_transcription.completed",
             "transcript": "你好，这是一个测试"},
            {"type": "response.text.delta", "delta": "NONE"},
            {"type": "response.done", "response": {}},
        ]
        r = await stream.check(timeout_s=2)
        assert r.text == "NONE"
        assert r.transcripts == ["你好，这是一个测试"]
        await stream._cancel_receiver()

    async def test_check_ignores_stale_response_events(self) -> None:
        """[P4] 上一轮超时残响（非本轮 response_id 的 delta/done）不混入、不早退。"""
        ws = FakeWs()
        stream = _stream(ws)
        await stream._start_receiver()
        ws.script_on_response = [
            {"type": "response.created", "response_id": "r2"},
            {"type": "response.text.delta", "response_id": "r2", "delta": "NEW"},
            {"type": "response.text.delta", "response_id": "r1", "delta": "STALE"},
            {"type": "response.done", "response_id": "r1", "response": {}},
            {"type": "response.text.delta", "response_id": "r2", "delta": "!"},
            {"type": "response.done", "response_id": "r2",
             "response": {"usage": {"total_tokens": 7}}},
        ]
        r = await stream.check(timeout_s=2)
        assert r.text == "NEW!"  # 旧 delta 被滤、旧 done 未早退
        assert r.usage == {"total_tokens": 7}
        await stream._cancel_receiver()

    async def test_check_timeout_sends_cancel(self) -> None:
        """[P4] 超时放弃本轮时 best-effort response.cancel，掐掉在途响应的残响源。"""
        ws = FakeWs()
        stream = _stream(ws)
        await stream._start_receiver()
        with pytest.raises(AvCheckTimeout, match="timeout"):
            await stream.check(timeout_s=0.2)
        assert any(m["type"] == "response.cancel" for m in ws.sent)
        await stream._cancel_receiver()

    async def test_check_timeout(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        await stream._start_receiver()
        with pytest.raises(AvCheckTimeout, match="timeout"):
            await stream.check(timeout_s=0.2)
        await stream._cancel_receiver()

    async def test_check_error_frame(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        await stream._start_receiver()
        ws.script_on_response = [{"type": "error", "error": {"message": "boom"}}]
        with pytest.raises(ProviderError, match="boom"):
            await stream.check(timeout_s=2)
        await stream._cancel_receiver()

    async def test_check_dead_stream_raises(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        stream._dead = True
        with pytest.raises(ProviderError, match="dead"):
            await stream.check(timeout_s=2)
        assert not any(m["type"] == "response.create" for m in ws.sent)


class TestCommit:
    async def test_commit_noop_without_audio(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        await stream.commit_audio()
        assert not any(m["type"] == "input_audio_buffer.commit" for m in ws.sent)

    async def test_commit_settles_on_transcription(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        await stream._start_receiver()
        ws.script_on_commit = [
            {"type": "input_audio_buffer.committed"},
            {"type": "conversation.item.created"},
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": ""},
        ]
        await stream.feed_audio(b"\x00\x00")
        await stream.commit_audio()
        assert any(m["type"] == "input_audio_buffer.commit" for m in ws.sent)
        # 结算事件已被丢弃（队列清空），不影响后续 check 的窗口转写
        assert stream._queue.empty()
        await stream._cancel_receiver()

    async def test_commit_second_call_noop(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        await stream._start_receiver()
        ws.script_on_commit = [
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": ""},
        ]
        await stream.feed_audio(b"\x00\x00")
        await stream.commit_audio()
        n = sum(1 for m in ws.sent if m["type"] == "input_audio_buffer.commit")
        await stream.commit_audio()
        assert sum(1 for m in ws.sent if m["type"] == "input_audio_buffer.commit") == n
        await stream._cancel_receiver()

    async def test_commit_send_failure_reconnects_window_destroyed(self) -> None:
        """[P6.7] commit 发送失败 → 重连一次：陈旧窗口随旧连接销毁（不重发）。

        只告警继续的话，连接未死时服务端仍持有未提交音频——下一次 check 的
        response.create 会把它一并提交（陈旧转写误触发否决 / 过期事件）。
        """
        failing = FakeWs()

        async def fail_send(raw: str) -> None:
            raise ConnectionError("dead")

        failing.send = fail_send  # type: ignore[method-assign]
        ok = FakeWs()
        ok.preload({"type": "session.created"}, {"type": "session.updated"})

        async def fake_connect(*args, **kwargs):
            return ok

        stream = _stream(failing)
        stream._window_has_audio = True
        stream._queue.put_nowait(
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": "你好"}
        )
        with patch("avdetector.providers.alibaba_avdetector.websockets.connect", fake_connect):
            await stream.commit_audio()  # 重连成功 → 不抛
        assert failing.closed  # 旧连接已关闭（陈旧窗口随之销毁）
        assert not any(m["type"] == "input_audio_buffer.commit" for m in ok.sent)  # 新会话不重发
        assert stream._window_has_audio is False
        assert stream._queue.empty()  # 旧残留转写被 _drain 清掉
        await stream._cancel_receiver()

    async def test_commit_reconnect_failure_raises_dead(self) -> None:
        """[P6.7] 重连也失败 → 抛 ProviderError 且 _dead=True（monitor 兜底降级）。"""
        failing = FakeWs()

        async def fail_send(raw: str) -> None:
            raise ConnectionError("dead")

        failing.send = fail_send  # type: ignore[method-assign]

        async def fake_connect(*args, **kwargs):
            raise OSError("cannot connect")

        stream = _stream(failing)
        stream._window_has_audio = True
        with (
            patch("avdetector.providers.alibaba_avdetector.websockets.connect", fake_connect),
            pytest.raises(ProviderError, match="reconnect failed"),
        ):
            await stream.commit_audio()
        assert stream._dead is True


class TestFeed:
    async def test_feed_image_primes_silence_without_audio(self) -> None:
        """[P6] 静音自愈：窗口无音频 → 先补 20ms 静音再上图（G2/G6 实测）。"""
        import base64 as _b64

        ws = FakeWs()
        stream = _stream(ws)
        await stream.feed_image(b"\xff\xd8jpeg", width=640, height=480)
        types = [m["type"] for m in ws.sent]
        assert types == ["input_audio_buffer.append", "input_image_buffer.append"]
        assert _b64.b64decode(ws.sent[0]["audio"]) == b"\x00" * 640  # 20ms @16k/mono
        assert _b64.b64decode(ws.sent[1]["image"]) == b"\xff\xd8jpeg"

    async def test_feed_image_no_prime_when_audio_present(self) -> None:
        """[P6] 有窗口音频则不补（只有用户那次 append）。"""
        ws = FakeWs()
        stream = _stream(ws)
        await stream.feed_audio(b"\x00\x00")
        await stream.feed_image(b"\xff\xd8jpeg", width=640, height=480)
        appends = [m for m in ws.sent if m["type"] == "input_audio_buffer.append"]
        assert len(appends) == 1
        assert any(m["type"] == "input_image_buffer.append" for m in ws.sent)

    async def test_feed_image_reconnect_reprimes_on_fresh_session(self) -> None:
        """[review 修复] 图发送失败重连后，必须在新会话上重新自愈再重发。

        原 `_send_feeding` 重试路径会在重连（窗口清零）后裸发图 → 撞
        "append image before append audio"；本用例锁定修复：重连后先补静音。
        """
        failing = FakeWs()

        async def fail_send(raw: str) -> None:
            raise ConnectionError("dead")

        failing.send = fail_send  # type: ignore[method-assign]
        ok = FakeWs()
        ok.preload({"type": "session.created"}, {"type": "session.updated"})

        async def fake_connect(*args, **kwargs):
            return ok

        stream = _stream(failing)
        stream._window_has_audio = True  # 首会话窗口已有音频 → 首次不补
        with patch("avdetector.providers.alibaba_avdetector.websockets.connect", fake_connect):
            await stream.feed_image(b"\xff\xd8jpeg", width=640, height=480)
        wire = [m["type"] for m in ok.sent if m["type"] != "session.update"]
        assert wire == ["input_audio_buffer.append", "input_image_buffer.append"]
        await stream._cancel_receiver()

    async def test_feed_image_appends_base64_after_audio(self) -> None:
        ws = FakeWs()
        stream = _stream(ws)
        await stream.feed_audio(b"\x00\x00")  # 满足"先音频后图"窗口约束
        await stream.feed_image(b"\xff\xd8jpeg", width=640, height=480)
        msg = next(m for m in ws.sent if m["type"] == "input_image_buffer.append")
        import base64 as _b64

        assert _b64.b64decode(msg["image"]) == b"\xff\xd8jpeg"

    async def test_feed_audio_reconnect_retry(self) -> None:
        failing = FakeWs()

        async def fail_send(raw: str) -> None:
            raise ConnectionError("dead")

        failing.send = fail_send  # type: ignore[method-assign]
        ok = FakeWs()
        ok.preload({"type": "session.created"}, {"type": "session.updated"})

        async def fake_connect(*args, **kwargs):
            return ok

        stream = _stream(failing)
        with patch("avdetector.providers.alibaba_avdetector.websockets.connect", fake_connect):
            await stream.feed_audio(b"\x01\x02")
        assert any(m["type"] == "input_audio_buffer.append" for m in ok.sent)
        await stream._cancel_receiver()


def _noise_jpeg(width: int, height: int, quality: int = 90) -> bytes:
    """随机噪声图：不可压缩 → 必超帧限（重压真实链路用例）。"""
    data = random.Random(7).randbytes(width * height * 3)
    img = Image.frombytes("RGB", (width, height), data)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _oversized_junk() -> bytes:
    """30 万随机字节 → base64 后约 400KB，帧必超 _SEND_LIMIT。"""
    return random.Random(11).randbytes(300_000)


def _sent_image(ws: FakeWs) -> dict:
    return next(m for m in ws.sent if m["type"] == "input_image_buffer.append")


class TestFeedImageResize:
    """docs/fd_pipeline_avdetector.md §10 条目 28：超限上行副本两档重压（960→640）。

    网关单帧硬限 262144B，超限直接 close 1009（2026-10-09 探针实测）。
    """

    async def test_constants_match_probe_evidence(self) -> None:
        """常量与探针结论锁定：硬限 262144B、发送阈值留 1KB 余量。"""
        assert _MAX_WS_FRAME_BYTES == 262144
        assert _SEND_LIMIT == _MAX_WS_FRAME_BYTES - 1024
        assert _RESIZE_EDGE_LADDER == (960, 640)

    async def test_oversized_image_resized_under_limit(self, caplog) -> None:
        """超限 → 重压后发送帧 ≤ _SEND_LIMIT、可解码且长边 ≤960；落 debug 日志。"""
        jpeg = _noise_jpeg(1280, 960)
        ws = FakeWs()
        stream = _stream(ws)
        with caplog.at_level(logging.DEBUG, logger="livachat.avdetector.alibaba"):
            await stream.feed_image(jpeg, width=1280, height=960)
        msg = _sent_image(ws)
        assert base64.b64decode(msg["image"]) != jpeg  # 上行副本已重压
        assert len(json.dumps(msg)) <= _SEND_LIMIT
        decoded = Image.open(io.BytesIO(base64.b64decode(msg["image"])))
        assert max(decoded.size) <= 960
        assert "resized for upload" in caplog.text

    async def test_small_image_not_resized(self, caplog) -> None:
        """未超限 → payload 字节不变（零重编码）、无重压日志。"""
        ws = FakeWs()
        stream = _stream(ws)
        with caplog.at_level(logging.DEBUG, logger="livachat.avdetector.alibaba"):
            await stream.feed_image(b"\xff\xd8jpeg", width=640, height=480)
        msg = _sent_image(ws)
        assert base64.b64decode(msg["image"]) == b"\xff\xd8jpeg"
        assert "resized for upload" not in caplog.text

    async def test_second_rung_used_when_first_unreachable(self, monkeypatch, caplog) -> None:
        """960 档不可达（None）→ 降 640 档达标；档序与日志锁定。"""
        small = _noise_jpeg(640, 480, quality=30)
        calls: list[int] = []

        def fake_resize(jpeg, *, max_edge, max_bytes=None, quality=80):
            calls.append(max_edge)
            return None if max_edge == 960 else small

        monkeypatch.setattr(
            "avdetector.providers.alibaba_avdetector.image_resize", fake_resize
        )
        ws = FakeWs()
        stream = _stream(ws)
        with caplog.at_level(logging.DEBUG, logger="livachat.avdetector.alibaba"):
            await stream.feed_image(_oversized_junk(), width=1280, height=960)
        assert calls == list(_RESIZE_EDGE_LADDER)
        assert base64.b64decode(_sent_image(ws)["image"]) == small
        assert len(json.dumps(_sent_image(ws))) <= _SEND_LIMIT  # 发送不变量
        assert "(edge 640)" in caplog.text

    async def test_all_rungs_fail_raises_no_send_no_reconnect(self, monkeypatch, caplog) -> None:
        """两档皆超预算 → 抛 ProviderError；不发送、不重连；落 warning 日志。"""
        monkeypatch.setattr(
            "avdetector.providers.alibaba_avdetector.image_resize",
            lambda jpeg, *, max_edge, max_bytes=None, quality=80: None,
        )
        ws = FakeWs()
        stream = _stream(ws)
        connect_mock = MagicMock()
        with (
            patch("avdetector.providers.alibaba_avdetector.websockets.connect", connect_mock),
            caplog.at_level(logging.WARNING, logger="livachat.avdetector.alibaba"),
            pytest.raises(ProviderError),
        ):
            await stream.feed_image(_oversized_junk(), width=1280, height=960)
        assert ws.sent == []
        assert connect_mock.call_count == 0
        assert "still over frame limit" in caplog.text

    async def test_decode_failure_raises_no_send(self, caplog) -> None:
        """重压异常（垃圾字节解码失败）→ 抛 ProviderError；不发送、不重连。"""
        ws = FakeWs()
        stream = _stream(ws)
        connect_mock = MagicMock()
        with (
            patch("avdetector.providers.alibaba_avdetector.websockets.connect", connect_mock),
            caplog.at_level(logging.WARNING, logger="livachat.avdetector.alibaba"),
            pytest.raises(ProviderError),
        ):
            await stream.feed_image(_oversized_junk(), width=1280, height=960)
        assert ws.sent == []
        assert connect_mock.call_count == 0
        assert "resize unusable (decode failed)" in caplog.text
