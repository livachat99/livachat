"""一次性生成 day_stats 等价性基线（docs/bff_service_interfaces.md §7.5 P0）。

用法（仓库根）：
    .venv/bin/python tools/lvdata-mcp/tests/fixtures/gen_day_stats_golden.py

产物（本目录，随代码入库）：
- ``day_stats_events.json`` —— 固定 events 夹具（ts_ms 已固化，两侧共用同一份）
- ``day_stats_golden.json`` —— 用**现 BFF 实现**（services/bff/timeline/service.py 的
  ``_derive_hot_slots`` / ``_derive_images`` + counts 口径）产出的期望值

纪律（§7.5-1）：**必须在删除 BFF 派生函数之前运行本脚本**。lvdata-mcp 侧无法 import
BFF 代码，跨包比对只能靠本快照；基线一旦缺失，等价性无从验证。

调用口径 = 新 stage 的口径：``devices=None``（E2-A 合并全设备）、全类型 counts、
日窗 ``day_bounds_ms`` 闭区间。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "services"))

TZ = "Asia/Shanghai"
TZINFO = ZoneInfo(TZ)

D1 = "b53985c7-0000-4000-8000-000000000001"
D2 = "b53985c7-0000-4000-8000-000000000002"
S1 = "019fabe0-3b0f-7947-8000-1e567def787d"
S2 = "019fabe0-3b0f-7947-8000-1e567def787e"

# 事件优先级（与 consts.EVENT_PRIORITY 同源；stub 排序用）
PRIORITY = {"chat": 0, "transcribe": 1, "translate": 2, "window": 3,
            "video": 4, "audio": 5, "agent_job": 6}


def ts(local: str) -> int:
    return int(datetime.strptime(local, "%Y-%m-%d %H:%M:%S.%f")
               .replace(tzinfo=TZINFO).timestamp() * 1000)


# ── 夹具事件（(local_ts, type, device, session, payload, summary)）───────────
# 覆盖：日界两端、60min 切段边界（恰好 60min 不分段 / 65min 分段）、>3 段合并、
# 相等间隔并列取最左、非 hot-slot 类型不参与、window 分组（有/无 window_id、
# 同首帧 ts 按键决胜）、轮转与 ts 升序、无 frame_id / 无 rsession_id 剔除、
# 池上限 15 截断、多设备合并。
SPEC: list[tuple] = []
add = SPEC.append

# Day A 2026-08-17：hot_slots 主场景 + 日界
add(("2026-08-17 00:00:00.000", "chat", D1, S1, {}, "day start"))
add(("2026-08-17 08:00:00.000", "chat", D1, S1, {}, "a1"))
add(("2026-08-17 08:05:00.000", "chat", D1, S1, {}, "a2"))          # 同段
add(("2026-08-17 09:10:00.000", "transcribe", D1, S1, {}, "a3"))    # 65min → 分段
add(("2026-08-17 10:00:00.000", "agent_job", D1, S1, {}, "a4"))     # 50min → 分段
add(("2026-08-17 11:00:00.000", "translate", D1, S1, {}, "a5"))     # 恰好 60min → 不分段
add(("2026-08-17 13:00:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-8fc29ccdab01", "window_id": "wA1"}, "a6"))
add(("2026-08-17 13:30:00.000", "window", D1, S1, {"window_id": "wA1"}, "a7"))  # 不参与
add(("2026-08-17 14:00:00.000", "audio", D1, S1,
     {"segment_id": "segA1", "summary": "环境声"}, "a8"))           # 不参与
add(("2026-08-17 15:00:00.000", "chat", D1, S1, {}, "a9"))
add(("2026-08-17 23:59:59.999", "chat", D1, S1, {}, "day end"))

# Day B 2026-08-18：合并并列取最左 + images 轮转/分组决胜
add(("2026-08-18 09:00:00.000", "chat", D1, S1, {}, "b1"))
add(("2026-08-18 09:30:00.000", "chat", D1, S1, {}, "b2"))
add(("2026-08-18 10:00:00.000", "chat", D1, S1, {}, "b3"))
add(("2026-08-18 10:30:00.000", "chat", D1, S1, {}, "b4"))
add(("2026-08-18 12:00:00.000", "chat", D1, S1, {}, "b5"))
add(("2026-08-18 13:30:00.000", "chat", D1, S1, {}, "b6"))
add(("2026-08-18 09:00:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000010", "window_id": "w1"}, "f10"))
add(("2026-08-18 09:05:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000011", "window_id": "w1"}, "f11"))
add(("2026-08-18 09:10:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000012", "window_id": "w1"}, "f12"))
add(("2026-08-18 09:20:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000020", "window_id": "w2"}, "f20"))
add(("2026-08-18 09:25:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000021", "window_id": "w2"}, "f21"))
add(("2026-08-18 09:40:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000030"}, "f30"))   # 无 window_id → 按 event_ref 分组
add(("2026-08-18 09:45:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000031"}, "f31"))
add(("2026-08-18 09:00:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000040", "window_id": "w3"}, "f40"))  # 与 w1 同首帧 ts
add(("2026-08-18 09:50:00.000", "video", D1, S1, {"window_id": "w1"}, "no frame_id"))  # 剔除
add(("2026-08-18 09:55:00.000", "video", D1, S1,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000050", "window_id": "w2"}, "no rsession"))

# Day C 2026-08-19：多设备合并
add(("2026-08-19 10:00:00.000", "chat", D1, S1, {}, "c1"))
add(("2026-08-19 10:30:00.000", "chat", D2, S2, {}, "c2"))
add(("2026-08-19 11:00:00.000", "video", D2, S2,
     {"frame_id": "019fabe5-b00e-7b8d-8000-000000000060", "window_id": "w1"}, "f60"))

# Day D 2026-08-20：单窗口 18 帧 → 池上限 15 截断
for i in range(18):
    add((f"2026-08-20 10:{i:02d}:00.000", "video", D1, S1,
         {"frame_id": f"019fabe5-b00e-7b8d-8000-0000000001{i:02d}", "window_id": "w9"},
         f"pool{i:02d}"))


def build_events() -> list[dict]:
    out = []
    for i, (local, etype, dev, sess, payload, summary) in enumerate(SPEC):
        event_ref = f"{sess}:{etype}:{i:03d}"
        p = dict(payload)
        if etype == "video" and "frame_id" not in p and "no frame_id" in summary:
            p = {"window_id": "w1"}          # 显式无 frame_id
        out.append({
            "event_ref": event_ref,
            "device_uuid": dev,
            "rsession_id": "" if summary == "no rsession" else sess,
            "type": etype,
            "ts_ms": ts(local),
            "local": local,
            "summary": summary,
            "payload": p,
        })
    return out


class StubStore:
    """镜像 LvdataStore 的出库语义：events_in_range 下推 + `_event_out` 的 payload 扁平化。

    payload 键合并进行字典（列同名以列为准）——`_derive_images` 读
    `e["frame_id"]`/`e["window_id"]` 依赖这一点（store.py:241-257）。
    """

    def __init__(self, events: list[dict]):
        self.events = events

    @staticmethod
    def _out(e: dict) -> dict:
        d = {k: v for k, v in e.items() if k != "payload"}
        for k, v in (e.get("payload") or {}).items():
            if k not in d:
                d[k] = v
        return d

    def events_in_range(self, start_ms, end_ms, types=None, devices=None,
                        limit=None, offset=None):
        rows = [e for e in self.events
                if start_ms <= e["ts_ms"] <= end_ms
                and (types is None or e["type"] in types)]
        if devices is not None:
            rows = [e for e in rows if e["device_uuid"] in devices]
        rows.sort(key=lambda e: (e["ts_ms"], PRIORITY.get(e["type"], 9),
                                 e["_event_id"]))
        return [self._out(e) for e in rows]


def main() -> None:
    from bff.common.schemas import DayImage  # noqa: F401  （结构自证）
    from bff.common.timeutil import day_bounds_ms

    try:
        from bff.timeline.service import _derive_hot_slots, _derive_images
    except ImportError as e:  # 派生已随 §7 删除 → 基线不可重算（产物仍在 fixtures/）
        raise SystemExit(f"本生成器只在 BFF 派生被删除前运行（§7.5-1/§7.8 P0）：{e}")

    events = build_events()
    for i, e in enumerate(events):
        e["_event_id"] = i + 1              # 与测试插入序（event_id 自增）一致
    store = StubStore(events)

    days = sorted({e["local"][:10] for e in events})
    golden = {"timezone": TZ, "devices": None, "days": {}}
    for date in days:
        start, end = day_bounds_ms(date, TZ)
        in_day = [e for e in events if start <= e["ts_ms"] <= end]
        counts: dict[str, int] = {}
        for e in in_day:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        golden["days"][date] = {
            "counts": counts,
            "hot_slots": [s.model_dump() for s in
                          _derive_hot_slots(store, date, TZ, None)],
            "images": [i.model_dump() for i in _derive_images(store, date, TZ, None)],
        }

    clean = [{k: v for k, v in e.items() if k != "_event_id"} for e in events]
    (HERE / "day_stats_events.json").write_text(
        json.dumps(clean, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (HERE / "day_stats_golden.json").write_text(
        json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"events={len(clean)} days={len(days)}")
    for d, g in sorted(golden["days"].items()):
        print(f"  {d}: counts={g['counts']} slots={len(g['hot_slots'])} "
              f"images={len(g['images'])}")


if __name__ == "__main__":
    main()
