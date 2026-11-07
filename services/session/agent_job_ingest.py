"""agent job 结果注入：投递成功时把结果写入 ① WorkingMemory ② recording chatlog。"""

from __future__ import annotations

import logging
import os
import string
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from recording.sink import RecordingSink
from session.history import WorkingMemory
from shared.observability.metrics import get_registry
from shared.utils.time import now_ms

logger = logging.getLogger("livachat.session.agent_job_ingest")

# -- provenance（fd_pipeline_llm_v2）与截断----------------------

AGENT_JOB_PREFIX = "[Agent job result]\n"
AGENT_JOB_PROMPT_DECLARATION = (
    "对话历史中以 [Agent job result] 开头的 assistant 消息，是Agent完成任务后"
    "向用户播报过的最终结果：不要重复播报。"
)

# 后台 Agent 认知声明（docs/prompts_improvements.md §3.5）：装配期追加到 dialog/omni
# system prompt；$nickname 由调用方（app.application._with_agent_awareness）代入。
# 能力清单 = lvdata MCP 5 个本地历史只读工具 + "联网查询实时信息"（2026-09-30 部署方确认
# Hermes 侧具备；具体 toolset 由 Hermes 侧配置决定，见 hermes_adaptor_design_v2.md §6.2）。
AGENT_AWARENESS_DECLARATION_TEMPLATE = (
    "【后台助手「$nickname」】用户可能用这个名字称呼一个后台 Agent:它能做需要较长时间、"
    "或你在实时对话里\n做不到的事——查用户的历史对话与长期记忆、联网查询实时信息、"
    "多步工具调用、长思考。\n你自己负责与用户的实时语音互动;遇到超出你能力、"
    "或需要较长时间的要求,在回复里自然地建议交给它\n(如「这个我让$nickname帮你查一下」),"
    "不要生硬拒绝或声称完全无法处理,也不要替它执行、不要自称是它。\n"
    "用户点名「$nickname」时,那句要求是给它的:你可以简短确认(如「好,我让$nickname处理」);\n"
    "它的答复会以文字显示在对话里(不会朗读)。"
)

# 通用单助手模板（非 Hermes 单 adapter，如 OpenClaw-only；docs/agent_relay_prompts.md §8.4-b）：
# 能力清单与 Hermes 模板一致（2026-10-18 用户确认 OpenClaw 也支持联网查询实时信息），措辞略简。
AGENT_AWARENESS_GENERIC_TEMPLATE = (
    "【后台助手「$nickname」】用户可能用这个名字称呼一个后台 Agent:它能做需要较长时间、"
    "或你在实时对话里做不到的事(查用户的历史对话与长期记忆、联网查询实时信息、"
    "多步工具调用、长思考)。\n"
    "你自己负责与用户的实时语音互动;遇到超出你能力、或需要较长时间的要求,在回复里自然地建议交给它\n"
    "(如「这个我让$nickname帮你查一下」),不要生硬拒绝或声称完全无法处理,也不要替它执行、不要自称是它。\n"
    "用户点名「$nickname」时,那句要求是给它的:你可以简短确认(如「好,我让它处理」);\n"
    "它的答复会以文字显示在对话里(不会朗读)。"
)

# 多助手模板（docs/agent_relay_prompts.md §8.4-a）：能力描述使用当前 adapter 共有的通用口径
# （Hermes/OpenClaw 均支持联网查询实时信息）；$nickname_list / $first_nickname 由
# render_agent_awareness 代入。
AGENT_AWARENESS_MULTI_TEMPLATE = (
    "【后台助手:$nickname_list】用户可能用这些名字分别称呼后台 Agent:\n"
    "它们能做需要较长时间、或你在实时对话里做不到的事"
    "(查用户的历史对话与长期记忆、联网查询实时信息、多步工具调用、长思考)。\n"
    "你自己负责与用户的实时语音互动;遇到超出你能力、或需要较长时间的要求,"
    "在回复里自然地建议交给合适的后台助手\n"
    "(如「这个我让$first_nickname帮你查一下」),不要生硬拒绝或声称完全无法处理,"
    "也不要替它们执行、不要自称是它们。\n"
    "用户点名某个后台助手时,那句要求是给它的:你可以简短确认(如「好,我让它处理」);\n"
    "若用户没有点名或无法判断指的是谁,用「后台助手」泛称,不要臆测具体名字。\n"
    "它们的答复会以文字显示在对话里(不会朗读)。"
)


