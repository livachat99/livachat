from __future__ import annotations

from avdetector.base import AvCheckResult


class FakeAvDetectorStream:
    """假检测流：记录全部调用；check 按预设队列依次返回（默认 NONE）。"""

    def __init__(self, results: list[AvCheckResult] | None = None,
                 raise_on_check: Exception | None = None) -> None:
        self.feeds: list[bytes] = []
        self.images: list[tuple[bytes, int, int]] = []
        self.commits = 0
        self.checks = 0
        self.reopened = 0
        self.closed = False
        self.calls: list[str] = []  # 单管道顺序断言用
        self._results = list(results or [])
        self._raise_on_check = raise_on_check
        self.check_timeouts: list[float] = []

    async def feed_audio(self, pcm: bytes) -> None:
        self.calls.append("audio")
        self.feeds.append(pcm)

    async def commit_audio(self) -> None:
        self.calls.append("commit")
        self.commits += 1

    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None:
        self.calls.append("image")
        self.images.append((jpeg, width, height))

    async def check(self, *, timeout_s: float) -> AvCheckResult:
        self.calls.append("check")
        self.checks += 1
        self.check_timeouts.append(timeout_s)
        if self._raise_on_check is not None:
            raise self._raise_on_check
        if self._results:
            return self._results.pop(0)
        return AvCheckResult(text="NONE")

    async def reopen(self) -> None:
        self.calls.append("reopen")
        self.reopened += 1

    async def close(self) -> None:
        self.closed = True


class FakeAvDetectorProvider:
    name = "fake_avdetector"

    def __init__(self, name: str | None = None, raise_on_open: Exception | None = None,
                 stream: FakeAvDetectorStream | None = None) -> None:
        if name is not None:
            self.name = name
        self.raise_on_open = raise_on_open
        self.stream = stream or FakeAvDetectorStream()
        self.last_open_kwargs: dict | None = None
        self.open_calls = 0

    async def open(self, **kwargs: object) -> FakeAvDetectorStream:
        self.open_calls += 1
        self.last_open_kwargs = dict(kwargs)
        if self.raise_on_open is not None:
            raise self.raise_on_open
        return self.stream
