from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omni.base import OmniEvent, OmniEventType
from omni.providers.openai_realtime import (
    _get_response_id,
    _OpenAIRealtimeStream,
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
    """Test that OpenAI Realtime events are correctly mapped to OmniEvent via the receive loop."""

    @pytest.fixture
    def mock_ws(self) -> AsyncMock:
        ws = AsyncMock()
        ws.recv = MagicMock()
        ws.send = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        return ws

    @pytest.fixture
    def stream(self, mock_ws: AsyncMock) -> _OpenAIRealtimeStream:
        update_payload = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {"type": "server_vad"},
                "instructions": "system prompt",
            },
        }
        return _OpenAIRealtimeStream(mock_ws, update_payload, "wss://x", "key", sample_rate=16000)

    async def _drain(self, stream: _OpenAIRealtimeStream, count: int) -> list:
        events = []
        gen = stream.events()
        for _ in range(count):
            try:
                ev = await asyncio.wait_for(gen.__anext__(), timeout=0.1)
                events.append(ev)
            except (StopAsyncIteration, TimeoutError):
                break
        return events

    async def test_speech_started(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "input_audio_buffer.speech_started", "response_id": "r1"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.SPEECH_STARTED
        assert events[0].response_id == "r1"

    async def test_speech_stopped(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "input_audio_buffer.speech_stopped", "response_id": "r2"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.SPEECH_STOPPED

    async def test_transcription_delta_via_delta_event(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.delta", "delta": "Hello", "response_id": "r3"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.INPUT_TRANSCRIPTION_DELTA
        assert events[0].text == "Hello"

    async def test_transcription_delta_via_text_event(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        """OpenAI may send .text (legacy) instead of .delta for transcription."""
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.text", "delta": "Hi", "response_id": "r3b"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.INPUT_TRANSCRIPTION_DELTA
        assert events[0].text == "Hi"

    async def test_transcription_completed(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Hello world", "response_id": "r4"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.INPUT_TRANSCRIPTION_DONE
        assert events[0].text == "Hello world"
        assert events[0].is_final is True

    async def test_response_created(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp1"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.RESPONSE_STARTED
        assert events[0].response_id == "resp1"

    async def test_text_delta(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "r5"}}),
            json.dumps({"type": "response.text.delta", "delta": "Hello", "response_id": "r5"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.TEXT_DELTA
        assert events[1].text == "Hello"

    async def test_audio_transcript_delta_mapped_to_text(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "r6"}}),
            json.dumps({"type": "response.audio_transcript.delta", "delta": "Hi", "response_id": "r6"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.TEXT_DELTA
        assert events[1].text == "Hi"

    async def test_audio_delta_decodes_base64(self, mock_ws: AsyncMock) -> None:
        """When sample_rate matches output rate (24kHz), no resampling; raw PCM passes through."""
        update_payload = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {"type": "server_vad"},
                "instructions": "test",
            },
        }
        stream = _OpenAIRealtimeStream(mock_ws, update_payload, "wss://x", "key", sample_rate=24000)
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

    async def test_audio_delta_with_resampling(self, mock_ws: AsyncMock) -> None:
        """When sample_rate != 24000, audio should pass through resampler."""
        update_payload = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {"type": "server_vad"},
                "instructions": "test",
            },
        }
        stream = _OpenAIRealtimeStream(mock_ws, update_payload, "wss://x", "key", sample_rate=16000)
        assert stream._resampler is not None

        # soxr needs enough samples to produce output; 4800 bytes = 2400 samples at 24kHz
        import numpy as np
        samples = (np.sin(np.linspace(0, 440 * 2 * np.pi, 2400)) * 8192).astype(np.int16)
        pcm_24k = samples.tobytes()
        b64 = base64.b64encode(pcm_24k).decode()
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "r7b"}}),
            json.dumps({"type": "response.audio.delta", "delta": b64, "response_id": "r7b"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.AUDIO_DELTA
        assert len(events[1].audio) > 0

    async def test_audio_delta_no_resampling_when_rate_matches(self, mock_ws: AsyncMock) -> None:
        """When sample_rate == 24000, no resampler is created."""
        update_payload = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {"type": "server_vad"},
                "instructions": "test",
            },
        }
        stream = _OpenAIRealtimeStream(mock_ws, update_payload, "wss://x", "key", sample_rate=24000)
        assert stream._resampler is None

    async def test_response_done(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "r8"}}),
            json.dumps({"type": "response.done", "response_id": "r8"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.RESPONSE_DONE

    async def test_error_event(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "error", "error": {"message": "something wrong", "code": "server_error"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.ERROR
        assert "something wrong" in events[0].error_message

    async def test_turn_boundary_clears_stale_queue(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        """New response.created clears stale events from previous response."""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "old_resp"}}),
            json.dumps({"type": "response.text.delta", "delta": "stale", "response_id": "old_resp"}),
            json.dumps({"type": "response.created", "response": {"id": "new_resp"}}),
            json.dumps({"type": "response.text.delta", "delta": "fresh", "response_id": "new_resp"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 4)
        types = [e.type for e in events]
        assert OmniEventType.RESPONSE_STARTED in types
        assert OmniEventType.TEXT_DELTA in types
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        assert len(text_events) == 1
        assert text_events[0].text == "fresh"

    async def test_ignores_binary_frame(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            b"\x00\x01binary",
            json.dumps({"type": "response.created", "response": {"id": "r9"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.RESPONSE_STARTED

    async def test_ignores_invalid_json(self, stream: _OpenAIRealtimeStream, mock_ws: AsyncMock) -> None:
        frames = [
            "not valid json",
            json.dumps({"type": "response.created", "response": {"id": "r10"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.RESPONSE_STARTED


class TestOpenAIRealtimeProviderHandshake:
    """Test OpenAIRealtimeProvider.open() handshake logic."""

    async def test_openai_beta_header_present(self) -> None:
        """open() must include OpenAI-Beta: realtime=v1 header."""
        from omni.providers.openai_realtime import OpenAIRealtimeProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"id": "s1"}}),
            json.dumps({"type": "session.updated", "session": {"id": "s1"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock()) as mock_connect:
            mock_connect.return_value = ws
            provider = OpenAIRealtimeProvider(
                base_url="wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview",
                api_key="sk-test",
                model="gpt-4o-realtime-preview",
                voice="alloy",
            )
            await provider.open(sample_rate=16000)

            call_kwargs = mock_connect.call_args.kwargs
            headers = call_kwargs["additional_headers"]
            assert headers["Authorization"] == "Bearer sk-test"
            assert headers["OpenAI-Beta"] == "realtime=v1"

    async def test_handshake_payload_contains_required_fields(self) -> None:
        """session.update payload must contain OpenAI-expected fields."""
        from omni.providers.openai_realtime import OpenAIRealtimeProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"id": "s1"}}),
            json.dumps({"type": "session.updated", "session": {"id": "s1"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = OpenAIRealtimeProvider(
                base_url="wss://test", api_key="k", model="m", voice="alloy"
            )
            await provider.open(
                sample_rate=16000,
                system_prompt="You are helpful.",
                voice="alloy",
                modalities=("text", "audio"),
            )

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        assert len(send_calls) == 1
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert session["modalities"] == ["text", "audio"]
        assert session["voice"] == "alloy"
        assert session["input_audio_format"] == "pcm16"
        assert session["output_audio_format"] == "pcm16"
        assert session["turn_detection"] == {"type": "server_vad"}
        assert session["instructions"] == "You are helpful."
        assert session["input_audio_transcription"] == {"model": "whisper-1"}

    async def test_generation_opts_merged_into_session(self) -> None:
        """generation_opts are merged into session.update payload."""
        from omni.providers.openai_realtime import OpenAIRealtimeProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"id": "s1"}}),
            json.dumps({"type": "session.updated", "session": {"id": "s1"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = OpenAIRealtimeProvider(
                base_url="wss://test", api_key="k", model="m", voice="alloy"
            )
            await provider.open(
                sample_rate=16000,
                system_prompt="test",
                generation_opts={"temperature": 0.5, "max_response_output_tokens": 512},
            )

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert session["temperature"] == 0.5
        assert session["max_response_output_tokens"] == 512

    async def test_missing_session_created_raises_provider_error(self) -> None:
        """If first message is not session.created, raise ProviderError."""
        from omni.providers.openai_realtime import OpenAIRealtimeProvider
        from shared.utils.errors import ProviderError

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        ws.recv = AsyncMock(return_value=json.dumps({"type": "unexpected"}))
        ws.send = AsyncMock()
        ws.close = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = OpenAIRealtimeProvider(
                base_url="wss://test", api_key="k", model="m", voice="alloy"
            )
            with pytest.raises(ProviderError, match="expected session.created"):
                await provider.open(sample_rate=16000)

        ws.close.assert_awaited()

    async def test_timeout_waiting_session_created_raises_provider_error(self) -> None:
        """Timeout waiting for session.created raises ProviderError."""
        from omni.providers.openai_realtime import OpenAIRealtimeProvider
        from shared.utils.errors import ProviderError

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        ws.recv = AsyncMock(side_effect=TimeoutError())
        ws.send = AsyncMock()
        ws.close = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = OpenAIRealtimeProvider(
                base_url="wss://test", api_key="k", model="m", voice="alloy"
            )
            with pytest.raises(ProviderError, match="timeout"):
                await provider.open(sample_rate=16000)

    async def test_connect_failure_raises_provider_error(self) -> None:
        """Connection failure raises ProviderError."""
        from omni.providers.openai_realtime import OpenAIRealtimeProvider
        from shared.utils.errors import ProviderError

        with patch("websockets.connect", new=AsyncMock(side_effect=OSError("Connection refused"))):
            provider = OpenAIRealtimeProvider(
                base_url="wss://test", api_key="k", model="m", voice="alloy"
            )
            with pytest.raises(ProviderError, match="connect failed"):
                await provider.open(sample_rate=16000)


class TestOpenAIRealtimeProviderProperties:
    def test_name_and_supports_vision(self) -> None:
        from omni.providers.openai_realtime import OpenAIRealtimeProvider

        p = OpenAIRealtimeProvider(
            base_url="wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview",
            api_key="sk-test",
            model="gpt-4o-realtime-preview",
            voice="alloy",
        )
        assert p.name == "openai_realtime"
        assert p.supports_vision is False

    async def test_feed_image_is_noop(self) -> None:
        """feed_image should not send anything over WS for OpenAI (no vision support)."""
        mock_ws = AsyncMock()
        mock_ws.protocol = MagicMock()
        mock_ws.protocol.state.name = "OPEN"
        mock_ws.send = AsyncMock()

        stream = _OpenAIRealtimeStream(
            mock_ws,
            update_payload={"event_id": "evt", "type": "session.update", "session": {}},
            base_url="wss://x",
            api_key="k",
            sample_rate=16000,
        )
        await stream.feed_image(b"\xff\xd8fakejpeg", width=640, height=480)
        # No WebSocket send should have occurred
        mock_ws.send.assert_not_called()


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

        stream = _OpenAIRealtimeStream(
            old_ws,
            update_payload={"event_id": "evt_session_update", "type": "session.update", "session": {}},
            base_url="wss://x",
            api_key="k",
            sample_rate=16000,
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
            json.dumps({"type": "session.created", "session": {"id": "s2"}}),
            json.dumps({"type": "session.updated", "session": {"id": "s2"}}),
        ])

        stream = _OpenAIRealtimeStream(
            old_ws,
            update_payload={"event_id": "evt_session_update", "type": "session.update", "session": {}},
            base_url="wss://x",
            api_key="k",
            sample_rate=16000,
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
