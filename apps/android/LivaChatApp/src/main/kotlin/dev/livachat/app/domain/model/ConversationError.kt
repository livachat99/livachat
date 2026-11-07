package dev.livachat.app.domain.model

sealed class ConversationError {
    data class NetworkError(val message: String, val retryable: Boolean = true) : ConversationError()
    data class AudioError(val message: String, val retryable: Boolean = false) : ConversationError()
    data class ServerError(val code: Int, val message: String) : ConversationError()
    data class PermissionDenied(val permission: String) : ConversationError()
    data object Timeout : ConversationError()
    data class Unknown(val message: String) : ConversationError()
}
