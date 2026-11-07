package dev.livachat.sdk.model

/** Per-frame Voice Activity Detection state exposed to the App layer. */
enum class VadState {
    SPEECH,
    SILENCE,
    UNKNOWN,
}
