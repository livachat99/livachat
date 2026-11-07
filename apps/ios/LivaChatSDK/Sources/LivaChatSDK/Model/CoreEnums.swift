import Foundation

/// WebSocket connection lifecycle states.
public enum ConnectionState: Sendable {
    case disconnected
    case connecting
    case connected
    case reconnecting
}

/// Server-driven dialog state machine states.
public enum SessionState: Sendable {
    case idle
    case listening
    case thinking
    case speaking
}

/// Per-frame voice activity detection state exposed to the App.
public enum VadState: Sendable {
    case speech
    case silence
    case unknown
}

/// WebRTC VAD aggressiveness mode.
public enum VadSensitivity: Int, Sendable {
    case low = 0
    case medium = 1
    case high = 2
}

/// Audio sample rate for microphone capture and Opus codec configuration.
///
/// `hz24000` is declared for protocol parity but is rejected by
/// `LivaChatSDK.init(config:audioSink:)` because WebRTC VAD only supports
/// 8/16/32 kHz.
public enum AudioSampleRate: Int, Sendable {
    case hz16000 = 16_000
    case hz24000 = 24_000
}

/// Reason for a user-initiated interrupt.
public enum InterruptReason: Sendable {
    case userVoice
    case userTap
    case appCancel
}

/// Reason supplied by the App when ending a session (C2S `session.end`).
public enum LivaChatSessionEndReason: Sendable {
    case userHangup
    case appBackground
    case error
}

/// Reason reported by the server in `session.ended` (S2C).
public enum SessionEndedReason: String, Sendable {
    case userQuit = "user_quit"
    case appBackground = "app_background"
    case appTerminate = "app_terminate"
    case serverShutdown = "server_shutdown"
    case error = "error"
    case idleTimeout = "idle_timeout"
}

/// Why the App should flush its audio playback queue.
public enum PlaybackFlushReason: Sendable {
    case interruptAck
    case sessionEnded
    case serverAbort
}

/// Pipeline mode requested via `session.change`.
public enum SessionMode: String, Sendable {
    case chat
    case translate
    case transcribe
    case omni
}

/// Translation usecase; only meaningful when mode is translate.
public enum SessionUsecase: String, Sendable {
    case lecture
    case dialogue
}

/// Origin of an `ai_text` message.
public enum AITextSource: String, Sendable {
    case llm
    case agent
}

/// Camera frame rotation in degrees (the rotation needed to display the image upright).
public enum Orientation: Int, Sendable {
    case portrait0 = 0
    case landscape90 = 90
    case portrait180 = 180
    case landscape270 = 270
}

/// Which source produced a camera frame.
public enum CameraFacing: String, Sendable {
    case front
    case back
    case screen
    case gallery
}

/// Why the connection reached a terminal state.
public enum ConnectionTerminatedReason: Sendable {
    /// HTTP 401 or WebSocket close 4001.
    case authFailed
    /// Reconnect attempts reached `reconnectMaxAttempts`.
    case reconnectExhausted
    /// Server closed the connection with code 1000.
    case serverClosed
}

/// TLS validation policy.
public enum TLSPolicy: Sendable {
    /// System certificate validation (recommended default).
    case system
    /// Accept any server certificate (trust-all). Intended for controlled
    /// self-signed deployments; it does not provide MITM protection.
    case allowSelfSigned
}
