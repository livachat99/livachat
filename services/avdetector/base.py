"""avdetector 抽象层（docs/fd_pipeline_avdetector.md §4 D1）。

与 omni 的事件流抽象不同，检测器是「查询-应答」语义：一次 check() = 一轮
response.create（manual 模式），返回该窗口的检测结果；上行常年持续（音频 append、
静默 commit、检查时刻补图）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from shared.utils.errors import ProviderError


class AvCheckTimeout(ProviderError):
    """检测查询超时（区别于连接/服务错误，D5 口径：只丢结果、不计熔断失败）。"""


@dataclass
class AvCheckResult:
    """一次检测查询的结果（response.create 一轮）。"""

    text: str                      # 响应全文（"NONE" 或一行 JSON）
    transcripts: list[str] = field(default_factory=list)
    # transcripts：本次查询窗口（自上次提交以来的音频）的输入转写——
    # 说话窗口的否决信号（说话时非空，非语音窗口为空串/缺省，§3.2 探针 7）
    usage: dict | None = None


class AvDetectorStream(Protocol):
    async def feed_audio(self, pcm: bytes) -> None: ...

    async def commit_audio(self) -> None:
        """静默提交当前音频窗口（窗口纪律，D5 ★）；内部等待该窗口转写结算后返回。"""
        ...

    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None: ...

    async def check(self, *, timeout_s: float) -> AvCheckResult:
        """发一次 response.create 并收满本轮（manual 模式；查询串行由上游保证）。"""
        ...

    async def reopen(self) -> None:
        """周期重建 / 错误恢复：新 WS + 重握手（检测器无对话状态需保留）。"""
        ...

    async def close(self) -> None: ...


class AvDetectorProvider(Protocol):
    name: str

    async def open(
        self,
        *,
        sample_rate: int,
        system_prompt: str = "",
        generation_opts: dict[str, Any] | None = None,
    ) -> AvDetectorStream: ...
