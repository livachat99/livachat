import Foundation

/// Image data carried in a final `ai_text` message when video is enabled.
public struct AiImage: Sendable, Equatable {
    /// JPEG bytes.
    public let data: Data
    public let mime: String
    public let width: Int
    public let height: Int
    public let orientation: Orientation
    public let cameraFacing: CameraFacing

    public init(
        data: Data,
        mime: String,
        width: Int,
        height: Int,
        orientation: Orientation,
        cameraFacing: CameraFacing
    ) {
        self.data = data
        self.mime = mime
        self.width = width
        self.height = height
        self.orientation = orientation
        self.cameraFacing = cameraFacing
    }
}

/// A running background job reported by a `job_status` snapshot.
public struct AgentJob: Sendable, Equatable {
    public let jobID: String
    public let agentName: String
    public let summary: String
    /// Converted to device-local epoch milliseconds with the session clock offset.
    public let startedAtMs: Int64
    /// Server-computed elapsed running time at frame send time.
    public let runningTimeMs: Int64

    public init(
        jobID: String,
        agentName: String,
        summary: String,
        startedAtMs: Int64,
        runningTimeMs: Int64
    ) {
        self.jobID = jobID
        self.agentName = agentName
        self.summary = summary
        self.startedAtMs = startedAtMs
        self.runningTimeMs = runningTimeMs
    }
}

/// Decoded AI TTS PCM delivered through `LivaChatAudioSink`.
public struct AudioPCMFrame: Sendable, Equatable {
    public let samples: [Int16]
    public let sampleRate: Int
    /// Bits per sample; currently always 16.
    public let sampleBits: Int
    public let durationMs: Int
    /// Monotonically increasing within the SDK lifetime; not reset on reconnect.
    public let sequenceNumber: UInt64
    /// Playback generation. Incremented on playback flushes and on leaving
    /// the connected state. The App must drop frames older than its last
    /// accepted flush epoch.
    public let playbackEpoch: UInt64
    /// Reserved; currently always empty.
    public let turnID: String

    public init(
        samples: [Int16],
        sampleRate: Int,
        sampleBits: Int,
        durationMs: Int,
        sequenceNumber: UInt64,
        playbackEpoch: UInt64,
        turnID: String
    ) {
        self.samples = samples
        self.sampleRate = sampleRate
        self.sampleBits = sampleBits
        self.durationMs = durationMs
        self.sequenceNumber = sequenceNumber
        self.playbackEpoch = playbackEpoch
        self.turnID = turnID
    }
}

/// Thread-safe snapshot of the SDK state.
public struct LivaChatState: Sendable, Equatable {
    public let connectionState: ConnectionState
    public let connectionEpoch: UInt64
    public let sessionState: SessionState
    public let localVadState: VadState
    /// Playback generation; see `AudioPCMFrame.playbackEpoch`.
    public let playbackEpoch: UInt64

    public init(
        connectionState: ConnectionState,
        connectionEpoch: UInt64,
        sessionState: SessionState,
        localVadState: VadState,
        playbackEpoch: UInt64
    ) {
        self.connectionState = connectionState
        self.connectionEpoch = connectionEpoch
        self.sessionState = sessionState
        self.localVadState = localVadState
        self.playbackEpoch = playbackEpoch
    }

    public static let initial = LivaChatState(
        connectionState: .disconnected,
        connectionEpoch: 0,
        sessionState: .idle,
        localVadState: .unknown,
        playbackEpoch: 1
    )
}
