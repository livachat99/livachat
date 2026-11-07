package dev.livachat.sdk.model

/** WebSocket connection lifecycle states. */
enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    RECONNECTING,
}
