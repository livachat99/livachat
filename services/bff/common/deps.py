from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Query, Request

from bff.common.store import BffError, LvdataStore
from shared.timeutil import canonicalize_timezone
from shared.utils.errors import AuthError

log = logging.getLogger(__name__)


@dataclass
class Principal:
    user_id: str
    device_id: str | None = None


@dataclass
class AgentRelayControl:
    """In-process relay handles exposed on app.state (docs/android_agent_job_status.md §7.2)."""

    job_store: Any
    adapters: dict[str, Any]


def get_agent_relay(request: Request) -> AgentRelayControl:
    job_store = getattr(request.app.state, "job_store", None)
    adapters = getattr(request.app.state, "agent_adapters", None)
    if job_store is None or not adapters:
        raise BffError("agent_relay_unavailable", "agent relay is not available")
    return AgentRelayControl(job_store=job_store, adapters=adapters)


def get_store(request: Request) -> LvdataStore:
    store = getattr(request.app.state, "lvdata_store", None)
    if store is None:
        store = request.app.state.lvdata_store = _build_store(request)
    return store


def get_timezone(request: Request) -> str:
    return request.app.state.config.bff.timezone


def _build_store(request: Request) -> LvdataStore:
    from bff.common.store import LvdataStore
    from shared.config.models import AppConfig

    cfg: AppConfig = request.app.state.config
    return LvdataStore.from_config(cfg.bff, cfg.recording)


async def get_principal(
    request: Request,
    token: str = Query(default=""),
    device_id: str | None = Query(default=None),
) -> Principal:
    """Phase 2 已落地：token 走 gateway Authenticator 校验（与 WS 握手同语义，
    失败 401 `unauthorized`）；device_id 仍不校验，仅作请求级过滤（∩ 白名单）。
    """
    authenticator = request.app.state.authenticator
    try:
        authenticator.verify(token, device_id or "")
    except AuthError:
        raise BffError("unauthorized", "authentication failed")
    return Principal(user_id="dev-user", device_id=device_id)


def validate_tz(timezone: str | None, default: str) -> str:
    """（内部调用语义参考）仅校验 IANA 名；service 侧已由 resolve_request_tz 取代。"""
    from zoneinfo import ZoneInfo

    tz = timezone or default
    try:
        ZoneInfo(tz)
    except Exception as exc:
        raise BffError("bad_request", f"invalid timezone: {tz!r}") from exc
    return tz


def resolve_request_tz(store: LvdataStore, timezone: str | None, default: str) -> str:
    """请求时区 → 生效时区（timezone-support.md §7.1）。

    - 未传/空串 → 默认时区（现状：validate_tz 的 `timezone or default` 语义）
    - 非法 → 400（维持现状口径）
    - 合法但不在 timezones 表 → 回退默认（G3）；表缺失/空表 → 放行请求时区
    """
    if not timezone:
        return default
    tz = canonicalize_timezone(timezone)
    if tz is None:
        raise BffError("bad_request", f"invalid timezone: {timezone!r}")
    if tz == default:
        return default  # 短路：默认时区无需查表
    known = store.timezone_known(tz)
    if known is False:
        log.debug("timezone %r not in timezones table, fallback to %r", tz, default)
        return default
    return tz
