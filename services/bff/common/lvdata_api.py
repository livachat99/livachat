"""BFF → lvdata-mcp 执行面客户端（docs/bff_service_remove.md §2.2）。

BFF 只做转发与可见性校验；删除的实际执行（T1/T2/文件面）全在 lvdata-mcp
（services 不能 import 其代码——§4.1 方案 A）。Bearer token 仅在服务端内部
使用，不下发给浏览器/APP。
"""

from __future__ import annotations

import httpx

from bff.common.store import BffError


async def remove_events(base_url: str, token: str, timeout_s: float,
                        refs: list[str], *,
                        transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """POST {base_url}/api/events/remove（Bearer）。

    错误映射（§2.1）：url 未配置 → removal_disabled(503)；上游 400 →
    bad_request；连接失败/超时/非 200 → upstream_unavailable(502)。
    `transport` 供测试注入（httpx.MockTransport）。
    """
    if not base_url:
        raise BffError("removal_disabled", "bff.lvdata_api_url 未配置，remove 端点禁用")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            resp = await client.post(f"{base_url.rstrip('/')}/api/events/remove",
                                     json={"event_refs": refs}, headers=headers)
    except httpx.HTTPError as exc:
        raise BffError("upstream_unavailable", f"lvdata-mcp 请求失败: {exc}") from exc
    if resp.status_code == 400:
        raise BffError("bad_request", _error_message(resp))
    if resp.status_code != 200:
        raise BffError("upstream_unavailable", f"lvdata-mcp HTTP {resp.status_code}")
    return resp.json()


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("error", {}).get("message", "invalid request"))
    except ValueError:
        return "invalid request"
