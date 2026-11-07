package dev.livachat.app.ui.screens.permission

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.livachat.app.data.local.PreferencesDataSource
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

sealed class PermissionEvent {
    data object NavigateToConversation : PermissionEvent()
}

data class PermissionUiState(
    val micGranted: Boolean = false,
    val cameraGranted: Boolean = false,
    val micPermanentlyDenied: Boolean = false,
    val showGoToSettings: Boolean = false,
    val showMicRequiredHint: Boolean = false,
)

@HiltViewModel
class PermissionViewModel @Inject constructor(
    private val preferencesDataSource: PreferencesDataSource,
) : ViewModel() {

    private val _uiState = MutableStateFlow(PermissionUiState())
    val uiState: StateFlow<PermissionUiState> = _uiState.asStateFlow()

    private val _event = MutableStateFlow<PermissionEvent?>(null)
    val event: StateFlow<PermissionEvent?> = _event.asStateFlow()

    // 防重复导航：快速连点"开始聊天"只应触发一次进入会话页
    private var navigationStarted = false

    fun onMicPermissionResult(granted: Boolean, permanentlyDenied: Boolean = false) {
        _uiState.value = _uiState.value.copy(
            micGranted = granted,
            micPermanentlyDenied = permanentlyDenied,
            showGoToSettings = permanentlyDenied,
            showMicRequiredHint = granted.not() && _uiState.value.showMicRequiredHint,
        )
    }

    fun onCameraPermissionResult(granted: Boolean) {
        _uiState.value = _uiState.value.copy(cameraGranted = granted)
    }

    fun onStartChat() {
        if (!_uiState.value.micGranted) return
        completeOnboarding()
    }

    fun setMicRequiredHint(show: Boolean) {
        _uiState.value = _uiState.value.copy(showMicRequiredHint = show)
    }

    fun clearEvent() {
        _event.value = null
    }

    private fun completeOnboarding() {
        if (navigationStarted) return
        navigationStarted = true
        viewModelScope.launch {
            preferencesDataSource.setFirstLaunchComplete()
            _event.value = PermissionEvent.NavigateToConversation
        }
    }
}
