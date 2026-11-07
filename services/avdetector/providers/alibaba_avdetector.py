"""DashScope Realtime 协议的 avdetector provider（docs/fd_pipeline_avdetector.md §4 D1/D2）。

与同族的 alibaba_omni.py 的关键差异（故独立实现，[R1] 拍板）：
- `turn_detection: null`（manual）：回合由服务端 `response.create` 触发（检测查询），
  不做 vendor VAD；
- `modalities: ["text"]`：只取文本描述，不出音频；
- 消费语义是「查询-应答」（`check()` 收满一轮）而非常驻事件泵；
- `commit_audio()` 提供静默提交（窗口纪律，D5 ★），不触发回复。

协议事实（2026-10-02/10-03 真机探针，`services/scripts/probe_avdetector_check.py` 可复跑）：
- 握手 `session.created → session.update → session.updated`；事件名与 omni 族一致；
- `response.create` 隐式提交当前音频窗口；每次提交（含静默）都会下发该窗口的
  `conversation.item.input_audio_transcription.completed`（非语音窗口为空串）；
- `input_image_buffer.append` 前该窗口须已 append 过音频（[P6]：provider 静音自愈，
  无音频时先补 20ms 静音——探针 G2/G6 实测）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

import websockets
from websockets.asyncio.client import ClientConnection

from avdetector.base import AvCheckResult, AvCheckTimeout, AvDetectorStream
from media.jpeg import image_resize
from shared.observability.logging import get_logger
from shared.utils.errors import ProviderError

logger = logging.getLogger("livachat.avdetector.alibaba")
msg_logger = get_logger("livachat.avdetector.alibaba.messages")

# text-only 下 voice 不生效；保留仅为对齐已验证的 payload 形态（探针 session 字典同款）
_DEFAULT_VOICE = "Tina"
# [P6] 静音自愈：本窗口无音频时先补 20ms 静音再上图（16k/mono/pcm16；
# 探针 G2/G6 实测满足"先音频后图"约束，G5 复现无注入时的服务端报错）
_PRIMING_SILENCE_16K_20MS = b"\x00" * 640
_COMMIT_SETTLE_TIMEOUT_S = 3.0
_HANDSHAKE_TIMEOUT_S = 10.0

# 对端（DashScope realtime 网关）单条 WS 帧硬限 262144B（=256KB；与官方图片约束
# "base64 ≤256KB" 同源）：超限帧在传输层被 close 1009 "Max frame length of 262144
# has been exceeded."（2026-10-09 探针实测：帧 262,068B 接受 / 263,068B 被拒，见
# docs/fd_pipeline_avdetector.md §10 条目 28）。发送阈值留 1KB 余量。
_MAX_WS_FRAME_BYTES = 262144
_SEND_LIMIT = _MAX_WS_FRAME_BYTES - 1024
# 超限上行副本重压档位（条目 28：最多两档，quality 固定 80）
_RESIZE_EDGE_LADDER = (960, 640)

# 连接死亡哨兵：receiver 退出时投递，唤醒 park 在队列上的 commit/check 等待者
_CONN_DEAD = {"type": "__conn_dead__"}


def _log_msg(direction: str, msg_type: str, **extra: object) -> None:
    msg_logger.debug(f"{direction} {msg_type}", direction=direction, type=msg_type, **extra)


def _get_response_id(msg: dict) -> str | None:
    value = msg.get("response_id") or msg.get("response", {}).get("id")
    return value if isinstance(value, str) else None


def _build_headers(api_key: str, workspace_id: str | None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    if workspace_id:
        headers["X-DashScope-WorkSpace"] = workspace_id
    return headers


class _RealtimeAvDetectorStream:
    def __init__(
        self,
        ws: ClientConnection,
        update_payload: dict,
        base_url: str,
        api_key: str,
        *,
        workspace_id: str | None = None,
    ) -> None:
        self._ws = ws
        self._update_payload = update_payload
        self._base_url = base_url
        self._api_key = api_key
        self._workspace_id = workspace_id
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._event_id = 0
        self._dead = False
        # 自上次提交以来是否喂过音频（空缓冲提交行为未验证 → 无音频不做静默提交）
        self._window_has_audio = False

    # -- infra ---------------------------------------------------------------

    def _next_event_id(self) -> str:
        self._event_id += 1
        return f"evt_{self._event_id}"

    def _drain(self) -> None:
        """清空待消费消息（保留队列对象身份——等待者可能 park 在旧队列上）。"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

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
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type", "")
                if t:
                    _log_msg("recv", t)
                if t == "session.finished":
                    self._dead = True
                    self._queue.put_nowait(_CONN_DEAD)
                    return
                self._queue.put_nowait(msg)
        except asyncio.CancelledError:
            return  # 主动关闭/重连，非连接死亡
        except Exception:
            logger.warning("avdetector receive loop crashed", exc_info=True)
        self._dead = True
        self._queue.put_nowait(_CONN_DEAD)

    async def _handshake(self) -> None:
        """session.created → session.update → session.updated（open 与重连共用）。"""
        raw = await asyncio.wait_for(self._ws.recv(), timeout=_HANDSHAKE_TIMEOUT_S)
        msg = json.loads(raw)
        if msg.get("type") != "session.created":
            raise ProviderError(
                f"expected session.created, got {msg.get('type')}", provider="alibaba_avdetector"
            )
        _log_msg("send", "session.update")
        await self._ws.send(json.dumps(self._update_payload))
        raw = await asyncio.wait_for(self._ws.recv(), timeout=_HANDSHAKE_TIMEOUT_S)
        msg = json.loads(raw)
        if msg.get("type") != "session.updated":
            raise ProviderError(
                f"expected session.updated, got {msg.get('type')}", provider="alibaba_avdetector"
            )
        _log_msg("recv", "session.updated")

    async def _reconnect(self) -> None:
        await self._cancel_receiver()
        try:
            await self._ws.close()
        except Exception:
            pass
        self._dead = False
        try:
            self._ws = await websockets.connect(
                self._base_url,
                additional_headers=_build_headers(self._api_key, self._workspace_id),
                ping_interval=20,
                ping_timeout=20,
                close_timeout=1,
            )
            await self._handshake()
        except Exception:
            self._dead = True
            try:
                await self._ws.close()
            except Exception:
                pass
            raise
        self._drain()
        self._window_has_audio = False
        await self._start_receiver()
        logger.info("avdetector reconnected")

    async def _send_feeding(self, payload: dict, *, op: str) -> None:
        """上行数据面发送（音频 append 专用）：失败重连一次再发。

        图片走 `feed_image` 自带重连（重连后需重新自愈）；音频 append 不记消息日志
        （50fps 洪水）。
        """
        payload["event_id"] = self._next_event_id()
        try:
            await self._ws.send(json.dumps(payload))
            return
        except Exception:
            logger.warning("avdetector %s send failed, reconnecting", op)
        try:
            await self._reconnect()
        except Exception as exc:
            raise ProviderError("avdetector reconnect failed", provider="alibaba_avdetector") from exc
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:
            raise ProviderError(
                f"avdetector {op} failed after reconnect", provider="alibaba_avdetector"
            ) from exc

    # -- AvDetectorStream interface ------------------------------------------

    async def feed_audio(self, pcm: bytes) -> None:
        await self._send_feeding(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()},
            op="feed_audio",
        )
        self._window_has_audio = True

    async def commit_audio(self) -> None:
        """静默提交当前窗口并等待其转写结算（事件丢弃，不触发回复）。

        发送失败 → 重连一次（与 `_send_feeding` 同款）——重连 = 全新会话、陈旧
        窗口随旧连接销毁，"窗口音频丢失"语义由此成立；只告警继续则不然：连接
        未死时服务端仍持有未提交音频，下一次 check 的 response.create 会把它一并
        提交（陈旧转写误触发否决 / 过期事件——正是 D5★ 窗口纪律要堵的混窗路径）。
        重连失败 → 抛 ProviderError（monitor `_pipeline` 兜底降级）。结算超时/
        错误帧仍只记日志——残留事件由 check 入口 drain 清掉。无未提交音频时
        no-op（空缓冲提交行为未验证，不赌）。
        """
        if not self._window_has_audio or self._dead:
            return
        self._window_has_audio = False
        try:
            await self._ws.send(
                json.dumps({"event_id": self._next_event_id(), "type": "input_audio_buffer.commit"})
            )
        except Exception:
            logger.warning("avdetector silent commit send failed, reconnecting")
            try:
                await self._reconnect()
            except Exception as exc:
                raise ProviderError(
                    "avdetector reconnect failed", provider="alibaba_avdetector"
                ) from exc
            return  # 新会话窗口为空——划界目的已达成，无需（也不能）重发
        _log_msg("send", "input_audio_buffer.commit")
        deadline = time.monotonic() + _COMMIT_SETTLE_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.debug("avdetector commit settle timeout")
                return
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except TimeoutError:
                return
            t = msg.get("type", "")
            if t == _CONN_DEAD["type"] or self._dead:
                return
            if t == "conversation.item.input_audio_transcription.completed":
                return
            if t == "error":
                logger.warning("avdetector commit error: %s", msg.get("error"))
                return
            # 其余（committed / item.created / …）为结算过程噪声，丢弃

    async def _prime_if_needed(self) -> None:
        """[P6] 静音自愈：本窗口无音频时先补 20ms 静音（有则不补）。"""
        if self._window_has_audio:
            return
        await self._ws.send(json.dumps({
            "event_id": self._next_event_id(),
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(_PRIMING_SILENCE_16K_20MS).decode(),
        }))
        _log_msg("send", "input_audio_buffer.append", priming=True)
        self._window_has_audio = True

    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None:
        """上图；本窗口无音频时先补 20ms 静音（实测约束，G1–G6）。

        自带重连重试（**不走** `_send_feeding`）：重连 = 全新会话、窗口态清零——
        重试前必须**重新自愈**，否则在新会话上裸发图会撞
        `Error append image before append audio.`（P6 实施后 review 修复）。

        序列化帧超 `_SEND_LIMIT` 时先两档重压上行副本（条目 28）——超限帧会被网关
        以 1009 直接关连接；重压后仍超/失败 → 抛 ProviderError（不发送、不重连），
        由 monitor `_run_check` 的"上图失败不拖垮本轮"路径降级纯音频检查。
        """
        # Qwen realtime：input_image_buffer.append（裸 base64 JPEG，非 data URL）
        payload = {
            "event_id": self._next_event_id(),
            "type": "input_image_buffer.append",
            "image": base64.b64encode(jpeg).decode(),
        }
        frame = json.dumps(payload)
        if len(frame) > _SEND_LIMIT:
            frame = await self._resize_image_frame(
                frame, payload, jpeg, width=width, height=height
            )
        try:
            await self._prime_if_needed()
            await self._ws.send(frame)
            _log_msg("send", "input_image_buffer.append")
            return
        except Exception:
            logger.warning("avdetector feed_image failed, reconnecting")
        try:
            await self._reconnect()
        except Exception as exc:
            raise ProviderError(
                "avdetector reconnect failed", provider="alibaba_avdetector"
            ) from exc
        await self._prime_if_needed()  # 新会话窗口为空 → 重新自愈
        try:
            await self._ws.send(frame)
            _log_msg("send", "input_image_buffer.append")
        except Exception as exc:
            raise ProviderError(
                "avdetector feed_image failed after reconnect", provider="alibaba_avdetector"
            ) from exc

    async def _resize_image_frame(
        self, frame: str, payload: dict, jpeg: bytes, *, width: int, height: int
    ) -> str:
        """超限上行副本两档重压（条目 28）：960 → 640，首个达标档采用。

        预算反推：`_SEND_LIMIT` − 实际 JSON 开销 → base64 4/3 → jpeg 字节预算。
        重压异常（解码失败等）或两档皆不可达 → 抛 ProviderError（不发送、不重连）。
        """
        overhead = len(frame) - len(payload["image"])
        jpeg_budget = 3 * ((_SEND_LIMIT - overhead) // 4)
        resized = None
        edge = 0
        for edge in _RESIZE_EDGE_LADDER:
            try:
                resized = await asyncio.to_thread(
                    image_resize, jpeg, max_edge=edge, max_bytes=jpeg_budget, quality=80
                )
            except Exception as exc:
                logger.warning(
                    "avdetector image resize unusable (decode failed): jpeg %dB frame %dB",
                    len(jpeg), len(frame), exc_info=True,
                )
                raise ProviderError(
                    f"avdetector image resize failed (jpeg {len(jpeg)}B)",
                    provider="alibaba_avdetector",
                ) from exc
            if resized is not None:
                break
        if resized is None:
            logger.warning(
                "avdetector image still over frame limit after %d attempts: frame %dB > %dB",
                len(_RESIZE_EDGE_LADDER), len(frame), _SEND_LIMIT,
            )
            raise ProviderError(
                f"avdetector image still over frame limit after resize "
                f"(frame {len(frame)}B > {_SEND_LIMIT}B)",
                provider="alibaba_avdetector",
            )
        payload["image"] = base64.b64encode(resized).decode()
        frame = json.dumps(payload)
        logger.debug(
            "avdetector image resized for upload: %dx%d jpeg %dB -> %dB, frame %dB (edge %d)",
            width, height, len(jpeg), len(resized), len(frame), edge,
        )
        return frame

    async def check(self, *, timeout_s: float) -> AvCheckResult:
        """发一次 response.create 并收满本轮。

        超时抛 `AvCheckTimeout`（D5：[P2] 只丢结果不计熔断）；连接错误抛 ProviderError。
        超时即 best-effort `response.cancel`：被放弃的 response 若仍在生成，其残响会
        在下一轮检查窗口里串台（[P4]：混文本 / 旧 `response.done` 早退伪造结论）。
        收集按 response_id 过滤（本轮 = 第一个 `response.created` 的 id）——非本轮
        response.* 事件一律丢弃，双保险。
        """
        if self._dead:
            raise ProviderError("avdetector stream dead", provider="alibaba_avdetector")
        self._drain()  # 清静默 commit 的残留结算事件（否决只看本次检查窗口）
        self._window_has_audio = False  # response.create 隐式提交当前窗口
        try:
            await self._ws.send(
                json.dumps({"event_id": self._next_event_id(), "type": "response.create"})
            )
        except Exception as exc:
            raise ProviderError(
                f"avdetector response.create send failed: {exc}", provider="alibaba_avdetector"
            ) from exc
        _log_msg("send", "response.create")

        deadline = time.monotonic() + timeout_s
        current_rid: str | None = None
        text_parts: list[str] = []
        transcripts: list[str] = []
        usage: dict | None = None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await self._cancel_inflight_best_effort()
                raise AvCheckTimeout(
                    f"avdetector check timeout after {timeout_s}s", provider="alibaba_avdetector"
                )
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except TimeoutError:
                await self._cancel_inflight_best_effort()
                raise AvCheckTimeout(
                    f"avdetector check timeout after {timeout_s}s", provider="alibaba_avdetector"
                ) from None
            t = msg.get("type", "")
            if t == _CONN_DEAD["type"] or t == "session.finished" or self._dead:
                raise ProviderError(
                    "avdetector connection lost during check", provider="alibaba_avdetector"
                )
            if t == "response.created":
                rid = _get_response_id(msg)
                if current_rid is None:
                    current_rid = rid  # 绑定本轮（第一个 created）
                continue
            rid = _get_response_id(msg)
            if (t.startswith("response.") and current_rid is not None) and rid and rid != current_rid:
                # [P4] 上一轮超时残响：丢弃（其 done 不得早退本轮、其 delta 不得混入）
                continue
            if t in ("response.text.delta", "response.audio_transcript.delta"):
                text_parts.append(msg.get("delta", ""))
            elif t == "response.done":
                resp = msg.get("response")
                usage = (resp.get("usage") if isinstance(resp, dict) else None) or msg.get("usage")
                break
            elif t == "conversation.item.input_audio_transcription.completed":
                transcripts.append(msg.get("transcript", ""))
            elif t == "error":
                err = msg.get("error", {})
                emsg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                raise ProviderError(f"avdetector check error: {emsg}", provider="alibaba_avdetector")
            # 其余事件（committed / item.created / content_part…）忽略
        return AvCheckResult(text="".join(text_parts), transcripts=transcripts, usage=usage)

    async def _cancel_inflight_best_effort(self) -> None:
        """超时放弃本轮后中止服务端在途生成（失败/无在途响应均无害——错误帧留待 drain）。"""
        try:
            await self._ws.send(
                json.dumps({"event_id": self._next_event_id(), "type": "response.cancel"})
            )
            _log_msg("send", "response.cancel")
        except Exception:
            pass

    async def reopen(self) -> None:
        await self._reconnect()

    async def close(self) -> None:
        try:
            await self._ws.send(
                json.dumps({"event_id": self._next_event_id(), "type": "session.finish"})
            )
        except Exception:
            pass
        await self._cancel_receiver()
        if self._ws.protocol.state.name != "CLOSED":
            try:
                await self._ws.close()
            except Exception:
                pass


class AlibabaAvDetectorProvider:
    name = "alibaba_avdetector"

    def __init__(self, base_url: str, api_key: str, model: str,
                 workspace_id: str | None = None) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._workspace_id = workspace_id

    async def open(
        self,
        *,
        sample_rate: int,
        system_prompt: str = "",
        generation_opts: dict | None = None,
    ) -> AvDetectorStream:
        headers = _build_headers(self._api_key, self._workspace_id)
        try:
            ws = await websockets.connect(
                self._base_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(
                f"alibaba avdetector connect failed: {exc}", provider="alibaba_avdetector"
            ) from exc

        logger.info("alibaba avdetector open model=%s workspace=%s",
                    self._model, self._workspace_id or "-")

        session_cfg: dict = {
            "modalities": ["text"],
            "voice": _DEFAULT_VOICE,
            # flat 兼容字段（与 omni 同款：回显可见、真实生效的是 nested audio.*）
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "sample_rate": sample_rate,
            "audio": {
                "input": {"format": {"type": "pcm", "sample_rate": sample_rate}},
                "output": {"format": {"type": "pcm", "sample_rate": sample_rate}},
            },
            # manual：回合由服务端 response.create 触发（检测查询），不用 vendor VAD
            "turn_detection": None,
            "instructions": system_prompt,
        }
        if generation_opts:
            session_cfg.update(generation_opts)
        update = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": session_cfg,
        }

        stream = _RealtimeAvDetectorStream(
            ws, update, self._base_url, self._api_key, workspace_id=self._workspace_id,
        )
        try:
            await stream._handshake()
        except Exception as exc:
            try:
                await ws.close()
            except Exception:
                pass
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(
                f"alibaba avdetector init failed: {exc}", provider="alibaba_avdetector"
            ) from exc
        await stream._start_receiver()
        return stream
