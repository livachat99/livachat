from __future__ import annotations

import logging
from typing import Any

from livachat_protocol.generated.client_message import ClientMessage, ClientMessageAdapter
from livachat_protocol.generated.protocol_models import (
    ServerErrorPayload,
    ServerPingPayload,
    ServerPongPayload,
    ServerSessionChangedPayload,
    ServerSessionEndedPayload,
    ServerSessionReadyPayload,
    ServerStatusPayload,
    SessionStatusCode,
    StatusReasonCode,
)
from livachat_protocol.generated.server_message import (
    ServerError,
    ServerMessage,
    ServerPing,
    ServerPong,
    ServerSessionChanged,
    ServerSessionEnded,
    ServerSessionReady,
    ServerStatus,
)
from pydantic import ValidationError

from gateway.seq import OutboundSeqAllocator
from shared.utils.errors import ProtocolError
from shared.utils.time import now_ms
from shared.version import SERVER_VERSION

logger = logging.getLogger("livachat.gateway.codec")

_MAX_CONSECUTIVE_INVALID = 10


class WireCodec:
    """Thin wrapper around protocol DTO adapters.

    Inbound: ``ClientMessageAdapter.validate_json(raw)``
    Outbound: ``build(type, payload)`` with auto-filled seq + timestamp.
    """

    def __init__(self, seq_allocator: OutboundSeqAllocator):
        self._seq = seq_allocator

    # -- inbound ----------------------------------------------------------

    def parse(self, raw: str) -> ClientMessage:
        """Parse a raw JSON frame into a :class:`ClientMessage`.

        Raises :class:`ProtocolError` on any validation failure.
        """
        try:
            return ClientMessageAdapter.validate_json(raw)
        except ValidationError as exc:
            raise ProtocolError(str(exc)) from exc

    # -- outbound ---------------------------------------------------------

    def build(self, msg: ServerMessage) -> str:
        """Serialise *msg* (with pre-filled seq/timestamp) to a JSON string."""
        return msg.model_dump_json(exclude_unset=True)

    def build_typed(self, payload: Any, *, msg_cls: type[ServerMessage]) -> str:
        """Build and serialise a server message in one call.

        *payload* is the per-type payload model instance.
        *msg_cls* is the server-message wrapper class (e.g. ``ServerPing``).

        Seq is set to a dummy value (1); the writer task replaces it with the
        real monotonic seq at dequeue time to avoid cross-coroutine races (design §4.7).
        """
        ts = now_ms()
        msg = msg_cls(seq=1, timestamp=ts, payload=payload)
        return self.build(msg)

    @staticmethod
    def finalize_seq(raw: str, seq: int) -> str:
        """Replace the dummy seq value with the real outbound seq assigned by the writer.

        Uses simple string replace — the placeholder ``"seq": 1,`` is unique in our
        message format (seq is always the first numeric field, no other key has the
        value ``"seq": 1``).
        """
        return raw.replace('"seq":1,', f'"seq":{seq},', 1)

    # -- convenience builders ---------------------------------------------

    def make_ping(self) -> str:
        return self.build_typed(ServerPingPayload(), msg_cls=ServerPing)

    def make_pong(self, ping_seq: int) -> str:
        return self.build_typed(ServerPongPayload(ping_seq=ping_seq), msg_cls=ServerPong)

    def make_error(self, *, code: int, message: str, fatal: bool = False, recoverable: bool = True) -> str:
        return self.build_typed(
            ServerErrorPayload(code=code, message=message, fatal=fatal, recoverable=recoverable),
            msg_cls=ServerError,
        )

    def make_session_ready(
        self, *, session_id: str, restored: bool, server_time_ms: int, keepalive_interval_s: int
    ) -> str:
        return self.build_typed(
            ServerSessionReadyPayload(
                session_id=session_id,
                session_restored=restored,
                server_time_ms=server_time_ms,
                keepalive_interval_s=keepalive_interval_s,
                server_version=SERVER_VERSION,
            ),
            msg_cls=ServerSessionReady,
        )

    def make_session_ended(self, *, reason: str, session_id: str) -> str:
        return self.build_typed(
            ServerSessionEndedPayload(reason=reason, session_id=session_id),  # type: ignore[arg-type]
            msg_cls=ServerSessionEnded,
        )

    def make_session_changed(
        self, *, mode: str, my_language: str | None = None, local_language: str | None = None,
        usecase: str | None = None, avdetector: str | None = None,
    ) -> str:
        # 可选字段只在有值时加入：build_typed 用 exclude_unset=True，显式传 None
        # 会被序列化成 null，而 schema 把这些字段定义为 string（不是 nullable）。
        # config 回声尤其只应携带 mode + avdetector，不应带三个 null。
        kwargs: dict[str, object] = {"mode": mode}
        if my_language is not None:
            kwargs["my_language"] = my_language
        if local_language is not None:
            kwargs["local_language"] = local_language
        if usecase is not None:
            kwargs["usecase"] = usecase
        if avdetector is not None:
            kwargs["avdetector"] = avdetector
        return self.build_typed(
            ServerSessionChangedPayload(**kwargs),  # type: ignore[arg-type]
            msg_cls=ServerSessionChanged,
        )

    def make_status(self, *, session_status: SessionStatusCode | str, reason: StatusReasonCode | str) -> str:
        return self.build_typed(
            ServerStatusPayload(session_status=session_status, reason=reason),  # type: ignore[arg-type]
            msg_cls=ServerStatus,
        )


class InboundDecodeGuard:
    """Tracks consecutive decode failures and signals when the connection should be closed."""

    def __init__(self, max_consecutive: int = _MAX_CONSECUTIVE_INVALID) -> None:
        self._max = max_consecutive
        self._count = 0

    def on_ok(self) -> None:
        self._count = 0

    def on_error(self) -> None:
        self._count += 1

    @property
    def should_close(self) -> bool:
        return self._count >= self._max
