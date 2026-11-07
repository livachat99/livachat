package dev.livachat.sdk.model

/** WebRTC VAD aggressiveness mode. */
enum class VadSensitivity(val mode: Int) {
    LOW(0),
    MEDIUM(1),
    HIGH(2),
}
