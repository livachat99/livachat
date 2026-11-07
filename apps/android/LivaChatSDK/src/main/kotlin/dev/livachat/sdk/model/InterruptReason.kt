package dev.livachat.sdk.model

/** Reason for user-initiated interrupt. */
enum class InterruptReason {
    /** VAD detected user speech (automatic interrupt). */
    USER_VOICE,
    /** User tapped the interrupt button. */
    USER_TAP,
    /** App cancelled the current turn (e.g., navigation away). */
    APP_CANCEL,
}