@dataclass(frozen=True)
class NamedAgent:
    """One user-facing background agent for the LivaChat awareness declaration."""

    nickname: str
    adapter_names: tuple[str, ...] = ()


def render_agent_awareness(agents: Sequence[NamedAgent]) -> str:
    """Render the awareness declaration; empty/unknown -> no declaration.

    Single Hermes agent keeps the byte-identical legacy template. A single
    non-Hermes agent (or a nickname shared by multiple backends) uses the
    generic template; multiple names use the multi-agent template.
    """
    if not agents:
        return ""
    if len(agents) == 1:
        agent = agents[0]
        template = (
            AGENT_AWARENESS_DECLARATION_TEMPLATE
            if agent.adapter_names == ("hermes",)
            else AGENT_AWARENESS_GENERIC_TEMPLATE
        )
        return string.Template(template).safe_substitute(nickname=agent.nickname)
    names = "、".join(f"「{a.nickname}」" for a in agents)
    return string.Template(AGENT_AWARENESS_MULTI_TEMPLATE).safe_substitute(
        nickname_list=names,
        first_nickname=agents[0].nickname,
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


AGENT_JOB_HISTORY_MAX_CHARS = _env_int("AGENT_JOB_HISTORY_MAX_CHARS", 4096)
_TRUNC_MARK = "\n…[truncated]…\n"


def _truncate_for_history(text: str) -> str:
    if AGENT_JOB_HISTORY_MAX_CHARS <= 0 or len(text) <= AGENT_JOB_HISTORY_MAX_CHARS:
        return text
    head = AGENT_JOB_HISTORY_MAX_CHARS * 3 // 4
    tail = AGENT_JOB_HISTORY_MAX_CHARS - head
    return text[:head] + _TRUNC_MARK + text[-tail:]


# -- 注入主入口 --------------------------------------------------------------

def _inc_ingest(path: str, result: str) -> None:
    get_registry().agent_job_ingest_total().labels(path=path, result=result).inc()


def ingest_agent_job_result(
    *,
    history: WorkingMemory | None,
    recording_sink: RecordingSink | None,
    live_session_id: str,
    text: str,
    message_id: str,
    agent_name: str = "",
    agent_id: str = "",
    job_key: str = "",
    chat_store: Any = None,
    device_uuid: str = "",
) -> None:
    """全部 best-effort：单项失败只 log + metric，不影响另一项与已投递的
    客户端帧。无 await 阻塞点（add_message 同步 append、on_chat_text 同步
    put_nowait，sink 契约见 recording/sink.py）。"""
    # ① WorkingMemory：前缀 + 截断 + source/ref_id 标记，ref_id 去重；
    #    ③ chat store：与 history 同点写 messages 行（kind='agent_job'，history
    #    形态），restore 可直接重放且 has_agent_job 去重语义不变（§3.4）
    try:
        if history is None:
            _inc_ingest("history", "no_history")
        elif history.has_agent_job(message_id):
            _inc_ingest("history", "dedup_skip")
        else:
            content = AGENT_JOB_PREFIX + _truncate_for_history(text)
            history.add_message(
                "assistant", content,
                source="agent_job", ref_id=message_id,
            )
            if chat_store is not None and device_uuid:
                chat_store.on_message(
                    device_uuid=device_uuid,
                    session_id=live_session_id,
                    kind="agent_job", role="assistant",
                    content=content, ref_id=message_id,
                    ts_ms=now_ms(),
                )
            _inc_ingest("history", "ok")
    except Exception:
        _inc_ingest("history", "failed")
        logger.warning("agent job history ingest failed", exc_info=True)
    # ② chatlog：全文、无前缀（NoOp sink 天然降级；detach 期间 writer 仍存活，可写）
    # agent_name/agent_id/job_key 随行落盘（recording_data_process.md §2.5）：
    # lvdata 侧据此铸造 agent_job 事件（event_ref 用 job_key，与旧事件对齐）
    try:
        if recording_sink is not None:
            recording_sink.on_chat_text(
                role="assistant", text=text, ts_ms=now_ms(),
                ref_id=message_id, chat_session_id=live_session_id,
                source="agent_job",
                agent_name=agent_name, agent_id=agent_id, job_key=job_key,
            )
        _inc_ingest("chatlog", "ok")
    except Exception:
        _inc_ingest("chatlog", "failed")
        logger.warning("agent job chatlog ingest failed", exc_info=True)
    logger.info(
        "agent job result ingested",
        extra={"message_id": message_id, "chat_session_id": live_session_id},
    )
