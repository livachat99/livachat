from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Awaitable

from livachat_protocol.generated import (
    SessionStatusCode,
    StatusReasonCode,
)

from .config import Scenario, ServerConfig
from .media_fixtures import MOCK_OPUS_SILENCE_B64, MOCK_OPUS_CHUNKS
from .protocol import (
    make_transcription_id,
    make_message_id,
    build_transcription,
    build_ai_text,
    build_ai_audio,
    build_status,
)
from .session_store import SessionContext

SendFunc = Callable[..., Awaitable[None]]

INTERIM_INTERVAL_S = 0.15


async def _send(websocket_send: SendFunc, ctx: SessionContext, msg) -> None:
    """Pass the Pydantic model to the send callback (which handles serialization)."""
    await websocket_send(msg)


async def run_response_flow(
    *,
    ctx: SessionContext,
    scenario: Scenario,
    config: ServerConfig,
    websocket_send: SendFunc,
) -> None:
    """Execute the full response flow after detecting end of user turn.

    Flow: final transcription → thinking → ai_text streaming → (optional ai_audio) → listening.

    Runs as a background task so it can be cancelled on interrupt.
    """
    # --- final transcription ---
    text = config.interrupt_transcript if ctx.interrupted else config.default_transcript
    tid = make_transcription_id()
    await _send(
        websocket_send, ctx,
        build_transcription(ctx.next_s2c_seq, text, is_final=True, transcription_id=tid),
    )

    # --- thinking ---
    await _send(
        websocket_send, ctx,
        build_status(ctx.next_s2c_seq, SessionStatusCode.THINKING, StatusReasonCode.ASR_FINAL),
    )

    if scenario == Scenario.SLOW_NETWORK:
        await _delay(config)

    # --- speaking ---
    await _send(
        websocket_send, ctx,
        build_status(ctx.next_s2c_seq, SessionStatusCode.SPEAKING, StatusReasonCode.TTS_START),
    )
    ctx.session_status = SessionStatusCode.SPEAKING

    # --- ai_text streaming ---
    msg_id = make_message_id()
    chunks = config.ai_text_chunks
    for i, chunk in enumerate(chunks):
        if ctx.interrupted:
            break
        is_final = (i == len(chunks) - 1)
        await _send(
            websocket_send, ctx,
            build_ai_text(ctx.next_s2c_seq, chunk, msg_id, i, is_final),
        )
        if scenario == Scenario.SLOW_NETWORK:
            await _delay(config)

    # --- ai_audio (optional) ---
    include_audio = scenario in (Scenario.DEFAULT, Scenario.WITH_AUDIO)
    if include_audio and not ctx.interrupted:
        for i, b64 in enumerate(MOCK_OPUS_CHUNKS):
            if ctx.interrupted:
                break
            is_final = (i == len(MOCK_OPUS_CHUNKS) - 1)
            await _send(
                websocket_send, ctx,
                build_ai_audio(ctx.next_s2c_seq, b64, msg_id, i, is_final),
            )
            if scenario == Scenario.SLOW_NETWORK:
                await _delay(config)

    ctx.interrupted = False

    # --- back to listening ---
    await _send(
        websocket_send, ctx,
        build_status(ctx.next_s2c_seq, SessionStatusCode.LISTENING, StatusReasonCode.TTS_FINISH),
    )
    ctx.session_status = SessionStatusCode.LISTENING


async def _delay(config: ServerConfig) -> None:
    ms = random.randint(config.slow_network_min_delay_ms, config.slow_network_max_delay_ms)
    await asyncio.sleep(ms / 1000.0)


async def send_interim_transcriptions(
    *,
    ctx: SessionContext,
    config: ServerConfig,
    websocket_send: SendFunc,
) -> None:
    """Send periodic interim transcription while user is speaking."""
    tid = make_transcription_id()
    partials = [
        "你好",
        "你好，请问",
        "你好，请问有什么",
        "你好，请问有什么可以",
    ]
    for part in partials:
        if ctx.interrupted:
            break
        await _send(
            websocket_send, ctx,
            build_transcription(ctx.next_s2c_seq, part, is_final=False, transcription_id=tid),
        )
        if config.scenario == Scenario.SLOW_NETWORK:
            await _delay(config)
        await asyncio.sleep(INTERIM_INTERVAL_S)
