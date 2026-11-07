package dev.livachat.app.domain.model

import dev.livachat.sdk.model.AiImage
import java.time.Instant

data class ChatMessage(
    val id: String,
    val sessionId: String,
    val role: MessageRole,
    val text: String,
    val isFinal: Boolean,
    val seq: Int,
    val timestamp: Instant,
    val image: AiImage? = null,
    /** Origin of the message: "agent" for agent-pushed replies, null/"llm" for LLM replies. */
    val source: String? = null,
    /**
     * 写入时的会话模式标记（docs/android_multilingual.md §2.3.3 D4=B）：展示时按当前语言拼后缀
     * （MessageBubble）；null = 无后缀（chat 模式，或语言迁移前的历史行——其后缀已并入 [text]，不回拆）。
     */
    val mode: String? = null,
) {
    companion object {
        const val MODE_TRANSCRIBE = "transcribe"
        const val MODE_TRANSLATE = "translate"
    }
}

/**
 * agent job 结果消息 id 前缀：适配器以 id = "ai-$messageId" 落库
 * （ConversationListenerAdapter.kt:185），job 结果的 message_id 恒为
 * "msg_agent_job_{job_key}"（agent_relay/delivery.py:81；docs/ws-protocol-design.md:592）。
 * 前缀含适配器的 "ai-" 段：改 id 拼法须同步此处。
 */
internal const val AGENT_JOB_RESULT_ID_PREFIX = "ai-msg_agent_job_"

/** 是否为 agent job 结果气泡（全屏查看入口判定，docs/android_agent_job_view.md §4.1）。 */
fun ChatMessage.isAgentJobResult(): Boolean = id.startsWith(AGENT_JOB_RESULT_ID_PREFIX)
