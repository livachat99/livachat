"""每 IP 滑动窗口限流（公开 join_meeting 端点的滥用收敛）。

实例挂在 app.state（不要模块全局），避免测试/多 app 之间状态泄漏。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class PerIpRateLimiter:
    def __init__(self, limit: int, window_s: float = 60.0, max_ips: int = 10_000):
        self.limit = limit
        self.window_s = window_s
        self.max_ips = max_ips
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        if ip not in self._hits and len(self._hits) >= self.max_ips:
            # 公开端点：不因大量来源 IP 让状态无界增长；淘汰最早插入的 bucket。
            self._hits.pop(next(iter(self._hits)))
        hits = self._hits[ip]
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True
