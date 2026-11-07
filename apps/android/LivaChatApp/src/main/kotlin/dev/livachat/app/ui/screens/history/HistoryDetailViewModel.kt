package dev.livachat.app.ui.screens.history

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.R
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.usecase.HistoryUseCase
import dev.livachat.app.media.ImageGallerySaver
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class HistoryDetailViewModel @Inject constructor(
    private val historyUseCase: HistoryUseCase,
    private val coordinator: ConversationCoordinator,
    private val imageGallerySaver: ImageGallerySaver,
    @ApplicationContext private val appContext: Context,
) : ViewModel() {

    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages.asStateFlow()

    /** 全屏图片查看器：非 null = 展示中，值为入口消息 id（"ai-<messageId>"）。 */
    private val _imageViewerMessageId = MutableStateFlow<String?>(null)
    val imageViewerMessageId: StateFlow<String?> = _imageViewerMessageId.asStateFlow()

    /** 全屏 agent job 结果查看器：非 null = 展示中，值为入口消息 id（"ai-msg_agent_job_*"）。 */
    private val _agentJobResultViewId = MutableStateFlow<String?>(null)
    val agentJobResultViewId: StateFlow<String?> = _agentJobResultViewId.asStateFlow()

    /** agent job 结果查看器落位偏移（打开时点击处正文字符偏移；null = 落顶部）。 */
    private val _agentJobResultInitialCharOffset = MutableStateFlow<Int?>(null)
    val agentJobResultInitialCharOffset: StateFlow<Int?> =
        _agentJobResultInitialCharOffset.asStateFlow()

    /** 查看器保存提示（「已保存到本地相册」/「保存失败」…）；非 null 时底部胶囊可见，2s 后自动清空。 */
    private val _imageSaveTip = MutableStateFlow<String?>(null)
    val imageSaveTip: StateFlow<String?> = _imageSaveTip.asStateFlow()

    // 保存提示的 TTL 定时器（取消语义见 showTipWithTimeout）；保存进行中的重入守卫
    private var tipJob: Job? = null
    private var saveInFlight = false

    fun loadSession(sessionId: String) {
        viewModelScope.launch {
            // 换会话重载即关两个覆盖层：避免指向已切换会话的消息（防悬空）
            closeImageViewer()
            closeAgentJobResult()
            val (_, msgs) = historyUseCase.getSessionDetail(sessionId)
            _messages.value = msgs
        }
    }

    fun deleteSession(sessionId: String) {
        viewModelScope.launch {
            if (coordinator.currentSessionId.value == sessionId) {
                coordinator.replaceWithNewSession()
            }
            historyUseCase.deleteSession(sessionId)
        }
    }

    // ── 图片查看器 + 保存到相册（三期：历史详情接入；拷贝自 ConversationViewModel，
    //    接受冗余换既有 conversation 逻辑零改动）──────────────────────────

    fun openImageViewer(messageId: String) {
        clearSaveTip()   // 2s 内重开查看器不得看到上一轮的陈旧提示
        _imageViewerMessageId.value = messageId
    }

    fun closeImageViewer() {
        _imageViewerMessageId.value = null
    }

    // ── agent job 结果全屏查看（docs/android_agent_job_view.md §12.3.2）──

    fun openAgentJobResult(messageId: String, charOffset: Int?) {
        _agentJobResultViewId.value = messageId
        _agentJobResultInitialCharOffset.value = charOffset
    }

    fun closeAgentJobResult() {
        _agentJobResultViewId.value = null
        _agentJobResultInitialCharOffset.value = null
    }

    /** 保存查看器当前页图片：未命中/无图 no-op；进行中忽略再次点击（防连点重复落库）。 */
    fun saveViewerImage(messageId: String) {
        if (saveInFlight) return
        val image = _messages.value.firstOrNull { it.id == messageId }?.image ?: return
        saveInFlight = true
        viewModelScope.launch {
            try {
                // runCatching 把跨类契约（saver 失败返回 false、不抛）本地化：未来 saver 违约
                // 也收敛为「保存失败」提示，而不是沿 viewModelScope 上抛成崩溃
                val ok = runCatching { imageGallerySaver.save(image) }.getOrDefault(false)
                showTipWithTimeout(
                    if (ok) appContext.getString(R.string.common_saved_to_gallery)
                    else appContext.getString(R.string.common_save_failed),
                )
            } finally {
                // 防未来改动把 flag 卡死、保存入口永久静默失效
                saveInFlight = false
            }
        }
    }

    /** API ≤ 28 存储权限被拒：只提示，不做"去设置"引导。 */
    fun onSavePermissionDenied() {
        showTipWithTimeout(appContext.getString(R.string.common_save_failed_storage))
    }

    /**
     * 统一提示 TTL：先取消上一个定时器再展示 2s。「文案比对」防不了顺序保存串台——
     * 保存 A 成功后 0.5s 保存 B 成功（文案相同），A 的延迟协程到点会提前清掉 B 的提示。
     */
    private fun showTipWithTimeout(text: String) {
        tipJob?.cancel()
        tipJob = viewModelScope.launch {
            _imageSaveTip.value = text
            delay(TIP_TTL_MS)
            _imageSaveTip.value = null
        }
    }

    /** 清空保存提示并取消其定时器（重开查看器时调用）。 */
    private fun clearSaveTip() {
        tipJob?.cancel()
        tipJob = null
        _imageSaveTip.value = null
    }

    private companion object {
        const val TIP_TTL_MS = 2_000L
    }
}
