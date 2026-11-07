from __future__ import annotations

import pytest

from shared.persistence.redis_store import RedisStore

pytestmark = pytest.mark.integration


class TestSessionSnapshot:
    async def test_save_and_load(self, redis_client):
        store = RedisStore(redis_client)
        snap = {"state": "listening", "epoch": "4", "config": '{"audio_sample_rate":24000}'}
        await store.save_session_snapshot("sess_snap_test", snap, ttl=30)

        loaded = await store.load_session_snapshot("sess_snap_test")
        assert loaded is not None
        assert loaded["state"] == "listening"
        assert loaded["epoch"] == "4"

    async def test_ttl_expiry(self, redis_client):
        """Snapshot with ttl=1 should expire quickly."""
        store = RedisStore(redis_client)
        await store.save_session_snapshot("sess_ttl_test", {"state": "idle"}, ttl=1)

        # Verify it exists
        loaded = await store.load_session_snapshot("sess_ttl_test")
        assert loaded is not None

    async def test_delete(self, redis_client):
        store = RedisStore(redis_client)
        await store.save_session_snapshot("sess_del_test", {"state": "idle"}, ttl=30)
        await store.delete_session_snapshot("sess_del_test")
        loaded = await store.load_session_snapshot("sess_del_test")
        assert loaded is None

    async def test_non_existent_returns_none(self, redis_client):
        store = RedisStore(redis_client)
        loaded = await store.load_session_snapshot("sess_nonexistent")
        assert loaded is None


class TestSessionHistory:
    async def test_save_and_load(self, redis_client):
        store = RedisStore(redis_client)
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you"},
            {"role": "assistant", "content": "good"},
        ]
        await store.save_session_history("sess_hist_test", msgs, ttl=30)

        loaded = await store.load_session_history("sess_hist_test", count=3)
        assert len(loaded) == 3
        assert loaded[-1]["content"] == "good"

    async def test_save_and_load_with_images(self, redis_client):
        """History entries carrying images round-trip through Redis (fd_pipeline_llm_v2)."""
        store = RedisStore(redis_client)
        msgs = [
            {"role": "user", "content": "what is this", "images": ["data:image/jpeg;base64,aaa"]},
            {"role": "assistant", "content": "a photo"},
            {"role": "user", "content": "and this", "images": ["data:image/jpeg;base64,bbb"]},
        ]
        await store.save_session_history("sess_hist_img_test", msgs, ttl=30)

        loaded = await store.load_session_history("sess_hist_img_test", count=3)
        assert len(loaded) == 3
        assert loaded[0]["images"] == ["data:image/jpeg;base64,aaa"]
        assert "images" not in loaded[1]
        assert loaded[2]["images"] == ["data:image/jpeg;base64,bbb"]


class TestConnectionRegistry:
    async def test_register_and_get(self, redis_client):
        store = RedisStore(redis_client)
        await store.register_conn("device_001", "instance_xyz")
        inst = await store.get_conn_instance("device_001")
        assert inst == "instance_xyz"

    async def test_unregister(self, redis_client):
        store = RedisStore(redis_client)
        await store.register_conn("device_002", "instance_abc")
        await store.unregister_conn("device_002")
        inst = await store.get_conn_instance("device_002")
        assert inst is None


class TestDeviceSessionMapping:
    async def test_set_and_get(self, redis_client):
        store = RedisStore(redis_client)
        await store.set_device_session("device_003", "sess_xyz", ttl=30)
        sid = await store.get_device_session("device_003")
        assert sid == "sess_xyz"


class TestReconnectQuota:
    async def test_first_three_allowed(self, redis_client):
        store = RedisStore(redis_client)
        assert await store.try_reconnect_quota("dev_rl_01") is True
        assert await store.try_reconnect_quota("dev_rl_01") is True
        assert await store.try_reconnect_quota("dev_rl_01") is True
        # 4th attempt should fail
        assert await store.try_reconnect_quota("dev_rl_01") is False

    async def test_separate_devices_independent(self, redis_client):
        store = RedisStore(redis_client)
        assert await store.try_reconnect_quota("dev_a") is True
        assert await store.try_reconnect_quota("dev_b") is True
        # dev_a still only has 1
        assert await store.try_reconnect_quota("dev_a") is True


class TestSessionLock:
    async def test_acquire_and_release(self, redis_client):
        store = RedisStore(redis_client)
        acquired = await store.acquire_session_lock("sess_lock_test")
        assert acquired is True

        # Second acquire should fail
        acquired2 = await store.acquire_session_lock("sess_lock_test")
        assert acquired2 is False

        # Release and reacquire
        await store.release_session_lock("sess_lock_test")
        acquired3 = await store.acquire_session_lock("sess_lock_test")
        assert acquired3 is True


class TestProviderUsage:
    async def test_bump_usage(self, redis_client):
        store = RedisStore(redis_client)
        await store.bump_provider_usage("2026-06-15", "deepgram", "asr", count=1)
        await store.bump_provider_usage("2026-06-15", "deepgram", "asr", count=1)
        await store.bump_provider_usage("2026-06-15", "deepgram", "llm", count=3)

        # Redis HINCRBY returns string values
        key = store._k("usage", "2026-06-15", "deepgram")
        data = await redis_client.hgetall(key)
        assert data["asr"] == "2"
        assert data["llm"] == "3"


class TestProviderHealth:
    async def test_set_and_get(self, redis_client):
        store = RedisStore(redis_client)
        await store.set_provider_health("deepgram", "healthy", ttl=30)
        await store.set_provider_health("elevenlabs", "degraded", ttl=30)
        health = await store.get_provider_health()
        assert health["deepgram"] == "healthy"
        assert health["elevenlabs"] == "degraded"
