from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis


class RedisStore:
    """Async facade over a Redis client for session state, connections, rate limits, and usage."""

    def __init__(self, client: aioredis.Redis):
        self._client = client
        self._prefix = getattr(client, "key_prefix", "livachat")

    # ------------------------------------------------------------------
    # key helpers
    # ------------------------------------------------------------------

    def _k(self, *segments: str) -> str:
        return ":".join((self._prefix, *segments))

    # ------------------------------------------------------------------
    # session snapshot (§8)
    # ------------------------------------------------------------------

    async def save_session_snapshot(self, session_id: str, snapshot: dict[str, Any], *, ttl: int = 30) -> None:
        key = self._k("session", session_id)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.hset(key, mapping={k: json.dumps(v) if not isinstance(v, str) else v for k, v in snapshot.items()})
            if ttl > 0:
                pipe.expire(key, ttl)
            await pipe.execute()

    async def load_session_snapshot(self, session_id: str) -> dict[str, Any] | None:
        key = self._k("session", session_id)
        raw: dict[str, str] = await self._client.hgetall(key)  # type: ignore[assignment]
        if not raw:
            return None
        return {k: self._deserialize_json(v) for k, v in raw.items()}

    async def delete_session_snapshot(self, session_id: str) -> None:
        key = self._k("session", session_id)
        await self._client.delete(key)

    # ------------------------------------------------------------------
    # session history (§8)
    # ------------------------------------------------------------------

    async def save_session_history(self, session_id: str, messages: list[dict[str, Any]], *, ttl: int = 30) -> None:
        key = self._k("session", session_id, "history")
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            if messages:
                pipe.rpush(key, *[json.dumps(m) for m in messages])
            if ttl > 0:
                pipe.expire(key, ttl)
            await pipe.execute()

    async def load_session_history(self, session_id: str, *, count: int = 3) -> list[dict[str, Any]]:
        key = self._k("session", session_id, "history")
        raw = await self._client.lrange(key, -count, -1)
        return [json.loads(item) for item in raw]

    # ------------------------------------------------------------------
    # connection registry (§8)
    # ------------------------------------------------------------------

    async def register_conn(self, device_id: str, instance_id: str, *, ttl_s: int | None = None) -> None:
        key = self._k("conn", device_id)
        if ttl_s:
            await self._client.set(key, instance_id, ex=ttl_s)
        else:
            await self._client.set(key, instance_id)

    async def unregister_conn(self, device_id: str) -> None:
        key = self._k("conn", device_id)
        await self._client.delete(key)

    async def get_conn_instance(self, device_id: str) -> str | None:
        key = self._k("conn", device_id)
        result = await self._client.get(key)
        return result if isinstance(result, str) else None

    # ------------------------------------------------------------------
    # device → session mapping (§8)
    # ------------------------------------------------------------------

    async def set_device_session(self, device_id: str, session_id: str, *, ttl: int = 30) -> None:
        key = self._k("device", device_id, "session")
        await self._client.set(key, session_id, ex=ttl)

    async def get_device_session(self, device_id: str) -> str | None:
        key = self._k("device", device_id, "session")
        result = await self._client.get(key)
        return result if isinstance(result, str) else None

    # ------------------------------------------------------------------
    # rate limiting (§8)
    # ------------------------------------------------------------------

    async def try_reconnect_quota(self, device_id: str, *, window_s: int = 300, max_attempts: int = 3) -> bool:
        """Return True if the device is allowed to reconnect (≤ *max_attempts* in *window_s*)."""
        key = self._k("ratelimit", "conn", device_id)
        count = await self._client.incr(key)
        if count == 1:
            await self._client.expire(key, window_s)
        return count <= max_attempts

    async def check_message_rate(self, session_id: str, *, window_s: int = 1, max_per_second: int = 50) -> bool:
        """Return True if the session is under the message rate limit."""
        key = self._k("ratelimit", "msg", session_id)
        count = await self._client.incr(key)
        if count == 1:
            await self._client.expire(key, window_s)
        return count <= max_per_second

    # ------------------------------------------------------------------
    # session lock (§8)
    # ------------------------------------------------------------------

    async def acquire_session_lock(self, session_id: str, *, ttl: int = 10) -> bool:
        """Acquire a distributed lock for the session. Returns True on success."""
        key = self._k("lock", "session", session_id)
        return bool(await self._client.set(key, "1", nx=True, ex=ttl))

    async def release_session_lock(self, session_id: str) -> None:
        key = self._k("lock", "session", session_id)
        await self._client.delete(key)

    # ------------------------------------------------------------------
    # provider usage (§8)
    # ------------------------------------------------------------------

    async def bump_provider_usage(
        self,
        date_str: str,
        provider: str,
        kind: str,
        *,
        count: int = 1,
    ) -> None:
        key = self._k("usage", date_str, provider)
        await self._client.hincrby(key, kind, count)

    # ------------------------------------------------------------------
    # provider health cache (§8)
    # ------------------------------------------------------------------

    async def set_provider_health(self, provider: str, status: str, *, ttl: int = 30) -> None:
        key = self._k("health", "providers")
        await self._client.hset(key, provider, status)
        if ttl > 0:
            await self._client.expire(key, ttl)

    async def get_provider_health(self) -> dict[str, str]:
        key = self._k("health", "providers")
        raw: dict[str, str] = await self._client.hgetall(key)  # type: ignore[assignment]
        return raw

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deserialize_json(value: str) -> Any:
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value
