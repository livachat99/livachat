package dev.livachat.sdk.model

/** Audio sample rate for microphone capture and Opus codec configuration. */
enum class AudioSampleRate(val hz: Int) {
    RATE_16000(16_000),
    RATE_24000(24_000),
}
