from __future__ import annotations

import json

import pytest

from gateway.codec import InboundDecodeGuard, WireCodec
from gateway.seq import OutboundSeqAllocator
from shared.utils.errors import ProtocolError
from shared.version import SERVER_VERSION

_SID = "sess_" + "a" * 18  # valid session_id pattern


@pytest.fixture
def codec() -> WireCodec:
    alloc = OutboundSeqAllocator()
    return WireCodec(alloc)


class TestInboundParsing:
    def test_parse_valid_ping(self, codec):
        raw = json.dumps({"type": "ping", "seq": 1, "timestamp": 1000, "payload": {}})
        msg = codec.parse(raw)
        assert msg.type == "ping"

    def test_parse_invalid_json_raises_protocol_error(self, codec):
        with pytest.raises(ProtocolError):
            codec.parse("not json")

    def test_parse_missing_type_raises(self, codec):
        with pytest.raises(ProtocolError):
            codec.parse('{"seq":1,"timestamp":1}')

    def test_parse_valid_session_start(self, codec):
        raw = json.dumps(
            {
                "type": "session.start",
                "seq": 1,
                "timestamp": 1000,
                "payload": {
                    "session_id": None,
                    "config": {
                        "enable_video": True,
                        "audio_sample_rate": 24000,
                        "vad_sensitivity": "medium",
                        "client_version": "android-1.0.0",
                    },
                },
            }
        )
        msg = codec.parse(raw)
        assert msg.type == "session.start"

    def test_parse_session_start_avdetector_disable(self, codec):
        raw = json.dumps(
            {
                "type": "session.start",
                "seq": 1,
                "timestamp": 1000,
                "payload": {
                    "session_id": None,
                    "config": {
                        "enable_video": True,
                        "audio_sample_rate": 24000,
                        "vad_sensitivity": "medium",
                        "client_version": "android-1.0.0",
                        "avdetector": "disable",
                    },
                },
            }
        )
        msg = codec.parse(raw)
        assert msg.payload.config.avdetector.value == "disable"
        assert "avdetector" in msg.payload.config.model_fields_set

    def test_parse_session_start_explicit_null_reaches_dto(self, codec):
        """Schema 不允许 null，但生成器把 optional 生成 nullable DTO；服务端需守卫。"""
        raw = json.dumps(
            {
                "type": "session.start",
                "seq": 1,
                "timestamp": 1000,
                "payload": {
                    "session_id": None,
                    "config": {
                        "enable_video": True,
                        "audio_sample_rate": 24000,
                        "vad_sensitivity": "medium",
                        "client_version": "android-1.0.0",
                        "avdetector": None,
                    },
                },
            }
        )
        msg = codec.parse(raw)
        assert msg.payload.config.avdetector is None
        assert "avdetector" in msg.payload.config.model_fields_set


