from __future__ import annotations

import pytest
import websockets

pytestmark = pytest.mark.e2e


class TestAuthFailed:
    async def test_invalid_token_rejected_401(self, e2e_server: str):
        url = f"{e2e_server}/v1/ws/stream?token=wrong-token&device_id=00000000-0000-0000-0000-000000000001"
        with pytest.raises(websockets.InvalidStatus) as exc:
            async with websockets.connect(url):
                pass
        assert exc.value.response.status_code == 401

    async def test_missing_token_rejected(self, e2e_server: str):
        url = f"{e2e_server}/v1/ws/stream?device_id=00000000-0000-0000-0000-000000000001"
        with pytest.raises(websockets.InvalidStatus):
            async with websockets.connect(url):
                pass

    async def test_invalid_device_id_rejected(self, e2e_server: str, e2e_token: str):
        url = f"{e2e_server}/v1/ws/stream?token={e2e_token}&device_id=not-a-uuid"
        with pytest.raises(websockets.InvalidStatus):
            async with websockets.connect(url):
                pass
