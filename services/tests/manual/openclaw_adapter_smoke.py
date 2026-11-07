"""真机冒烟：OpenClawAdapter 对运行中的本机 gateway 走一遍 B 方案链路。

不是单测——把适配器接上真实 gateway（2026.10.3，ws://127.0.0.1:18789），
驱动一次 nudge（真实 LLM turn），若模型按契约回 job_request 则继续观察
job 全链路（会话创建 → chat.send → chat 事件 → JobStore → 投递收口）。

- 只读之外的副作用：会创建 livachat-* 探测会话并运行少量真实 turn（低成本）。
- token 从 ~/.openclaw/openclaw.json 读取；不落盘明文。
Run from services/:  .venv/bin/python3 -m tests.manual.openclaw_adapter_smoke
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sqlite3
import sys
import time
import uuid

from agent_relay.adapters.base import AgentContext, ChatlogLine, ChatlogUpdate
from agent_relay.adapters.openclaw import OpenClawAdapter
from agent_relay.config import AgentRelayConfig, OpenClawAdapterConfig
from agent_relay.delivery import JobResultDeliverer
from agent_relay.job_store_sqlite import SqliteJobStore
from shared.persistence.chat_store import CHAT_STORE_SCHEMA_SQL

DB_PATH = "/tmp/openclaw_smoke.db"


def _load_token() -> str:
    cfg = json.loads((pathlib.Path.home() / ".openclaw" / "openclaw.json").read_text())
    return cfg["gateway"]["auth"]["token"]


class PrintPublisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, msg) -> bool:
        rec = {
            "message_id": msg.message_id,
            "text": msg.text,
            "agent_name": msg.agent_name,
            "agent_id": msg.agent_id,
            "job_key": msg.job_key,
        }
        self.published.append(rec)
        print(f"  PUBLISH {json.dumps(rec, ensure_ascii=False)[:400]}")
        return True


async def main() -> int:
    pathlib.Path(DB_PATH).unlink(missing_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(CHAT_STORE_SCHEMA_SQL)

    cfg = OpenClawAdapterConfig(
        ws_url="ws://127.0.0.1:18789",
        token=_load_token(),
        agent_id="main",
        mode="proactive",
        nickname="",
        nudge_timeout_s=90,
        request_timeout_s=30,
        watchdog_interval_s=60,
        background_timeout_s=900,
    )
    adapter = OpenClawAdapter(cfg)
    adapter.job_store = SqliteJobStore(DB_PATH)
    publisher = PrintPublisher()
    adapter.deliverer = JobResultDeliverer(
        adapter.job_store, publisher, AgentRelayConfig(),
        agent_ids={"openclaw": ""},
    )

    print("== connect ==")
    await adapter.connect()
    await adapter._ensure_ready()
    print("  connected")

    device = f"smoke-dev-{uuid.uuid4().hex[:6]}"   # 每次随机：避免命中旧会话上下文
    ctx = AgentContext(session_id=f"smoke-cs-{uuid.uuid4().hex[:6]}", device_id=device,
                       language="zh", timezone="Asia/Shanghai")
    update = ChatlogUpdate(
        path="/tmp/nonexistent/history.jsonl",   # 探测：路径不存在，契约要求自包含处理
        chat_session_id=ctx.session_id,
        new_lines=[
            ChatlogLine(role="user",
                        text="（冒烟测试）帮我查一下东京今天的天气，用一句话告诉我。",
                        ts_ms=int(time.time() * 1000)),
        ],
    )

    print("== nudge (real turn) ==")
    chunks = []
    async for chunk in adapter.nudge(update, ctx):
        chunks.append(chunk.text)
        print(f"  CHUNK {chunk.text[:200]}")

    store = adapter.job_store
    # 列出该 device 的全部 job（active + 终态），观察 B 方案结果
    jobs = []
    for key in await store.device_job_keys(device):
        rec = await store.get(key)
        if rec is not None:
            jobs.append(rec)
    print(f"== jobs ({len(jobs)}) ==")
    for j in jobs:
        print(f"  job {j.job_key} status={j.status} agent={j.agent_name} "
              f"run={j.hermes_task_id[:40]} result={j.result_text[:80]!r}")

    deadline = time.time() + 120
    while time.time() < deadline:
        jobs = []
        for key in await store.device_job_keys(device):
            rec = await store.get(key)
            if rec is not None:
                jobs.append(rec)
        if jobs and all(j.status in ("done", "delivered", "failed") for j in jobs):
            break
        await asyncio.sleep(2)

    print("== final ==")
    ok = True
    for j in jobs:
        print(f"  job {j.job_key} status={j.status} fail={j.fail_reason or '-'} "
              f"result={j.result_text[:120]!r}")
        if j.status == "failed" and j.fail_reason not in ("timeout",):
            ok = False
    if chunks:
        print(f"  nudge chunks: {chunks}")
    if publisher.published:
        print(f"  published: {json.dumps(publisher.published, ensure_ascii=False)[:400]}")
    else:
        print("  published: (none — 无 job 或无投递)")

    await adapter.close()
    print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
