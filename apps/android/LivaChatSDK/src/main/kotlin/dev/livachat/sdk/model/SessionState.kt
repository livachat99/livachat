package dev.livachat.sdk.model

/** Server-driven dialog state machine states. */
enum class SessionState {
    IDLE,
    LISTENING,
    THINKING,
    SPEAKING,
}
