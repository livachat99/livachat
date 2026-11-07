package dev.livachat.app.ui.screens.history

import dev.livachat.app.domain.model.SessionSummary

data class HistoryUiState(
    val sessions: List<SessionSummary> = emptyList(),
    val searchQuery: String = "",
    val isLoading: Boolean = true,
    val showDeleteConfirm: String? = null,
    val showClearAllConfirm: Boolean = false,
)
