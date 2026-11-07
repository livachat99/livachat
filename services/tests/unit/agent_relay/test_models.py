from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_relay.models import (
    AgentEvent,
    JobStatusItem,
    JobStatusPushMessage,
    PushMessage,
    make_recording_ready_event,
    make_session_ended_event,
    make_session_started_event,
    make_user_utterance_event,
)


class TestAgentEvent:
    def test_deserialize_user_utterance(self):
        raw = (
            '{"kind":"user.utterance","device_id":"dev-1","session_id":"sess-1",'
            '"ts_ms":1721894400000,"transcription_id":"tr-1","text":"hello","language":"en"}'
        )
        ev = AgentEvent.from_json(raw)
        assert ev.kind == "user.utterance"
        assert ev.device_id == "dev-1"
        assert ev.session_id == "sess-1"
        assert ev.ts_ms == 1721894400000
        assert ev.transcription_id == "tr-1"
        assert ev.text == "hello"
        assert ev.language == "en"

    def test_deserialize_recording_ready(self):
        raw = (
            '{"kind":"recording.ready","device_id":"dev-1","session_id":"sess-1",'
            '"ts_ms":1721894400000,"recording_id":"rec-1","manifest_path":"/tmp/manifest.json","duration_ms":60000}'
        )
        ev = AgentEvent.from_json(raw)
        assert ev.kind == "recording.ready"
        assert ev.recording_id == "rec-1"
        assert ev.manifest_path == "/tmp/manifest.json"
        assert ev.duration_ms == 60000

    def test_deserialize_session_started(self):
        raw = '{"kind":"session.started","device_id":"dev-1","session_id":"sess-1","ts_ms":1721894400000}'
        ev = AgentEvent.from_json(raw)
        assert ev.kind == "session.started"

    def test_deserialize_session_ended(self):
        raw = '{"kind":"session.ended","device_id":"dev-1","session_id":"sess-1","ts_ms":1721894400000}'
        ev = AgentEvent.from_json(raw)
        assert ev.kind == "session.ended"

    def test_unknown_kind(self):
        raw = '{"kind":"unknown.event","device_id":"dev-1","session_id":"sess-1","ts_ms":1721894400000}'
        with pytest.raises(ValidationError):
            AgentEvent.from_json(raw)

    def test_missing_required_field(self):
        raw = '{"kind":"user.utterance","session_id":"sess-1","ts_ms":1721894400000}'
        with pytest.raises(ValidationError):
            AgentEvent.from_json(raw)

    def test_ts_ms_wrong_type(self):
        raw = (
            '{"kind":"user.utterance","device_id":"dev-1","session_id":"sess-1",'
            '"ts_ms":"not-a-number","text":"hello"}'
        )
        with pytest.raises(ValidationError):
            AgentEvent.from_json(raw)


class TestPushMessage:
    def test_serialize_ai_text(self):
        msg = PushMessage(
            device_id="dev-1",
            session_id="sess-1",
            message_id="msg_agent_01",
            text="hello from agent",
            agent_name="hermes",
            reply_to="tr-1",
        )
        raw = msg.to_json()
        assert "source" in raw
        assert '"agent"' in raw
        assert "agent_name" in raw
        assert "hermes" in raw
        assert "reply_to" in raw

    def test_proactive_flag(self):
        msg = PushMessage(
            device_id="dev-1",
            session_id="sess-1",
            message_id="msg_agent_02",
            text="proactive message",
            agent_name="hermes",
            proactive=True,
        )
        raw = msg.to_json()
        assert '"proactive":true' in raw

    def test_exclude_none_fields(self):
        msg = PushMessage(
            device_id="dev-1",
            session_id="sess-1",
            message_id="msg_agent_03",
            text="a reply",
            agent_name="hermes",
        )
        raw = msg.to_json()
        assert '"reply_to"' not in raw

    def test_job_key_serialized_when_set(self):
        msg = PushMessage(
            device_id="dev-1",
            session_id="sess-1",
            message_id="msg_agent_job_jk1",
            text="result",
            job_key="jk1",
        )
        raw = msg.to_json()
        assert '"job_key":"jk1"' in raw

    def test_no_job_key_bytes_unchanged(self):
        """无 job_key 时序列化字节不变（exclude_none 回归，滚动升级兼容）。"""
        def build() -> PushMessage:
            return PushMessage(
                device_id="dev-1",
                session_id="sess-1",
                message_id="msg_agent_04",
                text="a reply",
                agent_name="hermes",
                proactive=True,
            )

        assert build().to_json() == build().to_json()  # 确定性
        assert '"job_key"' not in build().to_json()