class TestOutboundBuilders:
    def test_make_ping_uses_seq_placeholder(self, codec):
        frame = codec.make_ping()
        data = json.loads(frame)
        assert data["type"] == "ping"
        assert data["seq"] == 1  # dummy — replaced by writer with real seq
        assert "timestamp" in data

    def test_make_pong(self, codec):
        frame = codec.make_pong(42)
        data = json.loads(frame)
        assert data["type"] == "pong"
        assert data["payload"]["ping_seq"] == 42

    def test_make_error(self, codec):
        frame = codec.make_error(code=1006, message="bad", fatal=True)
        data = json.loads(frame)
        assert data["type"] == "error"
        assert data["payload"]["code"] == 1006
        assert data["payload"]["fatal"] is True

    def test_make_session_changed_config_echo_carries_avdetector(self, codec):
        frame = codec.make_session_changed(mode="config", avdetector="disable")
        data = json.loads(frame)
        assert data["payload"]["mode"] == "config"
        assert data["payload"]["avdetector"] == "disable"
        # 可选字段无值时省略；config 回声只带 mode + avdetector。
        assert "my_language" not in data["payload"]
        assert "local_language" not in data["payload"]
        assert "usecase" not in data["payload"]

    def test_make_session_changed_normal_mode_omits_avdetector(self, codec):
        frame = codec.make_session_changed(mode="chat")
        data = json.loads(frame)
        assert data["payload"]["mode"] == "chat"
        assert "avdetector" not in data["payload"]

    def test_make_session_ready(self, codec):
        frame = codec.make_session_ready(
            session_id=_SID, restored=False, server_time_ms=12345, keepalive_interval_s=15
        )
        data = json.loads(frame)
        assert data["type"] == "session.ready"
        assert data["payload"]["session_id"] == _SID
        assert data["payload"]["server_version"] == SERVER_VERSION

    def test_make_session_ended(self, codec):
        frame = codec.make_session_ended(reason="user_quit", session_id=_SID)
        data = json.loads(frame)
        assert data["type"] == "session.ended"
        assert data["payload"]["reason"] == "user_quit"

    def test_make_status(self, codec):
        frame = codec.make_status(session_status="listening", reason="connected")
        data = json.loads(frame)
        assert data["type"] == "status"
        assert data["payload"]["session_status"] == "listening"

    async def test_finalize_seq_injects_real_seq(self, codec):
        """Seq is assigned by the writer at dequeue time via finalize_seq."""
        frame = codec.make_ping()
        assert json.loads(frame)["seq"] == 1  # dummy

        finalized = codec.finalize_seq(frame, 42)
        assert json.loads(finalized)["seq"] == 42

    async def test_finalize_seq_only_replaces_seq_field(self, codec):
        """finalize_seq must only replace the seq field in the JSON."""
        frame = codec.make_error(code=5000, message="test", fatal=False)
        data_before = json.loads(frame)
        assert data_before["seq"] == 1

        finalized = codec.finalize_seq(frame, 5)
        data_after = json.loads(finalized)
        assert data_after["seq"] == 5
        # code field in payload must not change
        assert data_after["payload"]["code"] == 5000

    def test_seq_placeholder_format(self, codec):
        """M5: the seq placeholder format is part of the serialization contract.

        Every message type must serialize seq as ``"seq":1,`` (compact, no space).
        finalize_seq relies on this exact substring for replacement.
        """
        messages = [
            codec.make_ping(),
            codec.make_pong(42),
            codec.make_error(code=1006, message="x", fatal=False),
            codec.make_session_ready(session_id=_SID, restored=False, server_time_ms=0, keepalive_interval_s=15),
            codec.make_session_ended(reason="user_quit", session_id=_SID),
            codec.make_status(session_status="listening", reason="connected"),
        ]
        for raw in messages:
            assert '"seq":1,' in raw, f"seq placeholder format changed in: {raw[:60]}"


class TestInboundDecodeGuard:
    def test_resets_on_ok(self):
        g = InboundDecodeGuard(max_consecutive=3)
        g.on_error()
        g.on_error()
        g.on_ok()
        assert not g.should_close

    def test_signals_after_limit(self):
        g = InboundDecodeGuard(max_consecutive=2)
        g.on_error()
        g.on_error()
        assert g.should_close


class TestVideoTextLimit:
    """video.payload.text（docs/android_screen_recording.md §5.1/§8.2）：
    可选字段随 payload 透传；超长（>512，仓库首例 maxLength）由协议层拒绝为 ProtocolError
    → connection.py 回 1006 非致命错误帧（不静默截断）。"""

    def _video_raw(self, text: str | None) -> str:
        payload: dict = {
            "data": "AAAA", "frame_id": 1, "width": 640, "height": 480,
            "orientation": 90, "camera_facing": "back", "quality": 70,
        }
        if text is not None:
            payload["text"] = text
        return json.dumps({"type": "video", "seq": 2, "timestamp": 2000, "payload": payload})

    def test_text_parses(self, codec):
        msg = codec.parse(self._video_raw("描述图片内容"))
        assert msg.payload.text == "描述图片内容"

    def test_text_absent_is_none(self, codec):
        msg = codec.parse(self._video_raw(None))
        assert msg.payload.text is None

    def test_text_at_limit_parses(self, codec):
        msg = codec.parse(self._video_raw("x" * 512))
        assert msg.payload.text is not None and len(msg.payload.text) == 512

    def test_text_over_limit_rejected(self, codec):
        with pytest.raises(ProtocolError):
            codec.parse(self._video_raw("x" * 513))
