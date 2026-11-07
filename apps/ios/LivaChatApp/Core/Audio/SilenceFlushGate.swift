import Foundation

enum CaptureFrameAction: Equatable {
    case sendReal
    case sendSilence
    case drop
}

/// Emits one second of zero PCM on the mute edge so the server VAD can close
/// the current utterance, then drops frames until unmuted.
struct SilenceFlushGate {
    static let windowFrames = 50

    private var remainingFrames = 0
    private var lastMuted: Bool?

    mutating func resetBaseline(muted: Bool) {
        lastMuted = muted
        remainingFrames = 0
    }

    mutating func decide(muted: Bool) -> CaptureFrameAction {
        if muted {
            if lastMuted != true {
                remainingFrames = Self.windowFrames
            }
            lastMuted = true
            guard remainingFrames > 0 else { return .drop }
            remainingFrames -= 1
            return .sendSilence
        }

        lastMuted = false
        remainingFrames = 0
        return .sendReal
    }

    // Background teardown can cut the window short; the caller tops it up
    // before releasing the audio session.
    mutating func consumeRemainingForTopUp() -> Int {
        let remaining = remainingFrames
        remainingFrames = 0
        return remaining
    }

    mutating func reset() {
        lastMuted = nil
        remainingFrames = 0
    }

    var pendingSilenceFrames: Int { remainingFrames }
}
