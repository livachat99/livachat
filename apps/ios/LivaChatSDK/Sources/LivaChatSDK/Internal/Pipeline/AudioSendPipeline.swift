import Foundation

/// Upstream audio pipeline: PCM -> frame assembly -> VAD -> optional AGC ->
/// Opus. Queue-confined to `SDKQueue.audioSend`.
internal final class AudioSendPipeline: @unchecked Sendable {
    private let frameMs: Int
    private let vad: VadDetecting
    private let encoder: OpusEncoding
    private let onEncoded: (Data, Int, VadState, Bool) -> Void
    private let onVad: (VadState) -> Void
    private let agc: UplinkAGC?
    private let isAISpeaking: () -> Bool
    private let logger: SdkLogger?

    private var currentPushMuted = false
    private var failureLogged = false
    private lazy var assembler = FrameAssembler(sampleRate: sampleRate, frameMs: frameMs) { [weak self] frame in
        self?.processFrame(frame)
    }

    private let sampleRate: Int

    init(
        sampleRate: Int,
        frameMs: Int = 20,
        vad: VadDetecting,
        encoder: OpusEncoding,
        onEncoded: @escaping (Data, Int, VadState, Bool) -> Void,
        onVad: @escaping (VadState) -> Void,
        uplinkAGC: Bool,
        isAISpeaking: @escaping () -> Bool,
        logger: SdkLogger? = nil
    ) {
        self.sampleRate = sampleRate
        self.frameMs = frameMs
        self.vad = vad
        self.encoder = encoder
        self.onEncoded = onEncoded
        self.onVad = onVad
        self.agc = uplinkAGC ? UplinkAGC(frameMs: frameMs) : nil
        self.isAISpeaking = isAISpeaking
        self.logger = logger
    }

    func pushPCM(_ pcm: [Int16], muted: Bool) {
        currentPushMuted = muted
        assembler.push(pcm)
    }

    func reset() {
        assembler.reset()
        agc?.reset()
    }

    private func processFrame(_ frame: [Int16]) {
        var frame = frame
        let vadState: VadState = frame.withUnsafeBufferPointer { vad.detect($0) }
        onVad(vadState)

        if let agc, !isAISpeaking() {
            _ = agc.process(&frame)
        }

        do {
            let opus: Data = try frame.withUnsafeBufferPointer { buffer in
                try encoder.encode(buffer)
            }
            failureLogged = false
            onEncoded(opus, frameMs, vadState, currentPushMuted)
        } catch {
            if !failureLogged {
                failureLogged = true
                logger?.error("AudioSend", "audio frame encode failed", error: error)
            }
        }
    }
}
