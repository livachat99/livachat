package dev.livachat.app.ui.screens.history

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.usecase.HistoryUseCase
import dev.livachat.app.ui.screens.conversation.ConversationListenerAdapter
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import javax.inject.Inject

@HiltViewModel
class HistoryViewModel @Inject constructor(
    private val historyUseCase: HistoryUseCase,
    private val coordinator: ConversationCoordinator,
    private val listenerAdapter: ConversationListenerAdapter,
) : ViewModel() {

    private val _uiState = MutableStateFlow(HistoryUiState())
    val uiState: StateFlow<HistoryUiState> = _uiState.asStateFlow()

    private val _searchQuery = MutableStateFlow("")

    init {
        loadSessions()
        observeSearch()
    }

    private fun loadSessions() {
        viewModelScope.launch {
            historyUseCase.getAllSessions().collect { sessions ->
                _uiState.update { it.copy(sessions = sessions, isLoading = false) }
            }
        }
    }

    @OptIn(FlowPreview::class, ExperimentalCoroutinesApi::class)
    private fun observeSearch() {
        viewModelScope.launch {
            _searchQuery
                .debounce(300)
                .flatMapLatest { query -> historyUseCase.searchSessions(query) }
                .collect { sessions ->
                    _uiState.update { it.copy(sessions = sessions) }
                }
        }
    }

    fun onSearchQueryChanged(query: String) {
        _uiState.update { it.copy(searchQuery = query) }
        _searchQuery.value = query
    }

    fun onDeleteSession(sessionId: String) {
        viewModelScope.launch {
            if (coordinator.currentSessionId.value == sessionId) {
                coordinator.replaceWithNewSession()
            }
            historyUseCase.deleteSession(sessionId)
        }
    }

    private var clearingAll = false

    /**
     * 清空全部记录。历史页下常有进行中的实时会话,删除期间抑制消息落库并在完成后
     * 换新 session 承接当前对话,否则活会话会把消息写回已删 session 使记录"复活"
     * (语义与 SettingsViewModel 原"清除所有对话记录"一致)。
     */
    fun onClearAll() {
        // 防重入:协程完成前弹窗不关,双击确认会并发跑两次 clearAll+换会话
        if (clearingAll) return
        clearingAll = true
        viewModelScope.launch {
            listenerAdapter.suppressMessages = true
            try {
                // NonCancellable:确认后立刻返回会取消协程,若取消落在 clearAll 与换会话
                // 之间,活会话将指向已删行(下一帧消息外键崩溃)——临界区必须跑完
                withContext(NonCancellable) {
                    historyUseCase.clearAll()
                    coordinator.replaceWithNewSession()
                }
            } finally {
                // 无论异常还是协程取消,都必须解除抑制——adapter 是进程级单例,
                // 泄漏 true 会让之后所有会话静默停止落库
                listenerAdapter.suppressMessages = false
                clearingAll = false
                onSearchQueryChanged("")
                _uiState.update { it.copy(showClearAllConfirm = false) }
            }
        }
    }

    fun showClearAllConfirm(show: Boolean) {
        _uiState.update { it.copy(showClearAllConfirm = show) }
    }

    fun showDeleteConfirm(sessionId: String?) {
        _uiState.update { it.copy(showDeleteConfirm = sessionId) }
    }
}
