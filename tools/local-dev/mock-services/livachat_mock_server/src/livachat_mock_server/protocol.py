from __future__ import annotations

import random
import string
import time

from livachat_protocol.generated.client_message import (
    ClientMessage,
    ClientMessageAdapter,
)
from livachat_protocol.generated.server_message import (
    ServerAiAudio,
    ServerAiText,
    ServerAiVideoAck,
    ServerError,
    ServerPing,
    ServerPong,
    ServerSessionEnded,
    ServerSessionReady,
    ServerStatus,
    ServerTranscription,
    ServerMessage,
    ServerMessageAdapter,
)
from livachat_protocol.generated.protocol_models import (
    ServerAiAudioPayload,
    ServerAiTextImage,
    ServerAiTextPayload,
    ServerAiVideoAckPayload,
    ServerErrorPayload,
    ServerPingPayload,
    ServerPongPayload,
    ServerSessionEndedPayload,
    ServerSessionReadyPayload,
    ServerStatusPayload,
    ServerTranscriptionPayload,
    SessionStatusCode,
    StatusReasonCode,
    SessionEndReason,
    AudioDurationMs,
)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    return ClientMessageAdapter.validate_json(raw)


def dump_server_message(msg: ServerMessage) -> str:
    return ServerMessageAdapter.dump_json(msg).decode()


def now_ms() -> int:
    return int(time.time() * 1000)


def random_id(prefix: str, length: int) -> str:
    chars = string.ascii_lowercase + string.digits
    suffix = "".join(random.choice(chars) for _ in range(length))
    return f"{prefix}{suffix}"


def make_session_id() -> str:
    return random_id("sess_", 18)


def make_transcription_id() -> str:
    return random_id("trans_", 12)


def make_message_id() -> str:
    return random_id("msg_", 12)


def build_gallery_ai_text(seq: int, text: str, message_id: str, image_b64: str) -> ServerAiText:
    return build_ai_text(
        seq,
        text,
        message_id,
        0,
        True,
        ServerAiTextImage(
            data=image_b64,
            mime="image/jpeg",
            width=64,
            height=64,
            orientation=0,
            camera_facing="gallery",
        ),
    )


def build_session_ready(seq: int, session_id: str, restored: bool) -> ServerSessionReady:
    return ServerSessionReady(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerSessionReadyPayload(
            session_id=session_id,
            session_restored=restored,
            server_time_ms=now_ms(),
            keepalive_interval_s=15,
            server_version="0.1.0",
        ),
    )


def build_status(seq: int, status: SessionStatusCode, reason: StatusReasonCode) -> ServerStatus:
    return ServerStatus(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerStatusPayload(session_status=status, reason=reason),
    )


def build_transcription(
    seq: int, text: str, is_final: bool, transcription_id: str = "",
) -> ServerTranscription:
    tid = transcription_id or make_transcription_id()
    return ServerTranscription(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerTranscriptionPayload(
            text=text, is_final=is_final, transcription_id=tid,
        ),
    )


def build_ai_text(
    seq: int,
    text: str,
    message_id: str,
    index: int,
    is_final: bool,
    image: ServerAiTextImage | None = None,
) -> ServerAiText:
    return ServerAiText(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerAiTextPayload(
            text=text,
            message_id=message_id,
            index=index,
            is_final=is_final,
            image=image,
        ),
    )


def build_ai_audio(
    seq: int,
    data: str,
    message_id: str,
    index: int,
    is_final: bool,
    duration_ms: AudioDurationMs = 40,
) -> ServerAiAudio:
    return ServerAiAudio(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerAiAudioPayload(
            data=data,
            codec="opus",
            message_id=message_id,
            index=index,
            duration_ms=duration_ms,
            is_final=is_final,
        ),
    )


def build_ai_video_ack(seq: int, frame_id: int, ack_frame_id: int) -> ServerAiVideoAck:
    return ServerAiVideoAck(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerAiVideoAckPayload(
            frame_id=frame_id, ack_frame_id=ack_frame_id, processed=True,
        ),
    )


def build_error(
    seq: int, code: int, message: str, fatal: bool, recoverable: bool,
) -> ServerError:
    return ServerError(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerErrorPayload(
            code=code, message=message, fatal=fatal, recoverable=recoverable,
        ),
    )


def build_session_ended(
    seq: int, reason: SessionEndReason, session_id: str,
) -> ServerSessionEnded:
    return ServerSessionEnded(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerSessionEndedPayload(reason=reason, session_id=session_id),
    )


def build_pong(seq: int, ping_seq: int) -> ServerPong:
    return ServerPong(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerPongPayload(ping_seq=ping_seq),
    )


def build_ping(seq: int) -> ServerPing:
    return ServerPing(
        seq=seq,
        timestamp=now_ms(),
        payload=ServerPingPayload(),
    )
