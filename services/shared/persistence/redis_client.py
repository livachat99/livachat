from __future__ import annotations

import redis.asyncio as aioredis


def create_redis_client(url: str, *, key_prefix: str = "livachat") -> aioredis.Redis:
    """Create an async Redis client.

    The *key_prefix* is stored on the client instance for use by :class:`RedisStore`.
    """
    client = aioredis.from_url(
        url,
        decode_responses=True,
        socket_timeout=None,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
    )
    client.key_prefix = key_prefix  # type: ignore[attr-defined]
    return client
