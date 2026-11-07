from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Redis fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    client.key_prefix = "livachat"  # type: ignore[attr-defined]
    yield client
    await client.aclose()
