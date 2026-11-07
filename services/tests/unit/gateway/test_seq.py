from __future__ import annotations

import asyncio

import pytest

from gateway.seq import InboundSeqGuard, OutboundSeqAllocator


class TestInboundSeqGuard:
    def test_accepts_monotonic(self):
        g = InboundSeqGuard()
        assert g.check(1) is True
        assert g.check(2) is True
        assert g.check(5) is True

    def test_rejects_regression(self):
        g = InboundSeqGuard()
        g.check(10)
        assert g.check(5) is False

    def test_accepts_equal(self):
        g = InboundSeqGuard()
        g.check(3)
        # equal is not a regression (but might be a duplicate)
        assert g.check(3) is True

    def test_tolerates_video_regression(self):
        # video 取号在客户端闸口外，推流期回退属常态：记录日志但继续处理
        g = InboundSeqGuard()
        g.check(10, msg_type="video")
        assert g.check(5, msg_type="video") is True
        assert g.check(9, msg_type="video") is True
        assert g.check(11, msg_type="audio") is True

    def test_video_tolerance_does_not_lower_watermark(self):
        g = InboundSeqGuard()
        g.check(10, msg_type="video")
        g.check(5, msg_type="video")
        # 水位若被容忍路径拉低到 5，6 会被放行——必须仍按 10 判回退
        assert g.check(6, msg_type="audio") is False
        assert g.check(11, msg_type="audio") is True

    def test_tolerated_regression_not_counted_as_error(self, monkeypatch):
        counted: list[int] = []
        monkeypatch.setattr("gateway.seq._inc_seq_error_metric", lambda: counted.append(1))
        g = InboundSeqGuard()
        g.check(10, msg_type="video")
        assert g.check(5, msg_type="video") is True
        assert counted == []
        assert g.check(4, msg_type="audio") is False
        assert counted == [1]


class TestOutboundSeqAllocator:
    @pytest.mark.asyncio
    async def test_monotonic_sequence(self):
        alloc = OutboundSeqAllocator()
        assert await alloc.next() == 1
        assert await alloc.next() == 2
        assert await alloc.next() == 3

    @pytest.mark.asyncio
    async def test_concurrent_no_duplicates(self):
        alloc = OutboundSeqAllocator()

        async def take(n: int) -> set[int]:
            return {await alloc.next() for _ in range(n)}

        results = await asyncio.gather(take(50), take(50), take(50))
        all_vals = set().union(*results)
        assert len(all_vals) == 150
        assert min(all_vals) == 1
        assert max(all_vals) == 150
