import Foundation

/// Events delivered by `LivaChatSDK.events`.
///
/// PCM audio is intentionally not part of this stream; it is delivered by
/// `LivaChatAudioSink` on the audio receive queue.
public enum LivaChatEvent: Sendable {
    case connectionStateChanged(ConnectionState)
    case connectionEpochAdvanced(UInt64)
    /// The connection reached a terminal state. Emitted before the
    /// corresponding `.connectionStateChanged(.disconnected)` event.
    /// A user-initiated `disconnect()` does not emit this event.
    case connectionTerminated(ConnectionTerminatedReason)

    case sessionStateChanged(SessionState)
    case localVadStateChanged(VadState)

    case sessionReady(sessionID: String, restored: Bool)
    case sessionEnded(reason: SessionEndedReason, sessionID: String)

    case transcription(text: String, isFinal: Bool, transcriptionID: String)
    /// Full replacement snapshot of running jobs.
    case jobStatus([AgentJob])

    case aiText(
        text: String,
        messageID: String,
        index: UInt32,
        isFinal: Bool,
        image: AiImage?,
        source: AITextSource?
    )

    /// Playback generation after the flush; drop frames older than this epoch.
    case playbackShouldFlush(reason: PlaybackFlushReason, epoch: UInt64)

    case videoAck(frameID: UInt32, processed: Bool)
    case sessionChanged(
        mode: SessionMode,
        myLanguage: String?,
        localLanguage: String?,
        usecase: SessionUsecase?
    )
    case error(code: UInt16, message: String, fatal: Bool, recoverable: Bool)
}
