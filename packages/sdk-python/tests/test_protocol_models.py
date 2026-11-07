from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


SDK_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = SDK_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from livachat_protocol.generated import (  # noqa: E402
    CameraFacing,
    ClientMessageAdapter,
    ClientSessionStart,
    ClientSessionStartConfig,
    ClientSessionStartPayload,
    ClientVideoPayload,
    ServerStatusPayload,
    VadSensitivity,
)


class ProtocolModelsRuntimeTests(unittest.TestCase):
    def test_camera_facing_includes_gallery_and_round_trips(self) -> None:
        self.assertEqual("gallery", CameraFacing.GALLERY.value)
        payload = ClientVideoPayload(
            data="AAAA", frame_id=1, width=640, height=480,
            orientation=90, camera_facing="gallery", quality=70,
        )
        self.assertEqual(CameraFacing.GALLERY, payload.camera_facing)

        wire = payload.model_dump_json(by_alias=True, exclude_unset=True)
        self.assertIn('"camera_facing":"gallery"', wire)
        parsed = ClientVideoPayload.model_validate_json(wire)
        self.assertEqual(CameraFacing.GALLERY, parsed.camera_facing)

    def test_client_message_round_trips_through_discriminator(self) -> None:
        msg = ClientSessionStart(
            seq=1,
            timestamp=1,
            payload=ClientSessionStartPayload(
                session_id=None,
                config=ClientSessionStartConfig(
                    enable_video=True,
                    audio_sample_rate=24000,
                    vad_sensitivity=VadSensitivity.MEDIUM,
                    client_version="ios-1.0.0",
                    language="zh-Hans",
                ),
            ),
        )

        wire = msg.model_dump_json(by_alias=True, exclude_unset=True)
        parsed = ClientMessageAdapter.validate_json(wire)

        self.assertIsInstance(parsed, ClientSessionStart)
        self.assertIn('"type":"session.start"', wire)
        self.assertIn('"session_id":null', wire)
        self.assertIn('"language":"zh-Hans"', wire)
        self.assertEqual("zh-Hans", parsed.payload.config.language)

    def test_session_start_language_is_omitted_when_unset(self) -> None:
        msg = ClientSessionStart(
            seq=1,
            timestamp=1,
            payload=ClientSessionStartPayload(
                session_id=None,
                config=ClientSessionStartConfig(
                    enable_video=True,
                    audio_sample_rate=16000,
                    vad_sensitivity="medium",
                    client_version="ios-1.0.0",
                ),
            ),
        )

        wire = msg.model_dump_json(by_alias=True, exclude_unset=True)

        self.assertNotIn('"language"', wire)

    def test_client_version_accepts_optional_build_segment(self) -> None:
        for value in ("android-1.0.0", "android-1.0.0.130", "ios-1.0.0.42"):
            ClientSessionStartConfig(
                enable_video=True,
                audio_sample_rate=16000,
                vad_sensitivity="medium",
                client_version=value,
            )

        with self.assertRaises(ValidationError):
            ClientSessionStartConfig(
                enable_video=True,
                audio_sample_rate=16000,
                vad_sensitivity="medium",
                client_version="android-1.0.0.130.7",
            )

    def test_runtime_constraints_are_enforced(self) -> None:
        with self.assertRaises(ValidationError):
            ClientSessionStart(
                seq=0,
                timestamp=1,
                payload=ClientSessionStartPayload(
                    session_id=None,
                    config=ClientSessionStartConfig(
                        enable_video=True,
                        audio_sample_rate=24000,
                        vad_sensitivity="medium",
                        client_version="ios-1.0.0",
                    ),
                ),
            )

        with self.assertRaises(ValidationError):
            ClientSessionStartConfig(
                enable_video=True,
                audio_sample_rate=44100,
                vad_sensitivity="medium",
                client_version="ios-1.0.0",
            )

    def test_extra_fields_follow_schema_policy(self) -> None:
        config = ClientSessionStartConfig(
            enable_video=True,
            audio_sample_rate=16000,
            vad_sensitivity="low",
            client_version="android-1.2.3",
            experimental_flag=True,
        )
        self.assertTrue(config.model_dump()["experimental_flag"])

        with self.assertRaises(ValidationError):
            ServerStatusPayload(
                session_status="idle",
                reason="connected",
                unexpected=True,
            )

    def test_optional_fields_are_omitted_when_unset(self) -> None:
        payload = ServerStatusPayload(
            session_status="idle",
            reason="connected",
        )

        self.assertNotIn("hint", payload.model_dump(exclude_unset=True))


if __name__ == "__main__":
    unittest.main()
