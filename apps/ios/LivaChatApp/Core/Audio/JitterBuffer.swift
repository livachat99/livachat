import Foundation
import LivaChatSDK

/// Thread-safe playback buffer.
///
/// Pushed from the SDK audio receive queue and drained by the playback queue;
/// flushed from the main actor on playback generation changes.
final class JitterBuffer: @unchecked Sendable {
    static let targetFrames = 3
    /// Hard bound on buffered audio in milliseconds. A frame-count bound
    /// silently scaled with the server's frame duration (15k frames is five
    /// minutes at 20 ms), so bound the actual latency instead.
    ///
    /// The server does not pace TTS to real time: it drains provider chunks as
    /// fast as they arrive, so a normal 10-60 s utterance can be delivered as
    /// a burst. A 1 s cap would truncate those responses; 60 s keeps a full
    /// utterance while bounding the buffer to ~1.9 MB at both 20 ms and 120 ms
    /// frame durations (far below the previous 9.6-57.6 MB).
    static let defaultMaxLatencyMs = 60_000

    struct Stats: Equatable {
        var droppedLate: UInt64 = 0
        var droppedOverflow: UInt64 = 0
        var droppedEpoch: UInt64 = 0
    }

    private let lock = NSLock()
    private let maxLatencyMs: Int
    private var frames: [UInt64: AudioPCMFrame] = [:]
    private var bufferedDurationMs = 0
    private var started = false
    private var nextExpected: UInt64 = 0
    private var acceptedEpoch: UInt64
    private var stats = Stats()

    init(initialEpoch: UInt64 = 1, maxLatencyMs: Int = JitterBuffer.defaultMaxLatencyMs) {
        self.acceptedEpoch = initialEpoch
        self.maxLatencyMs = maxLatencyMs
    }

    func push(_ frame: AudioPCMFrame) {
        lock.lock()
        defer { lock.unlock() }

        guard frame.sampleRate == 16_000 else {
            stats.droppedEpoch += 1
            return
        }
        guard frame.playbackEpoch >= acceptedEpoch else {
            stats.droppedEpoch += 1
            return
        }

        if frame.playbackEpoch > acceptedEpoch {
            acceptedEpoch = frame.playbackEpoch
            frames = frames.filter { $0.value.playbackEpoch >= acceptedEpoch }
            bufferedDurationMs = frames.values.reduce(0) { $0 + durationMs(of: $1) }
            started = false
            nextExpected = 0
        }

        // A frame with no samples carries no audio and must not occupy a
        // zero-cost slot; otherwise a malformed producer could accumulate
        // frames forever now that the frame-count cap is gone.
        guard !frame.samples.isEmpty else {
            stats.droppedOverflow += 1
            return
        }
        // Count late frames before the capacity check so retransmits at the
        // latency cap are attributed to lateness, matching Android.
        if started, frame.sequenceNumber < nextExpected {
            stats.droppedLate += 1
            return
        }

        let duration = max(1, durationMs(of: frame))
        let replacedDuration = frames[frame.sequenceNumber].map { durationMs(of: $0) } ?? 0
        let durationWithoutReplaced = bufferedDurationMs - replacedDuration
        guard durationWithoutReplaced >= 0,
              durationWithoutReplaced <= maxLatencyMs,
              duration <= maxLatencyMs - durationWithoutReplaced
        else {
            stats.droppedOverflow += 1
            return
        }

        frames[frame.sequenceNumber] = frame
        bufferedDurationMs = durationWithoutReplaced + duration
    }

    func pull() -> AudioPCMFrame? {
        lock.lock()
        defer { lock.unlock() }

        guard !frames.isEmpty else { return nil }
        if !started {
            started = true
            nextExpected = frames.keys.min() ?? 0
        }

        let frame: AudioPCMFrame?
        if let expected = frames.removeValue(forKey: nextExpected) {
            frame = expected
        } else if let firstKey = frames.keys.min() {
            // A gap: skip ahead to the next available frame.
            nextExpected = firstKey
            frame = frames.removeValue(forKey: firstKey)
        } else {
            frame = nil
        }
        guard let frame else { return nil }

        bufferedDurationMs -= durationMs(of: frame)
        nextExpected = nextExpected &+ 1
        return frame
    }

    func isReady() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return frames.count >= Self.targetFrames
    }

    func size() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return frames.count
    }

    func currentBufferedDurationMs() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return bufferedDurationMs
    }

    func currentAcceptedEpoch() -> UInt64 {
        lock.lock()
        defer { lock.unlock() }
        return acceptedEpoch
    }

    func currentStats() -> Stats {
        lock.lock()
        defer { lock.unlock() }
        return stats
    }

    /// Drops frames from older generations but keeps frames at or above
    /// `epoch`. New-generation audio may arrive before the flush event.
    @discardableResult
    func flush(epoch: UInt64) -> Int {
        lock.lock()
        defer { lock.unlock() }

        acceptedEpoch = max(acceptedEpoch, epoch)
        let before = frames.count
        frames = frames.filter { $0.value.playbackEpoch >= acceptedEpoch }
        bufferedDurationMs = frames.values.reduce(0) { $0 + durationMs(of: $1) }
        started = false
        nextExpected = 0
        stats.droppedEpoch += UInt64(before - frames.count)
        return before - frames.count
    }

    func reset(acceptedEpoch: UInt64? = nil) {
        lock.lock()
        defer { lock.unlock() }

        frames.removeAll(keepingCapacity: true)
        bufferedDurationMs = 0
        started = false
        nextExpected = 0
        stats = Stats()
        if let acceptedEpoch {
            self.acceptedEpoch = acceptedEpoch
        }
    }

    /// Uses the decoded sample count as the source of truth. The wire
    /// `durationMs` is not validated by the SDK and must not be able to
    /// underreport a long frame (which would defeat the latency bound).
    private func durationMs(of frame: AudioPCMFrame) -> Int {
        guard frame.sampleRate > 0, !frame.samples.isEmpty else {
            // `push` rejects empty frames; keep a non-zero fallback so a future
            // insertion path cannot reintroduce a zero-cost frame.
            return max(1, frame.durationMs)
        }
        // Round up so a sub-millisecond frame still consumes part of the
        // latency budget; duration 0 would let such frames bypass the bound.
        let roundedUp = (frame.samples.count * 1_000 + frame.sampleRate - 1) / frame.sampleRate
        return max(1, roundedUp)
    }
}
