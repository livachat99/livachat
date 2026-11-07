from __future__ import annotations

import logging

from omni.base import OmniProvider, OmniStream
from shared.utils.errors import ErrorCode, ProviderError

logger = logging.getLogger("livachat.omni.service")


class OmniService:
    def __init__(self, active: OmniProvider, fallbacks: list[OmniProvider]) -> None:
        self._providers = [active] + fallbacks

    async def open(self, **kwargs: object) -> tuple[OmniStream, str]:
        last_err: Exception | None = None
        for idx, p in enumerate(self._providers):
            try:
                stream = await p.open(**kwargs)  # type: ignore[arg-type]
                return stream, p.name
            except Exception as exc:
                last_err = exc
                if idx < len(self._providers) - 1:
                    logger.warning(
                        "omni provider open failed, trying next",
                        extra={"provider": p.name, "error": str(exc)},
                    )
        raise ProviderError(
            f"all omni providers failed: {last_err}",
            provider="omni",
            code=ErrorCode.OMNI_UNAVAILABLE,
        )
