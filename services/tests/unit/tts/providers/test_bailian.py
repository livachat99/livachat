from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest
import websockets
from websockets.exceptions import ConnectionClosed

from shared.utils.errors import ProviderError
from tts.providers import alibaba_bailian as bailian_mod
from tts.providers.alibaba_bailian import AlibabaBailianTTSProvider, _BailianStream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _provider(port: int, *, workspace_id: str | None = None) -> AlibabaBailianTTSProvider:
    return AlibabaBailianTTSProvider(
        base_url=f"ws://127.0.0.1:{port}",
        api_key="k",
        model="m",
        voice="v",
        workspace_id=workspace_id,
    )


def _event(task_id: str, name: str, **header_extra) -> str:
    """服务端事件消息（task-started/task-finished/task-failed...）。"""
    header = {"event": name, "task_id": task_id}
    header.update(header_extra)
    return json.dumps({"header": header})


def _close_tolerant(handler):
    """服务端 handler 包装：客户端先关连接导致的 recv/send 异常属预期。

    只吞 ConnectionClosed——handler 内的其他异常（含 AssertionError 误写）仍会
    冒泡，避免掩盖问题。"""
    async def wrapped(ws):
        try:
            await handler(ws)
        except ConnectionClosed:
            pass
    return wrapped


async def _full_round(ws, out: dict, audio: bytes = b"\x00\x01\x02\x03") -> str:
    """完整一轮 duplex：run-task → started → continue-task → audio → finish →
    finished，返回 task_id 并记录事件。"""
    m = json.loads(await ws.recv())
    assert m["header"]["action"] == "run-task"
    tid = m["header"]["task_id"]
    out.setdefault("run_payloads", []).append(m)
    out.setdefault("events", []).append(("run", tid))
    await ws.send(_event(tid, "task-started"))
    m = json.loads(await ws.recv())
    assert m["header"]["action"] == "continue-task"
    assert m["header"]["task_id"] == tid
    out["events"].append(("continue", tid))
    await ws.send(audio)
    m = json.loads(await ws.recv())
    assert m["header"]["action"] == "finish-task"
    assert m["header"]["task_id"] == tid
    out["events"].append(("finish", tid))
    await ws.send(_event(tid, "task-finished"))
    return tid


