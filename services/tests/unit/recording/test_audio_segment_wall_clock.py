"""Unit tests: 音频段「墙钟兜底」切段（docs/audio_segment_rotation.md §4/§6）。

覆盖：切段规则两者取先到（E1/E9 余量）、空洞场景（本问题回归）、定时器与包路径
竞态幂等、attach/detach/flush 的句柄清理、收尾归零（#0 契约）、publish 口径
（E6/E8）、关闭 reason 遥测。

驱动方式：段长用极小值（0.05s）跑真实定时器；余量按模块常量注入 0（或放大到
不会触发），包路径的墙钟检查靠回拨 `_audio_segment_opened_at` 触发——`loop.time()`
不可注入，不为此造假时钟。
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

import recording.session as session_mod
from recording.file_registry import RecordingFileRegistry, uuid7_to_ms
from recording.session import RecordingSession

SEG_S = 0.05  # 极小段长：内容阈值 50ms、墙钟阈值 50ms（+余量）
FRAME_MS = 20


class _FakeChatStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def record_file(self, **kw) -> None:
        self.calls.append(("record", kw))

    async def touch_file(self, **kw) -> None:
        self.calls.append(("touch", kw))


class _FakePublisher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish_recording_ready(self, **kw) -> None:
        self.calls.append(kw)


def _make_session(tmp_path, *, segment_s: float = SEG_S, store=None,
                  publisher=None) -> RecordingSession:
    s = RecordingSession(
        recording_session_id="rec-1",
        device_uuid="dev-1",
        session_date="2026-09-25",
        storage_path=tmp_path / "rec-1",
        config={"audio_segment_s": segment_s},
    )
    if store is not None:
        s.file_registry = RecordingFileRegistry(
            store, tmp_path, device_uuid="dev-1", recording_session_id="rec-1")
    s.agent_publisher = publisher
    return s


def _feed(s: RecordingSession, n: int = 1) -> None:
    for _ in range(n):
        s.on_media(kind="audio", data=b"\x00" * 20, duration_ms=FRAME_MS, ts_ms=0)


def _audio_records(store: _FakeChatStore) -> list[dict]:
    return [kw for op, kw in store.calls if op == "record" and kw["kind"] == "audio"]


def _close_reasons(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.reason for r in caplog.records if hasattr(r, "reason")]


async def test_wall_clock_closes_idle_segment_then_next_packet_starts_new_one(
        tmp_path, monkeypatch, caplog):
    """§6-1a 本问题回归：建段 → 无包推进 ≥ 段长 → 定时器关闭并登记；之后首包建新段，
    新段 uuid7 = 该包时刻（不再是旧段的建段时刻）。"""
    monkeypatch.setattr(session_mod, "_AUDIO_WALL_BACKSTOP_MARGIN_MS", 0)
    caplog.set_level(logging.INFO)
    store = _FakeChatStore()
    s = _make_session(tmp_path, store=store)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        _feed(s)
        await asyncio.sleep(0.02)
        assert s._ogg_writer is not None
        assert _audio_records(store) == []          # 未到阈值，尚未落地

        await asyncio.sleep(0.12)                   # 无包推进 ≥ 段长
        assert s._ogg_writer is None            # 定时器关闭，且不自动开新段
        assert s._audio_timer is None
        recs = _audio_records(store)
        assert len(recs) == 1
        assert recs[0]["duration_ms"] == FRAME_MS   # 内容时长（非 0、非 60_000）
        assert _close_reasons(caplog) == ["wall_clock"]
        closed_ts_ms = recs[0]["ts_ms"]

        await asyncio.sleep(0.1)                    # 空洞期间不产生空段
        assert len(_audio_records(store)) == 1

        _feed(s)                                    # 恢复首包 → 新段
        await asyncio.sleep(0.02)
        assert s._audio_segment_index == 2
        part = s._ogg_writer._path                  # 新段仍是 .part（未关闭）
        new_ts_ms = uuid7_to_ms(part.name)
        assert new_ts_ms is not None
        assert new_ts_ms >= closed_ts_ms + 100      # ts 跟随恢复时刻，不是旧段时刻
        assert abs(new_ts_ms - time.time() * 1000) < 5_000
    finally:
        await s.finalize()


async def test_content_full_wins_over_wall_clock(tmp_path, caplog):
    """§6-1b/k①：默认余量下连续喂包 → 内容满先到（E9 余量保证内容路径优先）。"""
    caplog.set_level(logging.INFO)
    store = _FakeChatStore()
    s = _make_session(tmp_path, store=store)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        _feed(s, 3)                                 # 内容 60ms ≥ 50ms
        await asyncio.sleep(0.02)
        assert _close_reasons(caplog) == ["content_full"]
        assert s._ogg_writer is None
        assert s._audio_timer is None               # 关闭时 cancel + 清空
        recs = _audio_records(store)
        assert len(recs) == 1 and recs[0]["duration_ms"] == 3 * FRAME_MS
    finally:
        await s.finalize()


async def test_stale_timer_callback_is_noop_and_finish_is_idempotent(tmp_path, monkeypatch):
    """§6-1c/d：段 #1 的迟到回调不得误关段 #2（段序号身份校验）；到期回调连点只关一次。"""
    monkeypatch.setattr(session_mod, "_AUDIO_WALL_BACKSTOP_MARGIN_MS", 60_000)  # 墙钟不参与
    store = _FakeChatStore()
    s = _make_session(tmp_path, store=store)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        _feed(s)
        await asyncio.sleep(0.02)
        assert s._ogg_writer is not None

        s._audio_segment_ms = int(SEG_S * 1000)     # 下一包把内容推过阈值 → 内容满关闭
        _feed(s)
        await asyncio.sleep(0.02)
        assert s._ogg_writer is None
        registrations = len(_audio_records(store))
        assert registrations == 1

        _feed(s)                                    # 新段（段 #2）
        await asyncio.sleep(0.02)
        assert s._audio_segment_index == 2 and s._ogg_writer is not None

        s._on_audio_segment_timeout(1)              # 段 #1 的迟到回调
        assert s._ogg_writer is not None and s._audio_segment_index == 2
        assert len(_audio_records(store)) == registrations

        s._on_audio_segment_timeout(2)              # 段 #2 到期 → 收尾
        s._on_audio_segment_timeout(2)              # 重复点火 → 幂等（不再登记）
        await asyncio.sleep(0.02)
        assert len(_audio_records(store)) == registrations + 1
        assert s._ogg_writer is None
    finally:
        await s.finalize()


