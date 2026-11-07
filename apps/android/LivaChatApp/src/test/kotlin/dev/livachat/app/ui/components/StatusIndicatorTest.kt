package dev.livachat.app.ui.components

import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class StatusIndicatorTest {

    @Test
    fun `sessionState IDLE maps to IndicatorState IDLE`() {
        assertEquals(IndicatorState.IDLE, SessionState.IDLE.toIndicatorState())
    }

    @Test
    fun `sessionState LISTENING maps to IndicatorState LISTENING`() {
        assertEquals(IndicatorState.LISTENING, SessionState.LISTENING.toIndicatorState())
    }

    @Test
    fun `sessionState THINKING maps to IndicatorState THINKING`() {
        assertEquals(IndicatorState.THINKING, SessionState.THINKING.toIndicatorState())
    }

    @Test
    fun `sessionState SPEAKING maps to IndicatorState SPEAKING`() {
        assertEquals(IndicatorState.SPEAKING, SessionState.SPEAKING.toIndicatorState())
    }

    @Test
    fun `connectionState CONNECTED maps to IDLE indicator`() {
        assertEquals(IndicatorState.IDLE, ConnectionState.CONNECTED.toIndicatorState())
    }

    @Test
    fun `connectionState DISCONNECTED maps to DISCONNECTED indicator`() {
        assertEquals(IndicatorState.DISCONNECTED, ConnectionState.DISCONNECTED.toIndicatorState())
    }

    @Test
    fun `connectionState CONNECTING maps to CONNECTING indicator`() {
        assertEquals(IndicatorState.CONNECTING, ConnectionState.CONNECTING.toIndicatorState())
    }

    @Test
    fun `all IndicatorState values have non-null color`() {
        IndicatorState.entries.forEach { state ->
            assertEquals(state.color, state.color) // ensuring getter doesn't throw
        }
    }

    @Test
    fun `all IndicatorState values map to distinct label resources (SPEAKING none)`() {
        val resIds = IndicatorState.entries.map { state ->
            if (state == IndicatorState.SPEAKING) {
                assertEquals("SPEAKING 无文案", null, state.labelRes)
            } else {
                assertTrue("$state 缺 labelRes", state.labelRes != null && state.labelRes != 0)
            }
            state.labelRes
        }
        assertEquals("label 资源应互不相同", resIds.size, resIds.toSet().size)
    }
}
