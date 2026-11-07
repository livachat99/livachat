from __future__ import annotations

import asyncio
import copy
import json
import logging
import uuid
from collections.abc import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

from shared.utils.errors import ProviderError
from tts.base import TtsStream

logger = logging.getLogger("livachat.tts.bailian")

_SENTINEL = object()

# run-task → task-started 的有界等待。服务端不回 started（ack 丢失/任务被拒）
# 时在此超时后走"重连 + 整段重试一次"；被拒若表现为 task-failed（== pending），
# receiver 写 _task_start_error 并立即唤醒本等待（M3），不走满 5s。
_TASK_START_TIMEOUT_S = 5.0


class _TaskStartFailed(Exception):
    """内部信号：run-task 未获 task-started 确认（超时 / 服务端 task-failed /
    发送失败）。feed_text 捕获后按 §3.2 重连并整段重试一次。"""


class _BailianStream:
    """百炼 duplex 合成流（会话级，可承载顺序多任务）。

    任务生命周期（任务随文本而生，run-task 只在 feed_text 发出——open/reset/
    _reconnect 只维护 WS，无"空 run-task 空转 23s 被服务端杀死"路径）：
        None → pending（run-task 已发、未确认）→ active（task-started 确认）
        → None（task-finished/failed 或 reset）
    任意时刻 pending/active 至多一个非 None；_ws = None 表示连接已死（receiver
    异常置空），下个发送入口必须经 _ensure_ready 重连。
    """

    def __init__(self, ws: ClientConnection, start_payload: dict,
                 reconnect_factory) -> None:
        self._ws: ClientConnection | None = ws
        self._start_payload = start_payload
        self._reconnect_factory = reconnect_factory
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._pending_task_id: str | None = None
        self._active_task_id: str | None = None
        self._finish_sent = False
        self._task_started = asyncio.Event()
        self._task_start_error: str | None = None

    def _closed(self) -> bool:
        """CLOSING（服务端已发 close、握手未完成）同样不可用——send 会抛
        ConnectionClosed，必须按死连接处理走重连。"""
        if self._ws is None:
            return True
        return self._ws.protocol.state.name in ("CLOSED", "CLOSING")

    def _run_payload(self) -> dict:
        """run-task 消息：start 模板 + 当前 pending task_id（feed_text 内发出）。"""
        payload = copy.deepcopy(self._start_payload)
        payload["header"]["task_id"] = self._pending_task_id
        return payload

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()

    async def _start_receiver(self) -> None:
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def _cancel_receiver(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
            self._recv_task = None

    async def _receive_loop(self) -> None:
        """会话级常驻 receiver：不因单个任务结束退出，仅在 _reconnect/close 时
        被取消重建。

        不变式：
        I1 二进制帧仅当 active 任务存在时入队，否则丢弃——无 active 时到达的帧
            必然是旧任务残留或错位帧（配合 "active 仅在 task-started 确认后
            建立"，reset 清 active 与新任务 started 之间的残留帧泄漏窗口收敛
            为零）；
        I2 task-started 仅当 task_id == pending 才生效（→ active、清 pending、
            置 Event）；不匹配的记 debug 丢弃——孤儿任务的迟到 started 不得误
            唤醒新任务的就绪等待（M1）；
        I3 task-finished/task-failed 仅当 task_id == active 时放哨兵并清
            active；== pending 时写 _task_start_error + 置 Event（run-task 被拒
            时快速失败，M3）；其余记 debug 丢弃（不产生误导性 error 日志）；
        I4 连接死亡（async for 抛错或服务端干净关闭）：置 _ws = None，有 active
            时放哨兵让 audio() 立即结束而非挂 30s，任务标记全清——loop 允许退
            出，由 _ensure_ready 在下一个发送入口重建；
        I5 哨兵一个任务至多一个（finished/failed 匹配 active 才放），消费端每轮
            恰好收一个。
        """
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    if self._active_task_id is not None:
                        await self._queue.put(raw)
                else:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    event = msg.get("header", {}).get("event", "")
                    task_id = msg.get("header", {}).get("task_id")
                    if event == "task-started":
                        if task_id is not None and task_id == self._pending_task_id:
                            self._active_task_id = task_id
                            self._pending_task_id = None
                            self._task_started.set()
                        else:
                            logger.debug(
                                "bailian tts task-started ignored "
                                "(task_id=%s pending=%s)", task_id, self._pending_task_id
                            )
                    elif event in ("task-finished", "task-failed"):
                        if self._active_task_id is not None and task_id == self._active_task_id:
                            if event == "task-failed":
                                err = msg.get("header", {}).get("error_message", "unknown")
                                logger.error("bailian tts task failed: %s", err)
                            self._active_task_id = None
                            self._queue.put_nowait(_SENTINEL)
                        elif self._pending_task_id is not None and task_id == self._pending_task_id:
                            # run-task 被拒（配额/参数/模型不可用等）：立即唤醒
                            # feed 的 started 等待，失败点不拖满 5s 超时（M3）
                            self._task_start_error = msg.get("header", {}).get(
                                "error_message", "unknown"
                            )
                            self._task_started.set()
                        else:
                            logger.debug(
                                "bailian tts %s ignored (task_id=%s active=%s)",
                                event, task_id, self._active_task_id,
                            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("bailian tts receive loop error")
        # 连接已不可用（服务端干净/异常关闭）：标记死连接让 _ensure_ready 在下
        # 个发送入口重连；active 任务在途则放哨兵结束 audio()（I4）
        if self._active_task_id is not None:
            self._queue.put_nowait(_SENTINEL)
        elif self._pending_task_id is not None:
            # feed 正在等 task-started 时连接死亡——任务不可能再被确认，立即
            # 唤醒等待者走重连重试，避免白等满 5s 超时（与 I3 的 pending 通知
            # 同通道；唤醒者若已被 cancel 则无人消费，无副作用）
            self._task_start_error = "connection lost before task-started"
            self._task_started.set()
        self._active_task_id = None
        self._pending_task_id = None
        self._ws = None

    async def _ensure_ready(self) -> None:
        """feed/reset 入口：WS 死（含 CLOSING/None）→ _reconnect()；receiver 死而
        WS 活 → 原地重启 receiver（对齐 qwen reset 语义）。

        先判连接再判 receiver：连接状态已可见死亡（服务端 close 已到、receiver
        async for 尚未收尾）时入口即主动重连，不把 run-task 发送浪费到死连接上
        靠通用异常路径自愈（对齐旧版 feed 入口检查语义）。"""
        if self._closed():
            logger.warning("bailian tts connection dead, reconnecting")
            await self._reconnect()
            return
        if self._recv_task is not None and not self._recv_task.done():
            return
        logger.warning("bailian tts receiver task dead, restarting")
        self._queue = asyncio.Queue()
        await self._start_receiver()

    async def _start_task(self) -> None:
        """§3.2 步骤 3a-3e：drain 滞留队列 → 置新 pending + 清 Event/error/
        finish 标记 → 发 run-task → 等 task-started（超时/服务端 task-failed/
        发送抛错均视为建任务失败，抛 _TaskStartFailed 由 feed_text 重连重试）。"""
        self._drain_queue()
        self._pending_task_id = str(uuid.uuid4())
        self._finish_sent = False
        self._task_started.clear()
        self._task_start_error = None
        try:
            await self._ws.send(json.dumps(self._run_payload()))
        except Exception as exc:
            self._pending_task_id = None
            raise _TaskStartFailed(f"run-task send failed: {exc}") from exc
        try:
            await asyncio.wait_for(self._task_started.wait(),
                                   timeout=_TASK_START_TIMEOUT_S)
        except TimeoutError:
            # 清 pending：迟到的旧任务 task-started/task-failed 将按 I2/I3 丢弃
            self._pending_task_id = None
            raise _TaskStartFailed(
                f"no task-started within {_TASK_START_TIMEOUT_S}s"
            ) from None
        if self._task_start_error is not None:
            err, self._task_start_error = self._task_start_error, None
            self._pending_task_id = None
            raise _TaskStartFailed(err)

    async def _send_continue(self, text: str) -> None:
        # 快照连接：_start_task 的 started 等待是长 await，期间 receiver 可能
        # 因连接异常置空 _ws（如 started 确认后连接随即死亡）——直接
        # self._ws.send 会得难看的 NoneType AttributeError；显式转成与 send 抛
        # ConnectionClosed 同语义的失败（ConnectionError → 通用分支重连重试）
        ws = self._ws
        if ws is None:
            raise ConnectionError("bailian tts connection lost")
        await ws.send(
            json.dumps(
                {
                    "header": {
                        "action": "continue-task",
                        "task_id": self._active_task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {"text": text}},
                }
            )
        )

    async def feed_text(self, text: str) -> None:
        """唯一建任务点。

        1. _ensure_ready()（连接死 → 重连；receiver 死而 WS 活 → 原地重启
           receiver）；ready 检查自身失败（连不上）时直接传播 ProviderError——
           网络不可达时背靠背再连一次大概率同样失败，不进任务级重试循环；
        2. 清掉残留 pending（上轮 feed 被 cancel 在 started 等待中的孤儿任务；
           其事件按 I2/I3 丢弃，服务端 23s 后自清，无害）；
        3. 无 active 任务 → _start_task()（run-task → task-started，失败立即
           唤醒/5s 超时）；
        4. 发 continue-task(text)；active 已存在时直接发——保留 duplex 增量喂
           文本能力。
        建任务失败/发送抛错（在健康连接上）→ _reconnect() 后整段重试一次 →
        仍败 ProviderError（编排层已有 text-only 兜底）。重试幂等：若首次
        run-task 实际已到服务端只是 ack 丢失，重试在新 WS 上进行——旧连接已关、
        旧任务随之消亡，无重复音频风险。
        """
        try:
            await self._ensure_ready()
        except Exception as exc:
            # 连接层失败（生产上 _connect_raw 已抛 ProviderError；此处统一包装，
            # 防御 future factory 抛裸异常——编排层按 feed ProviderError 判断）
            raise ProviderError("bailian tts reconnect failed",
                                provider="alibaba_bailian") from exc
        for attempt in range(2):
            try:
                self._pending_task_id = None
                if self._active_task_id is None:
                    await self._start_task()
                await self._send_continue(text)
                return
            except _TaskStartFailed as exc:
                if attempt:
                    raise ProviderError(
                        f"bailian tts task start failed: {exc}",
                        provider="alibaba_bailian",
                    ) from exc
                logger.warning("bailian tts task start failed (%s), retrying on fresh connection", exc)
            except Exception as exc:
                if attempt:
                    raise ProviderError(
                        f"bailian tts feed failed: {exc}",
                        provider="alibaba_bailian",
                    ) from exc
                logger.warning("bailian tts send failed, attempting reconnect")
            try:
                await self._reconnect()
            except Exception as exc:
                raise ProviderError("bailian tts reconnect failed",
                                    provider="alibaba_bailian") from exc

    async def audio(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    async def poll_audio(self) -> AsyncIterator[bytes]:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    async def flush(self) -> None:
        """§3.3：active 且未 finish → best-effort 发 finish-task，发送前置
        _finish_sent 防同任务双发（M4）；否则 no-op。发送失败仅 warning——任务
        由服务端 23s 超时清理，audio() 经 I3/I4 哨兵结束，不为发 finish 而重连。"""
        if self._active_task_id is None or self._finish_sent or self._ws is None:
            return
        self._finish_sent = True
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "header": {
                            "action": "finish-task",
                            "task_id": self._active_task_id,
                            "streaming": "duplex",
                        },
                        "payload": {"input": {}},
                    }
                )
            )
        except Exception:
            logger.warning("bailian tts finish-task send failed "
                           "(task will be reaped by server timeout)")

    async def reset(self) -> None:
        """§3.4 幂等清场，不发 run-task。

        先摘出 active 并记下 finish 标记，再清空全部任务标记 + drain 队列（先清
        标记是 reset 双调幂等的关键——interrupt 路径 pipeline finally 与
        _do_interrupt 会串行双调）；确有 in-flight 任务且未 finish 且连接可用时
        best-effort 补发 finish-task 中止在途合成；pending 态任务不发 finish
        （服务端可能尚不认识它，让其自然超时，事件按 I3 丢弃）。最后
        _ensure_ready() 把死连接的重连成本移出下一轮首包路径（对齐 qwen reset
        语义）。"""
        tid = self._active_task_id
        was_active = tid is not None
        was_finish_sent = self._finish_sent
        self._active_task_id = None
        self._pending_task_id = None
        self._finish_sent = False
        self._drain_queue()
        if was_active and not was_finish_sent and not self._closed():
            try:
                await self._ws.send(
                    json.dumps(
                        {
                            "header": {
                                "action": "finish-task",
                                "task_id": tid,
                                "streaming": "duplex",
                            },
                            "payload": {"input": {}},
                        }
                    )
                )
            except Exception:
                logger.warning("bailian tts reset finish-task send failed")
        await self._ensure_ready()

    async def _reconnect(self) -> None:
        """Fresh WS + 重启 receiver，任务标记全清。不发 run-task；失败置
        _ws = None 并抛出（close 安全性已有回归测试锁）。"""
        await self._cancel_receiver()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        try:
            self._ws = await self._reconnect_factory()
        except Exception:
            self._ws = None
            raise
        self._pending_task_id = None
        self._active_task_id = None
        self._finish_sent = False
        self._task_started.clear()
        self._task_start_error = None
        self._queue = asyncio.Queue()
        await self._start_receiver()
        logger.info("bailian tts reconnected")

    async def close(self) -> None:
        await self._cancel_receiver()
        if not self._closed():
            try:
                await self._ws.close()
            except Exception:
                pass


