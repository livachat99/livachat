from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestWithAudio:
    async def test_audio_frame_before_session_is_ignored(self, e2e_client):
        """无 session 时 audio 被静默忽略（无响应，连接存活）。"""
        await e2e_client.audio_frame(seq=1)
        pong = await e2e_client.ping(seq=2)
        assert pong["type"] == "pong"

    async def test_audio_after_session_start(self, e2e_client):
        await e2e_client.session_start(seq=1)
        assert (await e2e_client.recv())["type"] == "session.ready"
        for _ in range(2):
            await e2e_client.recv()  # 初始 status（idle → listening）
        # listening 状态下 audio 不改变状态 → 无 status 响应；连接保持存活
        await e2e_client.audio_frame(seq=2, vad_state="speech")
        pong = await e2e_client.ping(seq=3)
        assert pong["type"] == "pong"
