from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omni.base import OmniEvent, OmniEventType
from omni.providers.alibaba_livetranslate import _get_response_id, _RealtimeTranslateStream


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
    """Test LiveTranslate realtime events → OmniEvent mapping via receive loop."""

    @pytest.fixture
    def mock_ws(self) -> AsyncMock:
        ws = AsyncMock()
        ws.recv = MagicMock()
        ws.send = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        return ws

    @pytest.fixture
    def stream(self, mock_ws: AsyncMock) -> _RealtimeTranslateStream:
        update_payload = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": {
                "output_modalities": ["text"],
                "voice": "Tina",
                "input_audio_format": "pcm",
                "output_audio_format": "pcm",
                "sample_rate": 16000,
                "translation": {"language": "en"},
            },
        }
        return _RealtimeTranslateStream(mock_ws, update_payload, "wss://x", "key")

    async def _drain(self, stream: _RealtimeTranslateStream, count: int) -> list:
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

    # -- source transcription -----------------------------------------------

    async def test_input_transcription_delta_accumulates_full_text(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """3.8 纯增量 → 累计回全量上抛（引擎 transcription 为覆盖语义）。"""
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": "Hello", "item_id": "item1", "language": "en"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": " wor", "item_id": "item1"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": "ld", "item_id": "item1"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 3)
        assert [e.type for e in events] == [OmniEventType.INPUT_TRANSCRIPTION_DELTA] * 3
        # 每次上抛的都是累计全文（覆盖语义），不是纯增量
        assert [e.text for e in events] == ["Hello", "Hello wor", "Hello world"]

    async def test_input_transcription_delta_item_change_resets(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """item_id 变化（新句）→ 累计器重置，不跨句串接。"""
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": "Hello", "item_id": "itemA"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": "Bonjour", "item_id": "itemB"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": " le monde", "item_id": "itemB"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 3)
        assert [e.text for e in events] == ["Hello", "Bonjour", "Bonjour le monde"]

    async def test_transcription_completed_clears_accumulator(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """completed 发 final 并清空累计器；同 item_id 的后续增量从零起算。"""
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": "你好", "item_id": "item1"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "你好世界", "item_id": "item1"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": "下一句", "item_id": "item1"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 3)
        assert events[0].type == OmniEventType.INPUT_TRANSCRIPTION_DELTA
        assert events[0].text == "你好"
        assert events[1].type == OmniEventType.INPUT_TRANSCRIPTION_DONE
        assert events[1].text == "你好世界"
        assert events[1].is_final is True
        assert events[2].type == OmniEventType.INPUT_TRANSCRIPTION_DELTA
        assert events[2].text == "下一句"

    async def test_transcription_completed_empty_suppressed(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """空 transcript 的 completed（3.8 静默微回合）不下发空 final；累计器仍重置。"""
        frames = [
            json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "", "item_id": "item1"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": "你好", "item_id": "item1"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert [e.type for e in events] == [OmniEventType.INPUT_TRANSCRIPTION_DELTA]
        assert events[0].text == "你好"

    # -- translation text: 3.8 pure delta pass-through -----------------------

    async def test_audio_transcript_delta_direct(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """3.8: response.audio_transcript.delta 为纯增量，直传（不经 diff）。"""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp1"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "Hello", "response_id": "resp1"}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": " world", "response_id": "resp1"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 3)
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        assert [e.text for e in text_events] == ["Hello", " world"]

    async def test_audio_transcript_delta_wrong_response_ignored(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """非当前 response 的 delta 被守卫丢弃。"""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp1"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "stale", "response_id": "other"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert [e for e in events if e.type == OmniEventType.TEXT_DELTA] == []

    async def test_response_text_delta_direct(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """text-only 模式（D2=A）：response.text.delta 直传。"""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp2"}}),
            json.dumps({"type": "response.text.delta",
                        "delta": "Bonjour", "response_id": "resp2"}),
            json.dumps({"type": "response.text.delta",
                        "delta": " le monde", "response_id": "resp2"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 3)
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        assert [e.text for e in text_events] == ["Bonjour", " le monde"]

    async def test_audio_transcript_done_emits_remaining(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """兜底：done 整段比已直传更长（增量缺失/截断）→ 补发差额。"""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp5"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "Hello", "response_id": "resp5"}),
            json.dumps({"type": "response.audio_transcript.done",
                        "transcript": "Hello world", "response_id": "resp5"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 3)
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        assert [e.text for e in text_events] == ["Hello", " world"]

    async def test_audio_transcript_done_no_new_text(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """done 与已直传一致 → 不重复下发。"""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp6"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "Hello world", "response_id": "resp6"}),
            json.dumps({"type": "response.audio_transcript.done",
                        "transcript": "Hello world", "response_id": "resp6"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 3)
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        assert [e.text for e in text_events] == ["Hello world"]

    async def test_audio_transcript_done_without_deltas(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """无任何 delta 时 done 兜底整段下发（极端丢增量场景）。"""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp7"}}),
            json.dumps({"type": "response.audio_transcript.done",
                        "transcript": "完整译文本", "response_id": "resp7"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        assert [e.text for e in text_events] == ["完整译文本"]

    # -- text-only mode: response.text.done fallback -------------------------

    async def test_response_text_done_backfills(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """text-only 模式 done 兜底同样补差。"""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp8"}}),
            json.dumps({"type": "response.text.delta",
                        "delta": "Bonjour", "response_id": "resp8"}),
            json.dumps({"type": "response.text.done",
                        "text": "Bonjour le monde", "response_id": "resp8"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 3)
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        assert [e.text for e in text_events] == ["Bonjour", " le monde"]

    # -- response lifecycle -------------------------------------------------

    async def test_response_created(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp_a"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.RESPONSE_STARTED
        assert events[0].response_id == "resp_a"

    async def test_response_done(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp_b"}}),
            json.dumps({"type": "response.done", "response_id": "resp_b"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.RESPONSE_DONE

    # -- audio --------------------------------------------------------------

    async def test_audio_delta_decodes_base64(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        import base64
        pcm = b"\x00\x01\x02\x03"
        b64 = base64.b64encode(pcm).decode()
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "resp_c"}}),
            json.dumps({"type": "response.audio.delta", "delta": b64, "response_id": "resp_c"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 2)
        assert events[1].type == OmniEventType.AUDIO_DELTA
        assert events[1].audio == pcm

    # -- speech events (best-effort) ----------------------------------------

    async def test_speech_started(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "input_audio_buffer.speech_started", "response_id": "r_s1"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.SPEECH_STARTED

    async def test_speech_stopped(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "input_audio_buffer.speech_stopped", "response_id": "r_s2"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.SPEECH_STOPPED

    # -- error --------------------------------------------------------------

    async def test_error_event(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "error", "error": {"message": "something wrong", "code": "server_error"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 1
        assert events[0].type == OmniEventType.ERROR
        assert "something wrong" in events[0].error_message

    async def test_error_image_rejection_disables_feed(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "error", "error": {"message": "Invalid video format", "code": "invalid"}}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 1)
        assert len(events) == 0  # image rejection doesn't emit ERROR
        assert stream._image_disabled is True

    # -- session.finished ---------------------------------------------------

    async def test_session_finished_stops_events(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        frames = [
            json.dumps({"type": "session.finished"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 5)
        # session.finished ends the iterator, no events returned
        assert len(events) == 0

    # -- turn boundary clearing ---------------------------------------------

    async def test_turn_boundary_clears_stale_output(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """New response.created clears stale TEXT_DELTA/AUDIO_DELTA from previous turn."""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "old"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "stale", "response_id": "old"}),
            json.dumps({"type": "response.created", "response": {"id": "new"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "fresh", "response_id": "new"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 4)
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        # Only the fresh text survives; stale is drained
        assert len(text_events) == 1
        assert text_events[0].text == "fresh"

    async def test_turn_boundary_preserves_input_events(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """Turn boundary preserves SPEECH_STARTED/STOPPED and INPUT_TRANSCRIPTION events
        (even though stale OUTPUT events like RESPONSE_STARTED from the old turn get drained)."""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "old"}}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                        "delta": "keep me", "item_id": "item1"}),
            json.dumps({"type": "input_audio_buffer.speech_started", "response_id": "old"}),
            json.dumps({"type": "response.created", "response": {"id": "new"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "new text", "response_id": "new"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 5)
        types = [e.type for e in events]
        # Old RESPONSE_STARTED is drained (not in kept list); new RESPONSE_STARTED + TEXT_DELTA
        # INPUT_TRANSCRIPTION_DELTA and SPEECH_STARTED survive the drain
        assert OmniEventType.RESPONSE_STARTED in types
        assert OmniEventType.INPUT_TRANSCRIPTION_DELTA in types
        assert OmniEventType.SPEECH_STARTED in types
        assert OmniEventType.TEXT_DELTA in types
        # Verify surviving events have correct content
        input_events = [e for e in events if e.type == OmniEventType.INPUT_TRANSCRIPTION_DELTA]
        assert any("keep me" in e.text for e in input_events)

    # -- resp_text reset on new turn ----------------------------------------

    async def test_resp_text_reset_on_new_turn(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """_resp_text resets on response.created：新回合 done 兜底从零比对。
        旧回合 TEXT_DELTA 在回合边界被清（stale output），仅新回合增量存活。"""
        frames = [
            json.dumps({"type": "response.created", "response": {"id": "t1"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "first turn text", "response_id": "t1"}),
            json.dumps({"type": "response.created", "response": {"id": "t2"}}),
            json.dumps({"type": "response.audio_transcript.delta",
                        "delta": "second turn text", "response_id": "t2"}),
            json.dumps({"type": "response.audio_transcript.done",
                        "transcript": "second turn text!", "response_id": "t2"}),
        ]
        mock_ws.__aiter__.return_value = frames
        await stream._start_receiver()
        events = await self._drain(stream, 5)
        text_events = [e for e in events if e.type == OmniEventType.TEXT_DELTA]
        # 旧回合增量被清；新回合：直传 + done 补差 "!"（计数已重置，不会误比对旧文本）
        assert [e.text for e in text_events] == ["second turn text", "!"]


class TestLiveTranslateProviderHandshake:
    """Test LiveTranslateProvider.open() handshake logic."""

    @pytest.fixture
    def mock_ws_connect(self) -> MagicMock:
        return MagicMock()

    async def test_handshake_payload_has_translation_fields(self, mock_ws_connect: MagicMock) -> None:
        """session.update 含 translation.language 与 output_modalities（3.8 口径）；
        不含旧 modalities、不含 input_audio_transcription（ASR 常开，§5 D4）。"""
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

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
            provider = LiveTranslateProvider(
                base_url="wss://test", api_key="k", model="m", voice="Tina",
                source_language="zh", target_language="en",
            )
            await provider.open(sample_rate=16000, voice="Tina", modalities=("text",))

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        assert len(send_calls) == 1
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert session["translation"] == {"language": "en"}
        assert session["output_modalities"] == ["text"]
        assert "modalities" not in session
        assert "input_audio_transcription" not in session

    async def test_no_instructions_or_search_in_payload(self, mock_ws_connect: MagicMock) -> None:
        """session.update must NOT contain instructions, enable_search, or turn_detection."""
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"session_id": "s2"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s2"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = LiveTranslateProvider(
                base_url="wss://test", api_key="k", model="m", voice="Tina",
                source_language="zh", target_language="en",
            )
            await provider.open(sample_rate=16000, voice="Tina")

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert "instructions" not in session
        assert "enable_search" not in session
        assert "turn_detection" not in session

    async def test_corpus_phrases_included(self, mock_ws_connect: MagicMock) -> None:
        """corpus_phrases are included in translation.corpus.phrases."""
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"session_id": "s4"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s4"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = LiveTranslateProvider(
                base_url="wss://test", api_key="k", model="m", voice="Tina",
                source_language="zh", target_language="en",
                corpus_phrases={"人工智能": "Artificial Intelligence"},
            )
            await provider.open(sample_rate=16000, voice="Tina")

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert session["translation"]["corpus"] == {"phrases": {"人工智能": "Artificial Intelligence"}}

    async def test_voice_clone_enabled(self, mock_ws_connect: MagicMock) -> None:
        """enable_voice_clone=true → session has voice_clone fields."""
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"session_id": "s5"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s5"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = LiveTranslateProvider(
                base_url="wss://test", api_key="k", model="m", voice="Tina",
                enable_voice_clone=True, voice_clone_frequency="once",
            )
            await provider.open(sample_rate=16000, voice="Tina")

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert session["enable_voice_clone"] is True
        assert session["voice_clone_options"] == {"frequency": "once"}

    async def test_generation_opts_not_merged(self, mock_ws_connect: MagicMock) -> None:
        """generation_opts (temperature/max_tokens) should NOT leak into session.update."""
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"session_id": "s_gen"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s_gen"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = LiveTranslateProvider(
                base_url="wss://test", api_key="k", model="m", voice="Tina",
            )
            await provider.open(
                sample_rate=16000, voice="Tina",
                generation_opts={"temperature": 0.5, "max_tokens": 512},
            )

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        payload = json.loads(send_calls[0].args[0])
        session = payload["session"]
        assert "temperature" not in session
        assert "max_tokens" not in session

    async def test_input_sample_rate_in_payload(self, mock_ws_connect: MagicMock) -> None:
        """input_sample_rate from config is used in session (not the open() sample_rate arg)."""
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"

        recv_frames = [
            json.dumps({"type": "session.created", "session": {"session_id": "s6"}}),
            json.dumps({"type": "session.updated", "session": {"session_id": "s6"}}),
        ]
        ws.recv = AsyncMock(side_effect=recv_frames)
        ws.send = AsyncMock()

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = LiveTranslateProvider(
                base_url="wss://test", api_key="k", model="m", voice="Tina",
                input_sample_rate=8000,
            )
            await provider.open(sample_rate=16000, voice="Tina")

        send_calls = [c for c in ws.send.call_args_list if "session.update" in str(c)]
        payload = json.loads(send_calls[0].args[0])
        assert payload["session"]["sample_rate"] == 8000


class TestWorkspaceHeader:
    """连接契约：workspace_id 只驱动 X-DashScope-WorkSpace 头（open 与重连同源），
    且 close 握手限时 1s（端点不回 close 帧；默认 10s 会卡模式切换/收尾，
    见 docs/translate_lecture_improvements.md）。"""

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
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

        ws = self._handshake_ws()
        connect = AsyncMock(return_value=ws)
        base = "wss://llm-x.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=m"
        with patch("websockets.connect", new=connect):
            provider = LiveTranslateProvider(
                base_url=base, api_key="k", model="m", voice="Tina",
                workspace_id=workspace_id,
            )
            await provider.open(sample_rate=16000, voice="Tina")

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
        stream = _RealtimeTranslateStream(
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


class TestReconnectConsumerLiveness:
    """重连不得换队列对象：消费端 parked 在 events() 上时必须继续收到后续事件。

    真机实测（2026-09-14）：换队列后重连，线路上有完整 response.text.text/
    response.done 事件流，消费端却零事件——lecture 从此静默哑掉。
    """

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

        stream = _RealtimeTranslateStream(
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


class TestHandshakeFailureCleanup:
    """握手失败必须关闭刚建立的 WS（错误帧/JSON 解析失败曾泄漏连接）。"""

    @pytest.mark.parametrize(
        "bad_frame",
        [
            json.dumps({"type": "error", "error": {"message": "bad workspace"}}),
            "{not-json",
        ],
    )
    async def test_non_session_created_closes_ws(self, bad_frame: str) -> None:
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider
        from shared.utils.errors import ProviderError

        ws = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        ws.send = AsyncMock()
        ws.close = AsyncMock()
        ws.recv = AsyncMock(return_value=bad_frame)

        with patch("websockets.connect", new=AsyncMock(return_value=ws)):
            provider = LiveTranslateProvider(
                base_url="wss://test", api_key="k", model="m", voice="Tina",
            )
            with pytest.raises(ProviderError):
                await provider.open(sample_rate=16000)

        ws.close.assert_awaited()

    async def test_reconnect_failure_closes_new_ws(self) -> None:
        """重连握手失败同样必须关掉新连接（每次失败泄漏一条 WS）。"""
        from shared.utils.errors import ProviderError

        old_ws = AsyncMock()
        new_ws = AsyncMock()
        new_ws.protocol = MagicMock()
        new_ws.protocol.state.name = "OPEN"
        new_ws.send = AsyncMock()
        new_ws.close = AsyncMock()
        new_ws.recv = AsyncMock(return_value=json.dumps({"type": "unexpected"}))

        stream = _RealtimeTranslateStream(
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


class TestCancelResponseNoop:
    """§4.4: cancel_response is a no-op for LiveTranslate."""

    @pytest.fixture
    def mock_ws(self) -> AsyncMock:
        ws = AsyncMock()
        ws.recv = MagicMock()
        ws.send = AsyncMock()
        ws.protocol = MagicMock()
        ws.protocol.state.name = "OPEN"
        return ws

    @pytest.fixture
    def stream(self, mock_ws: AsyncMock) -> _RealtimeTranslateStream:
        update_payload = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": {
                "output_modalities": ["text"],
                "voice": "Tina",
                "translation": {"language": "en"},
            },
        }
        return _RealtimeTranslateStream(mock_ws, update_payload, "wss://x", "key")

    async def test_cancel_response_does_not_send_ws_message(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """cancel_response should NOT send response.cancel or input_audio_buffer.clear."""
        await stream._start_receiver()
        await stream.cancel_response()
        # No WS send should have happened
        send_calls = mock_ws.send.call_args_list
        cancel_sends = [c for c in send_calls if "response.cancel" in str(c) or "input_audio_buffer.clear" in str(c)]
        assert len(cancel_sends) == 0

    async def test_cancel_response_does_not_raise(self, stream: _RealtimeTranslateStream, mock_ws: AsyncMock) -> None:
        """cancel_response should never raise an exception."""
        await stream._start_receiver()
        # Should not raise
        await stream.cancel_response()


class TestProviderProperties:
    """alibaba_livetranslate 现仅由 pipeline lecture 直接构造（omni 配置已于 2026-09-14 移除）；
    本类只锁 provider 自身属性。"""

    def test_livetranslate_provider_name(self) -> None:
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

        p = LiveTranslateProvider(
            base_url="wss://x", api_key="k", model="m", voice="Tina",
            source_language="zh", target_language="en",
        )
        assert p.name == "alibaba_livetranslate"
        assert p.supports_vision is True