class AlibabaBailianTTSProvider:
    name = "alibaba_bailian"

    # base_url 是实际连接端点（业务空间模型须配 workspace 专属域名）；
    # workspace_id 仅驱动 X-DashScope-WorkSpace 请求头，两者配套使用、互不派生。
    def __init__(self, base_url: str, api_key: str, model: str, voice: str,
                 workspace_id: str | None = None) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._workspace_id = workspace_id

    def _headers(self) -> dict[str, str]:
        """workspace_id 存在时附加 X-DashScope-WorkSpace 头（业务空间模型缺头 401/411）。"""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._workspace_id:
            headers["X-DashScope-WorkSpace"] = self._workspace_id
        return headers

    async def _connect_raw(self) -> ClientConnection:
        """Open a raw WS to base_url (no run-task yet). Stream reconnects reuse
        this so dead session-level connections recover."""
        try:
            return await websockets.connect(
                self._base_url,
                additional_headers=self._headers(),
                ping_interval=20, ping_timeout=20,
                # Bound the close handshake: the provider doesn't reply to a
                # close frame promptly; 1s keeps mode-switch/teardown paths
                # from stalling (see docs/translate_lecture_improvements.md).
                close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"bailian tts connect failed: {exc}", provider="alibaba_bailian") from exc

    async def open(self, *, voice: str, sample_rate: int, audio_format: str = "pcm") -> TtsStream:
        """连 WS（带保活参数）+ 起会话级 receiver 即返回；不发 run-task、不做
        任务级握手——协议可用性确认推迟到首个 feed（feed 内建任务失败路径已有
        完整兜底：重连重试一次 → ProviderError → 编排层 text-only）。task_id
        占位，实际任务 id 由 feed_text 每次 run-task 前生成。"""
        ws = await self._connect_raw()

        start = {
            "header": {
                "action": "run-task",
                "task_id": "",
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self._model,
                "parameters": {
                    "text_type": "PlainText",
                    "voice": voice or self._voice,
                    "format": audio_format,
                    "sample_rate": sample_rate,
                },
                "input": {},
            },
        }

        stream = _BailianStream(ws, start, self._connect_raw)
        await stream._start_receiver()
        return stream
