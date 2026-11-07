package dev.livachat.sdk.model

/** Reason for forcing the App to flush its audio playback buffer. */
enum class PlaybackFlushReason {
    /** Server acknowledged an interrupt; App should stop playback immediately. */
    INTERRUPT_ACK,
    /** Session has ended; App should flush and release AudioTrack. */
    SESSION_ENDED,
    /** Server aborted; App should flush immediately. */
    SERVER_ABORT,
}