async def test_detach_and_finalize_cancel_timer_and_no_part_left(tmp_path, monkeypatch):
    """§6-1d：detach / finalize 取消定时器、段落地、无 .part 残留、无 fd 泄漏。"""
    monkeypatch.setattr(session_mod, "_AUDIO_WALL_BACKSTOP_MARGIN_MS", 0)
    store = _FakeChatStore()
    s = _make_session(tmp_path, store=store)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        _feed(s, 2)
        await asyncio.sleep(0.02)
        timer = s._audio_timer
        assert timer is not None
        await s.detach()
        assert timer.cancelled()
        assert s._audio_timer is None
        assert s._audio_segment_opened_at is None
        recs = _audio_records(store)
        assert len(recs) == 1 and recs[0]["duration_ms"] == 2 * FRAME_MS
        audio_dir = s.storage_path / "audio"
        assert not any(p.name.endswith(".part") for p in audio_dir.iterdir())

        await s.attach()                            # 重新 attach：句柄已清，可再建段
        assert s._audio_timer is None
        _feed(s)
        await asyncio.sleep(0.02)
        assert s._ogg_writer is not None and s._audio_timer is not None
        timer = s._audio_timer
        await s.finalize()
        assert timer.cancelled()
        assert s._audio_timer is None
        assert len(_audio_records(store)) == 2
    finally:
        await s.finalize()


async def test_finish_resets_content_counter_across_segments(tmp_path, monkeypatch, caplog):
    """§6-1g #0 契约回归：定时器关闭后，下一段的 duration_ms / 切段点只按自身内容。

    旧写法（定时器路径不归零 `_audio_segment_ms`）下，本用例第二段会被上一段的
    59_980ms 顶到立即切段，且 duration_ms 虚高。
    """
    monkeypatch.setattr(session_mod, "_AUDIO_WALL_BACKSTOP_MARGIN_MS", 0)
    caplog.set_level(logging.INFO)
    store = _FakeChatStore()
    s = _make_session(tmp_path, store=store)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        _feed(s)
        await asyncio.sleep(0.02)
        s._audio_segment_ms = 59_980                 # 注入「上一段残留」量级
        await asyncio.sleep(0.12)                    # 定时器关闭该段
        recs = _audio_records(store)
        assert len(recs) == 1 and recs[0]["duration_ms"] == 59_980
        assert s._audio_segment_ms == 0              # 收尾归零

        _feed(s)                                     # 下一段：只含自身 20ms
        await asyncio.sleep(0.02)
        assert s._ogg_writer is not None
        assert s._audio_segment_ms == FRAME_MS
        assert len(_audio_records(store)) == 1       # 未被上一段内容顶到立即切段
    finally:
        await s.finalize()


