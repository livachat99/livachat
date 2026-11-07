import Foundation

/// Configuration for `LivaChatSDK`. Values are fixed for the SDK instance's lifetime,
/// except `languageProvider`, which is evaluated on every `session.start`.
public struct LivaChatConfig: Sendable {
    /// WebSocket URL without query parameters, e.g. `wss://host/v1/ws/stream`.
    public var serverURL: URL
    /// Wire token: `sha1_hex(secret + "LivaChat")`.
    public var token: String
    /// Persistent UUID v4 identifying this device.
    public var deviceID: String
    /// Client version written to `session.start.config.client_version`.
    public var clientVersion: String

    // Audio
    public var audioSampleRate: AudioSampleRate
    public var audioFrameMs: Int
    public var vadSensitivity: VadSensitivity
    public var uplinkAGC: Bool

    // Video
    public var videoQuality: Int
    public var videoMaxLongSide: Int
    public var enableVideo: Bool

    // Network
    /// Connection watchdog timeout for black-hole handshakes.
    public var connectTimeout: TimeInterval
    public var reconnectMaxAttempts: Int
    public var reconnectBaseDelay: TimeInterval
    public var reconnectMaxDelay: TimeInterval
    public var keepaliveInterval: TimeInterval
    public var keepaliveTimeout: TimeInterval

    // Diagnostics / platform
    public var logFileURL: URL?
    public var timezone: String?
    /// App UI language reported in `session.start.config.language`.
    /// nil = omit the field (old integrations / SDK users that do not set it).
    public var language: String?
    /// Optional dynamic provider, evaluated on every `session.start`.
    /// Takes precedence over `language`; a nil result falls back to `language`.
    public var languageProvider: (@Sendable () -> String?)?
    /// Initial per-session AVDetector switch; App reads it from persisted settings.
    /// Runtime updates go through `LivaChatSDK.setAvdetectorEnabled(_:)`.
    public var avdetectorEnabled: Bool
    public var tlsPolicy: TLSPolicy

    public init(
        serverURL: URL,
        token: String,
        deviceID: String,
        clientVersion: String,
        audioSampleRate: AudioSampleRate = .hz16000,
        audioFrameMs: Int = 20,
        vadSensitivity: VadSensitivity = .medium,
        uplinkAGC: Bool = false,
        videoQuality: Int = 70,
        videoMaxLongSide: Int = 1080,
        enableVideo: Bool = true,
        connectTimeout: TimeInterval = 10,
        reconnectMaxAttempts: Int = 5,
        reconnectBaseDelay: TimeInterval = 1.0,
        reconnectMaxDelay: TimeInterval = 30.0,
        keepaliveInterval: TimeInterval = 15,
        keepaliveTimeout: TimeInterval = 35,
        logFileURL: URL? = nil,
        timezone: String? = nil,
        tlsPolicy: TLSPolicy = .system,
        language: String? = nil,
        languageProvider: (@Sendable () -> String?)? = nil,
        avdetectorEnabled: Bool = true
    ) {
        self.serverURL = serverURL
        self.token = token
        self.deviceID = deviceID
        self.clientVersion = clientVersion
        self.audioSampleRate = audioSampleRate
        self.audioFrameMs = audioFrameMs
        self.vadSensitivity = vadSensitivity
        self.uplinkAGC = uplinkAGC
        self.videoQuality = videoQuality
        self.videoMaxLongSide = videoMaxLongSide
        self.enableVideo = enableVideo
        self.connectTimeout = connectTimeout
        self.reconnectMaxAttempts = reconnectMaxAttempts
        self.reconnectBaseDelay = reconnectBaseDelay
        self.reconnectMaxDelay = reconnectMaxDelay
        self.keepaliveInterval = keepaliveInterval
        self.keepaliveTimeout = keepaliveTimeout
        self.logFileURL = logFileURL
        self.timezone = timezone
        self.language = language
        self.languageProvider = languageProvider
        self.avdetectorEnabled = avdetectorEnabled
        self.tlsPolicy = tlsPolicy
    }

    internal func validate() throws {
        let scheme = serverURL.scheme?.lowercased()
        guard scheme == "wss" || scheme == "ws" else {
            throw LivaChatError.invalidConfiguration("serverURL.scheme must be wss or ws")
        }
        guard !token.isEmpty else {
            throw LivaChatError.invalidConfiguration("token must not be empty")
        }
        guard !deviceID.isEmpty else {
            throw LivaChatError.invalidConfiguration("deviceID must not be empty")
        }
        guard audioSampleRate == .hz16000 else {
            throw LivaChatError.unsupportedSampleRate(audioSampleRate.rawValue)
        }
        guard audioFrameMs == 20 else {
            throw LivaChatError.unsupportedFrameDuration(audioFrameMs)
        }
        guard (1...100).contains(videoQuality) else {
            throw LivaChatError.invalidConfiguration("videoQuality must be in 1...100")
        }
        guard videoMaxLongSide > 0 else {
            throw LivaChatError.invalidConfiguration("videoMaxLongSide must be > 0")
        }
        guard connectTimeout > 0 else {
            throw LivaChatError.invalidConfiguration("connectTimeout must be > 0")
        }
        guard reconnectMaxAttempts >= 0 else {
            throw LivaChatError.invalidConfiguration("reconnectMaxAttempts must be >= 0")
        }
        guard reconnectBaseDelay > 0 else {
            throw LivaChatError.invalidConfiguration("reconnectBaseDelay must be > 0")
        }
        guard reconnectMaxDelay >= reconnectBaseDelay else {
            throw LivaChatError.invalidConfiguration("reconnectMaxDelay must be >= reconnectBaseDelay")
        }
        guard keepaliveInterval > 0 else {
            throw LivaChatError.invalidConfiguration("keepaliveInterval must be > 0")
        }
        guard keepaliveTimeout > keepaliveInterval else {
            throw LivaChatError.invalidConfiguration("keepaliveTimeout must be > keepaliveInterval")
        }
    }
}
