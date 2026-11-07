package dev.livachat.sdk.model

/** Reason for ending a session. */
enum class SessionEndReason {
    /** User explicitly hung up. */
    USER_HANGUP,
    /** App moved to background. */
    APP_BACKGROUND,
    /** An error caused session termination. */
    ERROR,
}
