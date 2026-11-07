from __future__ import annotations

import logging

from avdetector.base import AvDetectorProvider, AvDetectorStream
from shared.utils.errors import ErrorCode, ProviderError

logger = logging.getLogger("livachat.avdetector.service")


class AvDetectorService:
    def __init__(self, active: AvDetectorProvider, fallbacks: list[AvDetectorProvider]) -> None:
        self._providers = [active] + fallbacks

    async def open(self, **kwargs: object) -> tuple[AvDetectorStream, str]:
        last_err: Exception | None = None
        for idx, p in enumerate(self._providers):
            try:
                stream = await p.open(**kwargs)  # type: ignore[arg-type]
                return stream, p.name
            except Exception as exc:
                last_err = exc
                if idx < len(self._providers) - 1:
                    logger.warning(
                        "avdetector provider open failed, trying next",
                        extra={"provider": p.name, "error": str(exc)},
                    )
        raise ProviderError(
            f"all avdetector providers failed: {last_err}",
            provider="avdetector",
            code=ErrorCode.AVDETECTOR_UNAVAILABLE,
        )
