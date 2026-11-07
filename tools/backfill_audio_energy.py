#!/usr/bin/env python3
"""存量语音段能量回填（docs/android_timeline_audio_gallery.md §10 E2）。

用法（默认 dry-run，不写库）：
    .venv/bin/python tools/backfill_audio_energy.py [--root /opt/lvdata] [--apply] [--force]

要点（文档 E2）：
- 只处理**有事件的段**：静音段按 `is_silent` 不落事件、无卡片，也就无需能量；
- **读改写** payload（只合并 energy 键，不动 transcript/summary/emotions 等既有字段）；
  绝不走 `upsert_event_if_changed`——其 `WHERE excluded.summary != events.summary`
  会在摘要未变时静默跳过整行更新，回填会无声失效；
- 单事务 + `PRAGMA busy_timeout`，写入前建议停 watcher / 低峰执行，避开在线分析写窗口；
- 幂等：已带当前 `energy_v` 的段默认跳过（`--force` 重算覆盖）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lvdata-mcp" / "src"))

from lvdata_mcp.analysis.audio import (  # noqa: E402
    ENERGY_VERSION,
    _decode_pcm_s16le,
    energy_payload,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/opt/lvdata")
    ap.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    ap.add_argument("--force", action="store_true", help="已带能量也重算覆盖")
    args = ap.parse_args()

    root = Path(args.root)
    db = root / "lvdata_inventory.db"
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")

    rows = conn.execute(
        "SELECT event_ref, device_uuid, rsession_id, payload FROM events "
        "WHERE type = 'audio' ORDER BY ts_ms").fetchall()
    print(f"audio 事件 {len(rows)} 条（root={root}，apply={args.apply}）")

    updates: list[tuple[str, str]] = []
    skipped = missing = failed = 0
    total_bytes = 0
    for r in rows:
        payload = json.loads(r["payload"] or "{}")
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("energy_v") == ENERGY_VERSION and not args.force:
            skipped += 1
            continue
        seg = payload.get("segment_id")
        ogg = root / r["device_uuid"] / r["rsession_id"] / "audio" / f"{seg}.ogg"
        if not seg or not ogg.is_file():
            missing += 1
            print(f"  跳过（源文件缺失）: {r['event_ref']}")
            continue
        t0 = time.perf_counter()
        try:
            samples, rate = _decode_pcm_s16le(str(ogg))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  失败（解码）: {r['event_ref']} — {type(e).__name__}: {e}")
            continue
        energy = energy_payload(samples, rate)
        if not energy:
            failed += 1
            print(f"  失败（无帧）: {r['event_ref']}")
            continue
        total_bytes += len(energy["energy"])
        ms = (time.perf_counter() - t0) * 1000
        print(f"  {r['event_ref']}  帧={len(energy['energy']) * 3 // 4}  "
              f"base64={len(energy['energy'])}B  解码+计算={ms:.0f}ms")
        updates.append((json.dumps({**payload, **energy}, ensure_ascii=False),
                        r["event_ref"]))

    print(f"待更新 {len(updates)} 段（跳过已带能量 {skipped} / 源缺失 {missing} / 失败 {failed}）；"
          f"base64 合计 {total_bytes} B")
    if not args.apply:
        print("dry-run：未写库（加 --apply 才写入）")
        return 0
    if not updates:
        print("无待更新项")
        return 0
    with conn:  # 单事务
        conn.executemany("UPDATE events SET payload = ? WHERE event_ref = ?", updates)
    print(f"已写入 {len(updates)} 段")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
