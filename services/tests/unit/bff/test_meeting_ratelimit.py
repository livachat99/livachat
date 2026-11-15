"""join_meeting 每 IP 限流（docs/livachat_meeting.md §6.2）。"""

from __future__ import annotations

from bff.meeting.ratelimit import PerIpRateLimiter


def test_window_allows_limit_then_blocks():
    limiter = PerIpRateLimiter(limit=2)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False
    # 其他 IP 不受影响
    assert limiter.allow("5.6.7.8") is True


def test_state_is_bounded_by_max_ips():
    limiter = PerIpRateLimiter(limit=1, max_ips=2)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("c") is True
    assert len(limiter._hits) == 2
    # 最早插入的 bucket 被淘汰；重新出现时按新 bucket 计数。
    assert limiter.allow("a") is True
