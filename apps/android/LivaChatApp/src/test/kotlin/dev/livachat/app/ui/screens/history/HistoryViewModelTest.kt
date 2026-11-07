package dev.livachat.app.ui.screens.history

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HistoryViewModelTest {

    @Test
    fun historyUiState_defaults_are_correct() {
        val state = HistoryUiState()
        assertTrue(state.isLoading)
        assertTrue(state.sessions.isEmpty())
        assertTrue(state.searchQuery.isEmpty())
        assertEquals(null, state.showDeleteConfirm)
        assertFalse(state.showClearAllConfirm)
    }
}