class TestBailianTTSProvider:
    async def test_open_and_start_message(self):
        """完整一轮：协议格式（run-task payload）+ feed→flush→audio 顺序。"""
        out: dict = {}

        async def handler(ws):
            await _full_round(ws, out)

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="longanyang", sample_rate=24000)
            await stream.feed_text("Hello")
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\x00\x01\x02\x03"]
            await stream.close()

        run = out["run_payloads"][0]
        assert run["payload"]["task"] == "tts"
        assert run["payload"]["function"] == "SpeechSynthesizer"
        assert run["payload"]["parameters"]["voice"] == "longanyang"
        assert run["payload"]["parameters"]["sample_rate"] == 24000
        tid = out["events"][0][1]
        assert out["events"] == [("run", tid), ("continue", tid), ("finish", tid)]

    async def test_close_timeout_bounded(self):
        """close 握手限时 1s（与 ASR/livetranslate 一致）：端点不回 close 帧时
        不再阻塞模式切换/收尾（见 docs/translate_lecture_improvements.md）。"""
        out: dict = {}

        async def handler(ws):
            await _full_round(ws, out)

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="longanyang", sample_rate=24000)

            assert stream._ws is not None
            assert stream._ws.close_timeout == 1
            await stream.close()

    async def test_open_and_reset_send_no_run_task(self):
        """回归锁：任务随文本而生——open() 与 reset() 不得发 run-task（空任务
        空转 23s 被服务端杀死的两条历史自爆路径）；flush() 在无 active 任务时
        no-op，不得发 finish-task（§3.3 后半句）。"""
        out: dict = {"events": []}

        async def handler(ws):
            try:
                while True:
                    m = json.loads(await ws.recv())
                    out["events"].append(m["header"]["action"])
                    if m["header"]["action"] == "run-task":
                        await ws.send(_event(m["header"]["task_id"], "task-started"))
                    elif m["header"]["action"] == "continue-task":
                        await ws.send(b"\x00\x01\x02\x03")
                    elif m["header"]["action"] == "finish-task":
                        await ws.send(_event(m["header"]["task_id"], "task-finished"))
            except ConnectionClosed:
                pass

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            await asyncio.sleep(0.4)  # open 后无消息窗口
            assert out["events"] == []
            await stream.flush()  # 无 active 任务 → no-op，不得发 finish-task
            assert out["events"] == []

            await stream.feed_text("hi")
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\x00\x01\x02\x03"]
            assert out["events"] == ["run-task", "continue-task", "finish-task"]

            await stream.reset()
            await stream.reset()  # reset 幂等
            await asyncio.sleep(0.4)  # reset 后无消息窗口
            assert out["events"] == ["run-task", "continue-task", "finish-task"]
            await stream.close()

    async def test_multi_round_same_connection(self):
        """同 WS 顺序两轮：新 task_id、连接不重建、轮末旧任务事件被丢弃。"""
        out: dict = {"events": []}

        async def handler(ws):
            # round 1
            m = json.loads(await ws.recv())
            out["events"].append(("run", m["header"]["task_id"]))
            tid1 = m["header"]["task_id"]
            await ws.send(_event(tid1, "task-started"))
            m = json.loads(await ws.recv())
            out["events"].append(("continue", m["header"]["task_id"]))
            await ws.send(b"R1")
            m = json.loads(await ws.recv())
            out["events"].append(("finish", m["header"]["task_id"]))
            await ws.send(_event(tid1, "task-finished"))
            # 旧任务迟到的 task-failed（客户端已 reset/新建任务）——必须被丢弃，
            # 不得放哨兵截断新一轮 audio，也不得产生 error 日志
            await ws.send(_event(tid1, "task-failed", error_message="late boom"))
            # round 2
            m = json.loads(await ws.recv())
            out["events"].append(("run", m["header"]["task_id"]))
            tid2 = m["header"]["task_id"]
            await ws.send(_event(tid2, "task-started"))
            m = json.loads(await ws.recv())
            out["events"].append(("continue", m["header"]["task_id"]))
            await ws.send(b"R2")
            m = json.loads(await ws.recv())
            out["events"].append(("finish", m["header"]["task_id"]))
            await ws.send(_event(tid2, "task-finished"))

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.feed_text("one")
            await stream.flush()
            assert [c async for c in stream.audio()] == [b"R1"]
            await stream.reset()
            await stream.feed_text("two")
            await stream.flush()
            assert [c async for c in stream.audio()] == [b"R2"]
            await stream.close()

        t1, t2 = out["events"][0][1], out["events"][3][1]
        assert t1 != t2  # 第二轮新 task_id
        assert out["events"] == [("run", t1), ("continue", t1), ("finish", t1),
                                 ("run", t2), ("continue", t2), ("finish", t2)]

    async def test_reset_during_synthesis_finish_once_then_clean_round(self, caplog):
        """interrupt 在合成中（flush 前）：reset 补发一次 finish-task 中止合成；
        残留/错位二进制帧（无 active 时到达）全部丢弃——新一轮音频纯净（M2）。"""
        out: dict = {"events": []}

        async def handler(ws):
            # round 1：合成中被打断
            m = json.loads(await ws.recv())
            out["events"].append(("run", m["header"]["task_id"]))
            tid1 = m["header"]["task_id"]
            await ws.send(_event(tid1, "task-started"))
            m = json.loads(await ws.recv())
            out["events"].append(("continue", m["header"]["task_id"]))
            await ws.send(b"A1")  # 在途音频，随后被打断
            m = json.loads(await ws.recv())
            out["events"].append(("finish", m["header"]["task_id"]))  # reset 补发
            await ws.send(_event(tid1, "task-finished"))
            # round 2：新 run-task 已发、task-started 未回——此时到达的二进制帧
            # 必属旧任务残留/错位帧，按 I1 丢弃
            m = json.loads(await ws.recv())
            out["events"].append(("run", m["header"]["task_id"]))
            tid2 = m["header"]["task_id"]
            await ws.send(b"STALE")
            await ws.send(_event(tid2, "task-started"))
            m = json.loads(await ws.recv())
            out["events"].append(("continue", m["header"]["task_id"]))
            await ws.send(b"B1")
            m = json.loads(await ws.recv())
            out["events"].append(("finish", m["header"]["task_id"]))
            await ws.send(_event(tid2, "task-finished"))

        with caplog.at_level(logging.ERROR, logger="livachat.tts.bailian"):
            async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                provider = _provider(port)
                stream = await provider.open(voice="v", sample_rate=24000)
                await stream.feed_text("one")  # 返回后立即中断（合成在途）
                await stream.reset()           # 应补发 finish-task(id1)
                await stream.reset()           # reset 双调：不得再发第二次 finish
                await stream.feed_text("two")
                await stream.flush()
                chunks = [c async for c in stream.audio()]
                assert chunks == [b"B1"]       # A1（drain）+ STALE（I1）均未混入
                await stream.close()

        t1, t2 = out["events"][0][1], out["events"][3][1]
        assert t1 != t2
        assert out["events"] == [("run", t1), ("continue", t1), ("finish", t1),
                                 ("run", t2), ("continue", t2), ("finish", t2)]
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)

    async def test_flush_then_reset_sends_finish_once(self):
        """M4：flush 已发 finish-task 后 interrupt 的 reset 不得对同一 task 双发。"""
        out: dict = {"events": []}

        async def handler(ws):
            m = json.loads(await ws.recv())
            out["events"].append(("run", m["header"]["task_id"]))
            tid = m["header"]["task_id"]
            await ws.send(_event(tid, "task-started"))
            m = json.loads(await ws.recv())
            out["events"].append(("continue", m["header"]["task_id"]))
            await ws.send(b"P1")
            m = json.loads(await ws.recv())
            out["events"].append(("finish", m["header"]["task_id"]))  # 客户端 flush
            # 不立即回 task-finished（服务端合成收尾慢）；探测窗口内若客户端再发
            # finish-task/run-task（双发 bug）即记录为 unexpected
            try:
                extra = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
                out["events"].append(("unexpected", extra["header"]["action"]))
            except TimeoutError:
                pass
            await ws.send(_event(tid, "task-finished"))
            try:
                await ws.recv()  # 等待客户端 close
            except ConnectionClosed:
                pass

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.feed_text("x")
            await stream.flush()      # finish-task 已发出（_finish_sent 置位）
            await stream.reset()      # active 仍在（finished 未回）→ 不得补发
            await asyncio.sleep(0.7)  # 探测窗口
            await stream.close()

        assert out["events"] == [
            ("run", out["events"][0][1]),
            ("continue", out["events"][0][1]),
            ("finish", out["events"][0][1]),
        ]

    async def test_task_start_timeout_reconnects_then_succeeds(self, monkeypatch):
        """run-task 后 5s 无 task-started（ack 丢失）→ 重连后整段重试一次成功。"""
        monkeypatch.setattr(bailian_mod, "_TASK_START_TIMEOUT_S", 0.2)
        conn_count = {"n": 0}
        out: dict = {"events": []}

        async def handler(ws):
            conn_count["n"] += 1
            if conn_count["n"] == 1:
                m = json.loads(await ws.recv())
                out["events"].append(("run-stalled", m["header"]["task_id"]))
                try:
                    await ws.recv()  # 不回 started，等客户端重连关闭
                except ConnectionClosed:
                    pass
            else:
                await _full_round(ws, out)

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.feed_text("hi")  # conn1 超时 → 重连 conn2 → 重试成功
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\x00\x01\x02\x03"]
            await stream.close()

        assert conn_count["n"] == 2
        assert len(out["events"]) == 4  # run-stalled + 完整一轮 3 事件

    async def test_task_start_timeout_retry_fails_raises(self, monkeypatch):
        """连续两次 started 超时 → ProviderError（编排层 text-only 兜底入口）。"""
        monkeypatch.setattr(bailian_mod, "_TASK_START_TIMEOUT_S", 0.2)
        conn_count = {"n": 0}

        async def handler(ws):
            conn_count["n"] += 1
            try:
                await ws.recv()  # 回 started，挂住等客户端重连/关闭
            except ConnectionClosed:
                pass

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            with pytest.raises(ProviderError, match="task start failed"):
                await stream.feed_text("hi")
            await stream.close()

        assert conn_count["n"] == 2  # 首次 + 重试一次，不无限重连

    async def test_connection_dies_while_waiting_started_fails_fast(self):
        """I4：feed 等 task-started 期间连接死亡（任务不可能再确认）→ 立即唤醒
        走重连重试——不得白等满 5s 超时。"""
        conn_count = {"n": 0}
        out: dict = {"events": []}
        t0 = time.monotonic()

        async def handler(ws):
            conn_count["n"] += 1
            if conn_count["n"] == 1:
                m = json.loads(await ws.recv())
                out["events"].append(("run-stalled", m["header"]["task_id"]))
                await asyncio.sleep(0.1)
                await ws.close()  # 服务端在任务确认前断连
            else:
                await _full_round(ws, out)

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.feed_text("hi")  # conn1 中断 → 立即失败 → 重连 conn2 重试
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\x00\x01\x02\x03"]
            await stream.close()

        assert conn_count["n"] == 2
        assert time.monotonic() - t0 < 2.5  # 未被拖入 5s started 超时

    async def test_interrupt_cancels_feed_wait_then_next_round_clean(self):
        """编排层 interrupt 落在 feed 的 started 等待中（cancel pipeline → finally
        reset + interrupt handler reset 双调）：孤儿 run-task 的迟到 task-started
        不得误唤醒/污染下一轮（M1 的 cancel 形态）；reset 双调幂等。"""
        out: dict = {"events": []}

        async def handler(ws):
            # 轮 1（孤儿）：收到 run-task 但永不回 started
            m = json.loads(await ws.recv())
            out["events"].append(("run", m["header"]["task_id"]))
            tid1 = m["header"]["task_id"]
            # 轮 2：先放孤儿任务迟到的 started（此刻新 run-task 已发、pending2 在
            # 等）——必须按 I2 丢弃；再确认新任务
            m = json.loads(await ws.recv())
            out["events"].append(("run", m["header"]["task_id"]))
            tid2 = m["header"]["task_id"]
            await ws.send(_event(tid1, "task-started"))  # 孤儿，应被丢弃
            await ws.send(_event(tid2, "task-started"))
            m = json.loads(await ws.recv())
            out["events"].append(("continue", m["header"]["task_id"]))
            await ws.send(b"R2")
            m = json.loads(await ws.recv())
            out["events"].append(("finish", m["header"]["task_id"]))
            await ws.send(_event(tid2, "task-finished"))

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            feed1 = asyncio.create_task(stream.feed_text("one"))
            await asyncio.sleep(0.15)  # 让 run-task1 发出、feed 进入 started 等待
            feed1.cancel()  # 模拟 _cancel_pipeline
            with pytest.raises(asyncio.CancelledError):
                await feed1
            await stream.reset()  # pipeline finally 的 reset
            await stream.reset()  # interrupt handler 的第二次 reset（双调幂等）
            await stream.feed_text("two")
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"R2"]  # 未被孤儿事件/残留哨兵截断或污染
            await stream.close()

        t1, t2 = out["events"][0][1], out["events"][1][1]
        assert t1 != t2
        assert out["events"] == [("run", t1), ("run", t2), ("continue", t2),
                                 ("finish", t2)]

    async def test_task_failed_on_start_retries_on_fresh_connection(self):
        """M3：run-task 被拒（task-failed 顶替 task-started）→ 立即唤醒（远小于
        5s 超时）→ 重连重试一次成功。"""
        conn_count = {"n": 0}
        out: dict = {"events": []}
        t0 = time.monotonic()

        async def handler(ws):
            conn_count["n"] += 1
            if conn_count["n"] == 1:
                m = json.loads(await ws.recv())
                await ws.send(_event(m["header"]["task_id"], "task-failed",
                                     error_message="quota exceeded"))
                try:
                    await ws.recv()
                except ConnectionClosed:
                    pass
            else:
                await _full_round(ws, out)

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.feed_text("hi")
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\x00\x01\x02\x03"]
            await stream.close()

        assert conn_count["n"] == 2
        assert time.monotonic() - t0 < 3.0  # 失败点立即暴露，未拖满 5s 超时

    async def test_task_failed_on_start_twice_raises_with_message(self):
        """M3：重试仍被拒 → ProviderError 透传服务端 error_message，且快速失败。"""
        t0 = time.monotonic()

        async def handler(ws):
            m = json.loads(await ws.recv())
            await ws.send(_event(m["header"]["task_id"], "task-failed",
                                 error_message="model unavailable"))
            try:
                await ws.recv()
            except ConnectionClosed:
                pass

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            with pytest.raises(ProviderError, match="model unavailable"):
                await stream.feed_text("hi")
            await stream.close()

        assert time.monotonic() - t0 < 3.0  # 快速失败路径（非 5s 等待）

    async def test_pending_ignores_stale_task_started(self):
        """M1：pending 期间收到旧 task_id 的 task-started 不得误唤醒新任务的就绪
        等待——feed 仍等到新任务 started 才返回。"""
        out: dict = {"events": []}

        async def handler(ws):
            m = json.loads(await ws.recv())
            out["events"].append(("run", m["header"]["task_id"]))
            tid = m["header"]["task_id"]
            await ws.send(_event("STALE-OLD", "task-started"))  # 孤儿任务迟到 started
            try:
                premature = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3))
                out["events"].append(("premature-continue", premature["header"]["action"]))
            except TimeoutError:
                pass  # 正确：feed 未被唤醒，仍在等待本任务 started
            await ws.send(_event(tid, "task-started"))
            m = json.loads(await ws.recv())
            out["events"].append(("continue", m["header"]["task_id"]))
            await ws.send(b"X1")
            m = json.loads(await ws.recv())
            out["events"].append(("finish", m["header"]["task_id"]))
            await ws.send(_event(tid, "task-finished"))

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            t0 = time.monotonic()
            await stream.feed_text("hi")
            assert time.monotonic() - t0 >= 0.25  # 未被 STALE started 提前唤醒
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"X1"]
            await stream.close()

        assert all(e[0] != "premature-continue" for e in out["events"])

    async def test_receiver_dead_ws_alive_feed_restarts_receiver(self):
        """receiver 死而 WS 活（理论路径）：feed 原地重启 receiver，不重连。"""
        conn_count = {"n": 0}
        out: dict = {"events": []}

        async def handler(ws):
            conn_count["n"] += 1
            await _full_round(ws, out)

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream._cancel_receiver()  # 模拟 receiver 被外部杀掉
            await stream.feed_text("hi")
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\x00\x01\x02\x03"]
            await stream.close()

        assert conn_count["n"] == 1  # 未重连

    # ------------------------------------------------------------------
    # 原回归锁（语义保持/改造）
    # ------------------------------------------------------------------
    async def test_reset_reconnects_after_server_close(self):
        """会话级流被服务端（无任务 idle）断开后, reset 必须透明重连, 后续轮次
        仍能合成。服务端断连不依赖任何 run-task（open 不发任务）。"""
        conn_count = {"n": 0}

        async def handler(ws):
            conn_count["n"] += 1
            if conn_count["n"] == 1:
                await ws.close()  # 服务端干净关闭空闲 WS
            else:
                await _full_round(ws, out)

        out: dict = {"events": []}
        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            for _ in range(100):
                if stream._ws is None:  # receiver 检测到连接死亡（I4 置空）
                    break
                await asyncio.sleep(0.02)
            assert stream._ws is None

            await stream.reset()
            assert conn_count["n"] == 2  # reconnected onto a fresh connection

            await stream.feed_text("Hello")
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\x00\x01\x02\x03"]

            await stream.close()

    async def test_feed_text_reconnects_after_server_close(self, caplog):
        """编排层每轮首个触点是 feed_text（reset 在轮末 finally）——断开后
        feed_text 必须能自愈（入口重连），否则首轮即静默无音频。此处以异常关闭
        （1001）模拟真实空闲断连。"""
        conn_count = {"n": 0}
        out: dict = {"events": []}

        async def handler(ws):
            conn_count["n"] += 1
            if conn_count["n"] == 1:
                await ws.close(code=1001, reason="idle timeout")
            else:
                await _full_round(ws, out)

        with caplog.at_level(logging.CRITICAL, logger="livachat.tts.bailian"):
            async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                provider = _provider(port)
                stream = await provider.open(voice="v", sample_rate=24000)
                for _ in range(100):
                    if stream._ws is None:
                        break
                    await asyncio.sleep(0.02)
                assert stream._ws is None

                await stream.feed_text("Hello")  # 触发内部重连
                assert conn_count["n"] == 2
                await stream.flush()
                chunks = [c async for c in stream.audio()]
                assert chunks == [b"\x00\x01\x02\x03"]
                await stream.close()

    async def test_close_cleans_up(self):
        async def handler(ws):
            await _full_round(ws, {})

        async with websockets.serve(_close_tolerant(handler), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.close()
            await stream.close()

    async def test_close_safe_after_failed_reconnect(self):
        """_reconnect 失败会把 _ws 置 None, close() 不得再访问其 protocol."""
        async with websockets.serve(_close_tolerant(lambda ws: asyncio.sleep(0)), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            stream._ws = None  # 模拟重连失败后的状态
            await stream.close()  # 不得 AttributeError

    async def test_feed_text_reconnect_failure_raises_then_close_safe(self):
        """重连失败 → ProviderError 透出; 且 _ws 已被置 None, close 不得抛."""
        async with websockets.serve(_close_tolerant(lambda ws: asyncio.sleep(0)), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = _provider(port)
            stream = await provider.open(voice="v", sample_rate=24000)
            stream._ws = None

            async def boom():
                raise ConnectionError("connect refused")

            stream._reconnect_factory = boom
            with pytest.raises(ProviderError):
                await stream.feed_text("hi")
            assert stream._ws is None
            await stream.close()  # 不得 AttributeError

    def test_workspace_header(self):
        """base_url = 连接端点（不派生不改写）；workspace_id 只驱动请求头。"""
        base = "wss://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        provider = AlibabaBailianTTSProvider(
            base_url=base,
            api_key="k",
            model="m",
            voice="v",
            workspace_id="llm-1tmsqifo51cwfadr",
        )
        assert provider._base_url == base
        assert provider._headers() == {
            "Authorization": "Bearer k",
            "X-DashScope-WorkSpace": "llm-1tmsqifo51cwfadr",
        }

        plain = AlibabaBailianTTSProvider(
            base_url="ws://127.0.0.1:0",
            api_key="k",
            model="m",
            voice="v",
        )
        assert plain._headers() == {"Authorization": "Bearer k"}

    def test_closed_includes_closing(self):
        """CLOSING（close 握手未完成）也必须按死连接处理, 否则 send 抛 ConnectionClosed."""
        class _State:
            name = "CLOSING"

        class _Proto:
            state = _State()

        class _FakeWs:
            protocol = _Proto()

        stream = _BailianStream(_FakeWs(), {}, None)
        assert stream._closed() is True
