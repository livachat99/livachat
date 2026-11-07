package dev.livachat.app.ui.components

import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.Instant

class MessageBubbleTest {

    @Test
    fun `ChatMessage domain model is correctly constructed`() {
        val msg = ChatMessage(
            id = "m1",
            sessionId = "s1",
            role = MessageRole.USER,
            text = "Hello",
            isFinal = false,
            seq = 0,
            timestamp = Instant.now(),
        )
        assertEquals("m1", msg.id)
        assertEquals(MessageRole.USER, msg.role)
        assertEquals("Hello", msg.text)
        assertEquals(false, msg.isFinal)
    }

    @Test
    fun `AI message role is correctly set`() {
        val msg = ChatMessage(
            id = "m2",
            sessionId = "s1",
            role = MessageRole.AI,
            text = "Hi there!",
            isFinal = true,
            seq = 1,
            timestamp = Instant.now(),
        )
        assertEquals(MessageRole.AI, msg.role)
        assertEquals(true, msg.isFinal)
    }
}