class TestJobStatusPushMessage:
    def test_serialize_full(self):
        msg = JobStatusPushMessage(
            device_id="dev-1",
            jobs=[
                JobStatusItem(
                    job_id="9f3c2ab4d1e84f0c9f3c2ab4d1e84f0c",
                    agent_name="hermes",
                    summary="正在查询东京机票",
                    started_at_ms=1712345640000,
                    running_time_ms=47000,
                )
            ],
        )
        raw = msg.to_json()
        assert '"kind":"job_status"' in raw
        assert '"device_id":"dev-1"' in raw
        assert '"job_id":"9f3c2ab4d1e84f0c9f3c2ab4d1e84f0c"' in raw
        assert '"started_at_ms":1712345640000' in raw
        assert '"running_time_ms":47000' in raw

    def test_round_trip(self):
        msg = JobStatusPushMessage(
            device_id="dev-1",
            jobs=[
                JobStatusItem(
                    job_id="abc",
                    summary="s",
                    started_at_ms=1000,
                    running_time_ms=200,
                )
            ],
        )
        back = JobStatusPushMessage.model_validate_json(msg.to_json())
        assert back.kind == "job_status"
        assert back.device_id == "dev-1"
        assert back.jobs[0].job_id == "abc"
        assert back.jobs[0].agent_name == "hermes"
        assert back.jobs[0].started_at_ms == 1000
        assert back.jobs[0].running_time_ms == 200

    def test_empty_jobs_default(self):
        msg = JobStatusPushMessage(device_id="dev-1")
        assert msg.jobs == []
        back = JobStatusPushMessage.model_validate_json(msg.to_json())
        assert back.jobs == []

    def test_rejects_wrong_kind(self):
        with pytest.raises(ValidationError):
            JobStatusPushMessage(kind="ai_text", device_id="dev-1")


class TestHelperFactories:
    def test_make_user_utterance_event(self):
        ev = make_user_utterance_event(
            device_id="dev-1",
            session_id="sess-1",
            ts_ms=1721894400000,
            transcription_id="tr-1",
            text="hello",
            language="en",
        )
        assert ev.kind == "user.utterance"
        assert ev.device_id == "dev-1"
        assert ev.transcription_id == "tr-1"
        assert ev.text == "hello"

    def test_make_recording_ready_event(self):
        ev = make_recording_ready_event(
            device_id="dev-1",
            session_id="sess-1",
            ts_ms=1721894400000,
            recording_id="rec-1",
            manifest_path="/tmp/manifest.json",
            duration_ms=60000,
        )
        assert ev.kind == "recording.ready"
        assert ev.recording_id == "rec-1"
        assert ev.duration_ms == 60000

    def test_make_session_started_event(self):
        ev = make_session_started_event(
            device_id="dev-1",
            session_id="sess-1",
            ts_ms=1721894400000,
            language="zh-Hant",
        )
        assert ev.kind == "session.started"
        assert ev.language == "zh-Hant"

    def test_make_session_ended_event(self):
        ev = make_session_ended_event(
            device_id="dev-1",
            session_id="sess-1",
            ts_ms=1721894400000,
        )
        assert ev.kind == "session.ended"
