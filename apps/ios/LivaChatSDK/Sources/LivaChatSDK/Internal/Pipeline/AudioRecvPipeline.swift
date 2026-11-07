import Foundation

/// Downstream audio pipeline: Opus -> PCM -> sink. Queue-confined to
/// `SDKQueue.audioRecv`.
internal final class AudioRecvPipeline: @unchecked Sendable {
    private let sampleRate: Int
    private let decoder: OpusDecoding
    private let onPCM: (AudioPCMFrame) -> Void
    private let logger: SdkLogger

    init(
        sampleRate: Int,
        decoder: OpusDecoding,
        logger: SdkLogger,
        onPCM: @escaping (AudioPCMFrame) -> Void
    ) {
        self.sampleRate = sampleRate
        self.decoder = decoder
        self.logger = logger
        self.onPCM = onPCM
    }

    func handleAIAudio(
        _ opus: Data,
        durationMs: Int,
        sequenceNumber: UInt64,
        playbackEpoch: UInt64
    ) {
        do {
            let samples = try decoder.decode(opus)
            onPCM(AudioPCMFrame(
                samples: samples,
                sampleRate: sampleRate,
                sampleBits: 16,
                durationMs: durationMs,
                sequenceNumber: sequenceNumber,
                playbackEpoch: playbackEpoch,
                turnID: ""
            ))
        } catch {
            logger.warn("AudioRecv", "opus decode failed", error: error)
        }
    }

    func reset() {
        // Opus decoder state is self-contained.
    }
}
