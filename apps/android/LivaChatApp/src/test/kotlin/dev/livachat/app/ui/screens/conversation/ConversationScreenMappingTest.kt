package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.ui.components.AvatarState
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 方案 A：断连终态覆盖 sessionState；
 * RECONNECTING 不覆盖（D2）。
 */
class ConversationScreenMappingTest {

    private fun avatar(conn: ConnectionState, sess: SessionState): AvatarState =
        mapSessionToAvatarState(
            ConversationUiState(connectionState = conn, sessionState = sess),
        )

    @Test
    fun disconnected_overrides_session_states_to_idle() {
        assertEquals(AvatarState.IDLE, avatar(ConnectionState.DISCONNECTED, SessionState.THINKING))
        assertEquals(AvatarState.IDLE, avatar(ConnectionState.DISCONNECTED, SessionState.SPEAKING))
        assertEquals(AvatarState.IDLE, avatar(ConnectionState.DISCONNECTED, SessionState.LISTENING))
    }

    @Test
    fun reconnecting_and_connected_pass_through_session_state() {
        assertEquals(AvatarState.THINKING, avatar(ConnectionState.RECONNECTING, SessionState.THINKING))
        assertEquals(AvatarState.THINKING, avatar(ConnectionState.CONNECTED, SessionState.THINKING))
        assertEquals(AvatarState.LISTENING, avatar(ConnectionState.CONNECTED, SessionState.LISTENING))
        assertEquals(AvatarState.IDLE, avatar(ConnectionState.CONNECTED, SessionState.IDLE))
        assertEquals(AvatarState.SPEAKING, avatar(ConnectionState.CONNECTED, SessionState.SPEAKING))
    }
}
