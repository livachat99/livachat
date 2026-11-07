"""remove service：可见性/类型预检 → 转发 lvdata-mcp 执行面（docs/bff_service_remove.md §2.1）。

部分成功语义（D7）：ref 级失败逐条 skip，不阻塞批内其余；请求格式非法由
`RemoveRequest` 在路由层拦为 400（唯一整批拒绝条件）。删除的实际执行
（T1/T2/文件面）全在 lvdata-mcp——BFF 保持只读（§4.1 方案 A）。
"""

from __future__ import annotations

import logging

from bff.common import lvdata_api
from bff.common.deps import Principal
from bff.common.store import LvdataStore

log = logging.getLogger(__name__)


async def build_remove(store: LvdataStore, principal: Principal, bff_cfg,
                       refs: list[str]) -> dict:
    """逐条分派：不存在/不可见 → not_found；非 video → not_video；其余转执行面。

    可见设备集 = `list_sessions(device_id)` 的 device_uuid 集合（白名单 ∩ 请求
    device；空集 = 无可见设备）——镜像 timeline 的设备交集语义。
    """
    # 去重（保序）：重复 ref 只分派一次，响应中每个 ref 至多出现一次（§2.1）
    uniq: list[str] = []
    for ref in refs:
        if ref not in uniq:
            uniq.append(ref)
    refs = uniq

    visible = {s["device_uuid"] for s in store.list_sessions(principal.device_id)}
    rows = {r["event_ref"]: r for r in store.events_by_refs(refs)}
    forward: list[str] = []
    skipped: list[dict] = []
    for ref in refs:
        row = rows.get(ref)
        if row is None or row["device_uuid"] not in visible:
            # 不存在与不可见合并同码，不泄漏存在性（§2.1）
            skipped.append({"event_ref": ref, "reason": "not_found"})
        elif row["type"] != "video":
            skipped.append({"event_ref": ref, "reason": "not_video"})
        else:
            forward.append(ref)

    # 基础字段恒在（含全 skip 的 removed=[] 形态，§2.1）
    result: dict = {"removed": [], "window_events_removed": [],
                    "images_removed": [], "images_pending": [],
                    "days_recomputed": []}
    upstream_skipped: list[dict] = []
    if forward:
        upstream = await lvdata_api.remove_events(
            bff_cfg.lvdata_api_url, bff_cfg.lvdata_api_token,
            bff_cfg.lvdata_api_timeout_s, forward)
        upstream_skipped = upstream.pop("skipped", [])
        result.update(upstream)
    # else：全 skip 不调执行面（§2.1-3）

    merged_skipped = skipped + upstream_skipped
    _log_skipped(merged_skipped)
    return {"schema_version": 1, **result, "skipped": merged_skipped}


def _log_skipped(skipped: list[dict]) -> None:
    if not skipped:
        return
    reasons: dict[str, int] = {}
    for s in skipped:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
    log.warning("remove skipped %d ref(s): %s, refs=%s", len(skipped), reasons,
                [s["event_ref"] for s in skipped[:5]])
