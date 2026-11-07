package dev.livachat.app.domain.model

import java.time.Instant
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatMessageTest {

    private fun message(id: String) = ChatMessage(
        id = id,
        sessionId = "s1",
        role = MessageRole.AI,
        text = "text",
        isFinal = true,
        seq = 0,
        timestamp = Instant.now(),
    )

    @Test
    fun isAgentJobResult_matches_job_result_message_id() {
        assertTrue(message("ai-msg_agent_job_9f8c7b6a5d4e3f2a").isAgentJobResult())
    }

    @Test
    fun isAgentJobResult_rejects_other_message_ids() {
        // LLM 回复：服务端随机 message_id
        assertFalse(message("ai-3f2b0c1e-1111-2222-3333-444455556666").isAgentJobResult())
        // job 启动提示（summary）：message_id = "msg_agent_{uuid hex16}"
        // （agent_relay/session_manager.py `_generate_message_id`）——与结果共享 "msg_agent_"
        // 前缀，判定必须带上 "job_" 段（最易写错的一例）
        assertFalse(message("ai-msg_agent_9f8c7b6a5d4e3f2a").isAgentJobResult())
        // nudge 等其它 agent 推送：随机 message_id（source 同为 agent）
        assertFalse(message("ai-msg_4d5e6f7a").isAgentJobResult())
        // 用户消息
        assertFalse(message("user-turn-1").isAgentJobResult())
        // 近似前缀不算命中
        assertFalse(message("ai-msg_agent_job").isAgentJobResult())
        assertFalse(message("ai-msg_agent_jobX-1").isAgentJobResult())
        // 前缀不在开头不算命中
        assertFalse(message("x-ai-msg_agent_job_1").isAgentJobResult())
    }
}