async def test_publish_only_from_content_path_with_real_duration(tmp_path, caplog):
    """§6-1e/h/k②：publish 只在内容满路径发、duration = 实际内容时长（E6）；墙钟
    抢跑（慢节拍）与 detach/flush 均不发（E8）。"""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(session_mod, "_AUDIO_WALL_BACKSTOP_MARGIN_MS", 0)
    caplog.set_level(logging.INFO)
    store = _FakeChatStore()
    pub = _FakePublisher()
    s = _make_session(tmp_path, store=store, publisher=pub)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        # 慢节拍：内容增长（20ms/60ms）慢于墙钟 → 墙钟先到 → 不 publish
        for _ in range(2):
            _feed(s)
            await asyncio.sleep(0.06)
        await asyncio.sleep(0.06)
        assert "wall_clock" in _close_reasons(caplog)
        assert pub.calls == []

        # 内容满路径：publish 带真实内容时长
        _feed(s, 3)
        await asyncio.sleep(0.02)
        assert len(pub.calls) == 1
        assert pub.calls[0]["duration_ms"] == 3 * FRAME_MS
        assert pub.calls[0]["recording_id"] == "rec-1"

        # detach / flush 不发
        _feed(s, 2)
        await asyncio.sleep(0.02)
        await s.detach()
        await s.finalize()
        assert len(pub.calls) == 1
    finally:
        monkeypatch.undo()
        await s.finalize()


async def test_entry_wall_clock_check_fires_when_timer_was_delayed(tmp_path, monkeypatch, caplog):
    """§4-4：定时器被事件循环延迟时，包路径入口检查兜底（回拨 opened_at 触发）。"""
    monkeypatch.setattr(session_mod, "_AUDIO_WALL_BACKSTOP_MARGIN_MS", 0)
    caplog.set_level(logging.INFO)
    store = _FakeChatStore()
    s = _make_session(tmp_path, store=store)
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        _feed(s)
        await asyncio.sleep(0.02)
        s._audio_segment_opened_at = time.monotonic() - 10 * SEG_S   # 视作早已超时
        s._audio_timer.cancel()                                      # 定时器「没点火」
        _feed(s)
        await asyncio.sleep(0.02)
        assert "wall_clock" in _close_reasons(caplog)
        recs = _audio_records(store)
        assert len(recs) == 1 and recs[0]["duration_ms"] == FRAME_MS
        # 收尾后同一包继续在「新段」写盘，不丢帧
        assert s._ogg_writer is not None and s._audio_segment_index == 2
        assert s._audio_segment_ms == FRAME_MS
    finally:
        await s.finalize()


async def test_timer_delay_uses_seconds_unit(tmp_path, monkeypatch):
    """§6-1k③ 单位断言：段长/余量是 ms，`call_later` 要秒——既有用例抓不到 ×1000 bug。"""
    seen: list[float] = []
    loop = asyncio.get_running_loop()
    real_call_later = loop.call_later

    def spy(delay, callback, *args):
        if getattr(callback, "__name__", "") == "_on_audio_segment_timeout":
            seen.append(delay)          # asyncio.sleep 也走 call_later，按回调过滤
        return real_call_later(delay, callback, *args)

    monkeypatch.setattr(loop, "call_later", spy)
    store = _FakeChatStore()
    s = _make_session(tmp_path, segment_s=30, store=store)   # 默认余量 6_000ms
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        _feed(s)
        await asyncio.sleep(0.02)
        assert seen and seen[0] == pytest.approx(36.0, abs=0.5)
    finally:
        await s.finalize()


async def test_segment_length_config_guards(tmp_path):
    """§3.4：`audio_segment_s` 缺失/非法（<=0、非数、**非有限**）→ 回退默认 60s。

    inf 是崩溃级输入：`int(inf)`（session.json 回执 + 包路径内容阈值）会抛
    OverflowError，前者直接让 start() 失败 ⇒ 整设备录制起不来。
    """
    for bad in ({}, {"audio_segment_s": 0}, {"audio_segment_s": -5}, {"audio_segment_s": "x"},
                {"audio_segment_s": float("inf")}, {"audio_segment_s": float("nan")},
                {"audio_segment_s": 1e309}):
        root = tmp_path / f"bad-{abs(hash(str(bad)))}"
        s = RecordingSession(
            recording_session_id="rec-1", device_uuid="dev-1",
            session_date="2026-09-25", storage_path=root, config=bad)
        assert s._audio_segment_s() == 60.0
        # 崩溃回归：start() + 首包（回执写 int(段长)、内容阈值 int(段长*1000)）不得抛
        await s.start()
        s.on_session_config(audio_sample_rate=16000)
        try:
            _feed(s)
            await asyncio.sleep(0.02)
            assert s._audio_segment_ms == FRAME_MS
            assert s._ogg_writer is not None
        finally:
            await s.finalize()

    # 大而有限的合法值原样透传（运维显式选择「几乎不轮转」）
    big = RecordingSession(
        recording_session_id="rec-1", device_uuid="dev-1",
        session_date="2026-09-25", storage_path=tmp_path / "big",
        config={"audio_segment_s": 1e9})
    assert big._audio_segment_s() == 1e9
