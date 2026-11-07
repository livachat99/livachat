from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from omni.base import OmniEvent, OmniEventType
from omni.providers.alibaba_omni import (
    _MAX_WS_FRAME_BYTES,
    _RESIZE_EDGE_LADDER,
    _SEND_LIMIT,
    _get_response_id,
    _RealtimeOmniStream,
)


class TestGetResponseId:
    def test_top_level(self) -> None:
        assert _get_response_id({"response_id": "r1"}) == "r1"

    def test_nested_in_response(self) -> None:
        assert _get_response_id({"response": {"id": "r2"}}) == "r2"

    def test_top_level_takes_precedence(self) -> None:
        assert _get_response_id({"response_id": "r1", "response": {"id": "r2"}}) == "r1"

    def test_none(self) -> None:
        assert _get_response_id({}) is None


class TestEventMapping:
    """Test that Qwen Realtime events are correctly mapped to OmniEvent via the receive loop.

    Uses a mock WebSocket that yields a predefined sequence of JSON/text frames.
    """

    @pytest.fixture
    def mock_ws(self) -> AsyncMock:
        ws = AsyncMock()
        ws.recv = MagicMock()
        ws.send = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        return ws

    @pytest.fixture
    def stream(self, mock_ws: AsyncMock) -> _RealtimeOmniStream:
        update_payload = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": "Cherry",
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "turn_detection": {"type": "semantic_vad"},
                "instructions": "system prompt",
            },
        }
        return _RealtimeOmniStream(mock_ws, update_payload, "wss://x", "key")

    async def _drain(self, stream: _RealtimeOmniStream, count: int) -> list:
        events = []
        gen = stream.events()
        for _ in range(count):
            try:
                import asyncio
                ev = await asyncio.wait_for(gen.__anext__(), timeout=0.1)
                events.append(ev)
            except (StopAsyncIteration, TimeoutError):
                break
        return events

    async def test_speech_started(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "input_audio_buffer.speech_started", "response_id": "r1"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.SPEECH_STARTED
        assert events[0].response_id == "r1"

    async def test_speech_stopped(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "input_audio_buffer.speech_stopped", "response_id": "r2"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.SPEECH_STOPPED

    async def test_transcription_delta(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        """实测形态：.delta + text(已确认) + stash(待修正)，拼接为当前最完整预览。"""
        frames = [
            json.dumps({
                "type": "conversation.item.input_audio_transcription.delta",
                "text": "今天",
                "stash": "天气",
                "language": "zh",
                "emotion": "neutral",
                "response_id": "r3",
            }),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.INPUT_TRANSCRIPTION_DELTA
        assert events[0].text == "今天天气"

    async def test_transcription_delta_stash_only(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        """开场帧 text 为空、只有 stash（实测首帧即如此）。"""
        frames = [
            json.dumps({
                "type": "conversation.item.input_audio_transcription.delta",
                "text": "",
                "stash": "你好",
                "response_id": "r3",
            }),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert events[0].text == "你好"

    async def test_transcription_legacy_text_event_fallback(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        """旧名 .text + delta 字段（历史写法）仍兼容。"""
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.text", "delta": "你好", "response_id": "r3"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.INPUT_TRANSCRIPTION_DELTA
        assert events[0].text == "你好"

    async def test_transcription_completed(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.completed", "transcript": "你好世界", "response_id": "r4"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.INPUT_TRANSCRIPTION_DONE
        assert events[0].text == "你好世界"
        assert events[0].is_final is True

    async def test_response_created(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp1"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.RESPONSE_STARTED
        assert events[0].response_id == "resp1"

    async def test_text_delta(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "r5"}}),
            json.dumps({"type": "response.text.delta", "delta": "Hello", "response_id": "r5"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.TEXT_DELTA
        assert events[1].text == "Hello"

    async def test_audio_transcript_delta_mapped_to_text(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "r6"}}),
            json.dumps({"type": "response.audio_transcript.delta", "delta": "Hi", "response_id": "r6"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.TEXT_DELTA
        assert events[1].text == "Hi"

    async def test_audio_delta_decodes_base64(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        import base64
        pcm = b"\x00\x01\x02\x03"
        b64 = base64.b64encode(pcm).decode()
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "r7"}}),
            json.dumps({"type": "response.audio.delta", "delta": b64, "response_id": "r7"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.AUDIO_DELTA
        assert events[1].audio == pcm

    async def test_response_done(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "r8"}}),
            json.dumps({"type": "response.done", "response_id": "r8"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.RESPONSE_DONE

    async def test_error_event(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "error", "error": {"message": "something wrong", "code": "server_error"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.ERROR
        assert "something wrong" in events[0].error_message

    async def test_turn_boundary_clears_stale_queue(self, stream: _RealtimeOmniStream, mock_ws: AsyncMock) -> None:
        """New response.created clears stale events from previous response."""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "old_resp"}}),
            json.dumps({"type": "response.text.delta", "delta": "stale", "response_id": "old_resp"}),
            json.dumps({"type": "response.created", "response": {"id": "new_resp"}}),
            json.dumps({"type": "response.text.delta", "delta": "fresh", "response_id": "new_resp"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        # The stale TEXT_DELTA should be cleared when new response.created arrives
        events = await self._drain(stream, 4)
        # We should get: RESPONSE_STARTED (old), RESPONSE_STARTED (new), TEXT_DELTA (fresh)
        # The stale text delta is cleared by the turn boundary
        types = [e.type for e in events]
        assert OmniEventType.RESPONSE_STARTED in types
        assert OmniEventType.TEXT_DELTA in types
        # The fresh text should be the only TEXT_DELTA
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        assert len(text_events) == 1
        assert text_events[0].text == "fresh"


class TestAlibabaOmniProviderHandshake:
    """Test AlibabaOmniProvider.open() handshake logic."""

    @pytest.fixture
    def mock_ws_connect(self) -> MagicMock:
        return MagicMock()

    async def test_handshake_payload_contains_required_fields(self, mock_ws_connect: MagicMock) -> None:
        """session.update payload must contain turn_detection, sample_rate, instructions."""
        from omni.providers.alibaba_omni import AlibabaOmniProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"session_id": "s1"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s1"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = AlibabaOmniProvider(
                base_url="wss://test", api_key="k", model="m", voice="Cherry"
            )
            await provider.open(
                sample_rate=16000,
                system_prompt="You are helpful.",
                voice="Cherry",
                modalities=("text", "audio"),
            )

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        assert len(send_calls) == 1
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert session["turn_detection"] == {"type": "semantic_vad"}
        assert session["sample_rate"] == 16000
        assert session["instructions"] == "You are helpful."

    async def test_handshake_payload_nested_audio(self, mock_ws_connect: MagicMock) -> None:
        """嵌套 audio.* 才是生效字段：flat sample_rate 被服务端回显但不生效。"""
        from omni.providers.alibaba_omni import AlibabaOmniProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        recv_frames = [
            json.dumps({"type": "session.created", "session": {"session_id": "s1"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s1"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = AlibabaOmniProvider(
                base_url="wss://test", api_key="k", model="m", voice="Cherry"
            )
            await provider.open(
                sample_rate=16000,
                system_prompt="You are helpful.",
                voice="Cherry",
                modalities=("text", "audio"),
            )

        payload = json.loads([c for c in ws.send.call_args_list if "session.update" in str(c)][0].args[0])
        audio = payload["session"]["audio"]
        assert audio["input"]["format"] == {"type": "pcm", "sample_rate": 16000}
        assert audio["output"]["format"] == {"type": "pcm", "sample_rate": 16000}
        assert audio["output"]["voice"] == "Cherry"

    async def test_generation_opts_merged_into_session(self, mock_ws_connect: MagicMock) -> None:
        """generation_opts are merged into session.update payload."""
        from omni.providers.alibaba_omni import AlibabaOmniProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"session_id": "s1"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s1"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = AlibabaOmniProvider(
                base_url="wss://test", api_key="k", model="m", voice="Cherry"
            )
            await provider.open(
                sample_rate=16000,
                system_prompt="test",
                generation_opts={"temperature": 0.5, "max_tokens": 512},
            )

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert session["temperature"] == 0.5
        assert session["max_tokens"] == 512

    async def test_missing_session_created_raises_provider_error(self, mock_ws_connect: MagicMock) -> None:
        """If first message is not session.created, raise ProviderError."""
        from omni.providers.alibaba_omni import AlibabaOmniProvider
        from shared.utils.errors import ProviderError

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        ws.recv = AsyncMock(return_value=json.dumps({"type": "unexpected"}))
        ws.send = AsyncMock()
        ws.close = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = AlibabaOmniProvider(
                base_url="wss://test", api_key="k", model="m", voice="Cherry"
            )
            with pytest.raises(ProviderError, match="expected session.created"):
                await provider.open(sample_rate=16000)

        ws.close.assert_awaited()

    async def test_timeout_waiting_session_created_raises_provider_error(self, mock_ws_connect: MagicMock) -> None:
        """Timeout waiting for session.created raises ProviderError."""

        from omni.providers.alibaba_omni import AlibabaOmniProvider
        from shared.utils.errors import ProviderError

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        ws.recv = AsyncMock(side_effect=TimeoutError())
        ws.send = AsyncMock()
        ws.close = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = AlibabaOmniProvider(
                base_url="wss://test", api_key="k", model="m", voice="Cherry"
            )
            with pytest.raises(ProviderError, match="timeout"):
                await provider.open(sample_rate=16000)


class TestReconnectHandshakeCleanup:
    """重连握手失败必须关掉刚建立的连接（否则每次失败泄漏一条 WS）。"""

    async def test_reconnect_failure_closes_new_ws(self) -> None:
        from shared.utils.errors import ProviderError

        old_ws = AsyncMock()
        new_ws = AsyncMock()
        new_ws.protocol = MagicMock()
        new_ws.protocol.state.name = "OPEN"
        new_ws.send = AsyncMock()
        new_ws.close = AsyncMock()
        new_ws.recv = AsyncMock(return_value=json.dumps({"type": "unexpected"}))

        stream = _RealtimeOmniStream(
            old_ws,
            {"event_id": "evt_session_update", "type": "session.update", "session": {}},
            "wss://x",
            "key",
        )
        with (
            patch("websockets.connect", new=AsyncMock(return_value=new_ws)),
            pytest.raises(ProviderError, match="expected session.created"),
        ):
            await stream._reconnect()

        new_ws.close.assert_awaited()


class TestReconnectConsumerLiveness:
    """重连不得换队列对象（livetranslate 同款 bug 的回归锁）：消费端 parked 在
    events() 上时必须继续收到重连后的事件。"""

    async def test_parked_consumer_survives_reconnect(self) -> None:
        old_ws = AsyncMock()
        new_ws = AsyncMock()
        new_ws.protocol = MagicMock()
        new_ws.protocol.state.name = "OPEN"
        new_ws.send = AsyncMock()
        new_ws.recv = AsyncMock(side_effect=[
            json.dumps({"type": "session.created", "session": {"session_id": "s2"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s2"}}),
        ])

        stream = _RealtimeOmniStream(
            old_ws,
            {"event_id": "evt_session_update", "type": "session.update", "session": {}},
            "wss://x",
            "key",
        )

        agen = stream.events()
        parked = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.05)  # 让消费端进入旧队列 get() 挂起
        assert not parked.done()

        with patch("websockets.connect", new=AsyncMock(return_value=new_ws)):
            await stream._reconnect()

        await stream._queue.put(
            OmniEvent(type=OmniEventType.RESPONSE_STARTED, response_id="r1")
        )
        ev = await asyncio.wait_for(parked, timeout=1.0)
        assert ev.type == OmniEventType.RESPONSE_STARTED

        await agen.aclose()
        await stream._cancel_receiver()


class TestWorkspaceHeader:
    """连接契约：workspace_id 只驱动 X-DashScope-WorkSpace 头（open 与重连同源），
    且 close 握手限时 1s（realtime 族文档中该头为可选项，按官方推荐路径保留；
    缺省时行为零变化）。"""

    @staticmethod
    def _handshake_ws() -> AsyncMock:
        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        ws.send = AsyncMock()
        ws.recv = AsyncMock(side_effect=[
            json.dumps({"type": "session.created", "session": {"session_id": "s1"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s1"}}),
        ])
        return ws

    @pytest.mark.parametrize(
        "workspace_id,expected",
        [
            (None, {"Authorization": "Bearer k"}),
            ("llm-x", {"Authorization": "Bearer k", "X-DashScope-WorkSpace": "llm-x"}),
        ],
    )
    async def test_open_additional_headers(self, workspace_id: str | None, expected: dict) -> None:
        from omni.providers.alibaba_omni import AlibabaOmniProvider

        ws = self._handshake_ws()
        connect = AsyncMock(return_value=ws)
        base = "wss://llm-x.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=m"
        with patch("websockets.connect", new=connect):
            provider = AlibabaOmniProvider(
                base_url=base, api_key="k", model="m", voice="Cherry",
                workspace_id=workspace_id,
            )
            await provider.open(sample_rate=16000)

        # base_url 是实际连接端点（不派生不改写）；workspace_id 只驱动请求头
        assert connect.call_args.args[0] == base
        assert connect.call_args.kwargs["additional_headers"] == expected
        assert connect.call_args.kwargs["close_timeout"] == 1

    @pytest.mark.parametrize(
        "workspace_id,expected",
        [
            (None, {"Authorization": "Bearer k"}),
            ("llm-x", {"Authorization": "Bearer k", "X-DashScope-WorkSpace": "llm-x"}),
        ],
    )
    async def test_reconnect_additional_headers(self, workspace_id: str | None, expected: dict) -> None:
        """断线重连必须复用同一套头，否则重连后丢 workspace 路由。"""
        old_ws = AsyncMock()
        new_ws = self._handshake_ws()
        connect = AsyncMock(return_value=new_ws)
        base = "wss://llm-x.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
        stream = _RealtimeOmniStream(
            old_ws,
            {"event_id": "evt_session_update", "type": "session.update", "session": {}},
            base,
            "k",
            workspace_id=workspace_id,
        )
        with patch("websockets.connect", new=connect):
            await stream._reconnect()

        assert connect.call_args.args[0] == base
        assert connect.call_args.kwargs["additional_headers"] == expected
        assert connect.call_args.kwargs["close_timeout"] == 1


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


class TestFeedImageResize:
    """docs/fd_pipeline_omni_mode.md 文首〔2026-10-09 修订〕：附帧超限两档重压（960→640）。

    网关单帧硬限 262144B，超限直接 close 1009（探针证据：docs/fd_pipeline_avdetector.md
    §10 条目 28）。omni 每帧附图，失败路径为"记 warning + 跳过本帧"——**不抛错**：
    OmniModeEngine 对 feed_image 异常会 `_drop_stream` 关掉整个会话流。
    """

    @pytest.fixture
    def mock_ws(self) -> AsyncMock:
        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        return ws

    @pytest.fixture
    def stream(self, mock_ws: AsyncMock) -> _RealtimeOmniStream:
        return _RealtimeOmniStream(
            mock_ws,
            {"event_id": "evt_session_update", "type": "session.update", "session": {}},
            "wss://x",
            "key",
        )

    def _sent_image_frames(self, ws: AsyncMock) -> list[tuple[dict, str]]:
        frames: list[tuple[dict, str]] = []
        for call in ws.send.call_args_list:
            raw = call.args[0]
            msg = json.loads(raw)
            if msg.get("type") == "input_image_buffer.append":
                frames.append((msg, raw))
        return frames

    async def test_constants_match_probe_evidence(self) -> None:
        assert _MAX_WS_FRAME_BYTES == 262144
        assert _SEND_LIMIT == _MAX_WS_FRAME_BYTES - 1024
        assert _RESIZE_EDGE_LADDER == (960, 640)

    async def test_oversized_image_resized_under_limit(
        self, stream: _RealtimeOmniStream, mock_ws: AsyncMock, caplog,
    ) -> None:
        """超限 → 重压后发送帧 ≤ _SEND_LIMIT、可解码且长边 ≤960；落 debug 日志。"""
        jpeg = _noise_jpeg(1280, 960)
        with caplog.at_level(logging.DEBUG, logger="livachat.omni.alibaba"):
            await stream.feed_image(jpeg, width=1280, height=960)
        msg, raw = self._sent_image_frames(mock_ws)[0]
        assert base64.b64decode(msg["image"]) != jpeg  # 上行副本已重压
        assert len(raw) <= _SEND_LIMIT
        decoded = Image.open(io.BytesIO(base64.b64decode(msg["image"])))
        assert max(decoded.size) <= 960
        assert "resized for upload" in caplog.text

    async def test_small_image_not_resized(
        self, stream: _RealtimeOmniStream, mock_ws: AsyncMock, caplog,
    ) -> None:
        """未超限 → payload 字节不变（零重编码）、无重压日志。"""
        with caplog.at_level(logging.DEBUG, logger="livachat.omni.alibaba"):
            await stream.feed_image(b"\xff\xd8jpeg", width=640, height=480)
        msg, _ = self._sent_image_frames(mock_ws)[0]
        assert base64.b64decode(msg["image"]) == b"\xff\xd8jpeg"
        assert "resized for upload" not in caplog.text

    async def test_second_rung_used_when_first_unreachable(
        self, stream: _RealtimeOmniStream, mock_ws: AsyncMock, monkeypatch, caplog,
    ) -> None:
        """960 档不可达（None）→ 降 640 档达标；档序与日志锁定。"""
        small = _noise_jpeg(640, 480, quality=30)
        calls: list[int] = []

        def fake_resize(jpeg, *, max_edge, max_bytes=None, quality=80):
            calls.append(max_edge)
            return None if max_edge == 960 else small

        monkeypatch.setattr("omni.providers.alibaba_omni.image_resize", fake_resize)
        with caplog.at_level(logging.DEBUG, logger="livachat.omni.alibaba"):
            await stream.feed_image(_oversized_junk(), width=1280, height=960)
        assert calls == list(_RESIZE_EDGE_LADDER)
        msg, raw = self._sent_image_frames(mock_ws)[0]
        assert base64.b64decode(msg["image"]) == small
        assert len(raw) <= _SEND_LIMIT  # 发送不变量
        assert "(edge 640)" in caplog.text

    async def test_all_rungs_fail_skips_frame(
        self, stream: _RealtimeOmniStream, mock_ws: AsyncMock, monkeypatch, caplog,
    ) -> None:
        """两档皆超预算 → 不发送、不抛错（引擎异常会关整个流）；落 warning 日志。"""
        monkeypatch.setattr(
            "omni.providers.alibaba_omni.image_resize",
            lambda jpeg, *, max_edge, max_bytes=None, quality=80: None,
        )
        with caplog.at_level(logging.WARNING, logger="livachat.omni.alibaba"):
            await stream.feed_image(_oversized_junk(), width=1280, height=960)  # 不抛错
        mock_ws.send.assert_not_awaited()
        assert "still over frame limit" in caplog.text

    async def test_decode_failure_skips_frame(
        self, stream: _RealtimeOmniStream, mock_ws: AsyncMock, caplog,
    ) -> None:
        """重压异常（垃圾字节解码失败）→ 不发送、不抛错；落 warning 日志。"""
        with caplog.at_level(logging.WARNING, logger="livachat.omni.alibaba"):
            await stream.feed_image(_oversized_junk(), width=1280, height=960)  # 不抛错
        mock_ws.send.assert_not_awaited()
        assert "resize unusable (decode failed)" in caplog.text
