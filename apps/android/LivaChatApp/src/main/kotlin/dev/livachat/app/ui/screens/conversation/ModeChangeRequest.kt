package dev.livachat.app.ui.screens.conversation

/**
 * 用户在会话内主动请求的模式（断网重连后自动恢复用）。
 *
 * 记录于 [ConversationListenerAdapter]，跨 session.ready 存活（服务端每次
 * session.start 重置回 chat，但"用户意图"不因重连丢失）；显式离开会话/
 * 新建会话（clearSession）时清除。mode 仅 translate / transcribe（chat 为
 * 服务端默认，无需恢复）。
 */
data class ModeChangeRequest(
    val mode: String,
    val myLanguage: String? = null,
    val localLanguage: String? = null,
    val usecase: String? = null,
)
