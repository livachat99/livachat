import AVFoundation
import Foundation
import LivaChatSDK

/// Minimal player surface used by `AudioPlaybackController`. A protocol keeps
/// scheduling/completion behavior deterministic in unit tests without running
/// a real audio engine.
protocol PlaybackNode: AnyObject {
    var volume: Float { get set }
    var isPlaying: Bool { get }
    func play()
    func stop()
    func schedule(_ buffer: AVAudioPCMBuffer, completion: @escaping @Sendable () -> Void)
}

extension AVAudioPlayerNode: PlaybackNode {
    func schedule(_ buffer: AVAudioPCMBuffer, completion: @escaping @Sendable () -> Void) {
        scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in
            completion()
        }
    }
}

/// Jitter buffer + AVAudioPlayerNode playback. The SDK only holds a weak
/// reference to this object; the runtime must keep it alive.
final class AudioPlaybackController: AudioPlaying, LivaChatAudioSink, @unchecked Sendable {
    private let playerNode: PlaybackNode
    private let targetFormat: AVAudioFormat
    private let jitterBuffer: JitterBuffer
    private let pumpTimerEnabled: Bool
    private let engineIsRunning: () -> Bool
    private let queue = DispatchQueue(label: "dev.livachat.playback", qos: .userInteractive)
    private let stateLock = NSLock()

    private var timer: DispatchSourceTimer?
    private var running = false
    private var primed = false
    private var muted = false
    /// Last engine-running value observed by the pump. A down→up transition
    /// invalidates completions that were lost while the engine was stopped
    /// and retries `play()` even when the jitter buffer is already empty.
    private var engineRunningObserved = true
    private var ttsInputExpected = true
    private var testSequence: UInt64 = 0
    private var underruns: UInt64 = 0
    private var scheduledFrameCount: UInt64 = 0

    // Protected by stateLock. Completion handlers and the playback queue can
    // both touch these values.
    private var playbackGeneration: UInt64 = 0
    private var pendingScheduledBuffers = 0
    private var activityHandler: (@MainActor () -> Void)?
    private var lastActivityNotifyUptime: TimeInterval = 0

    init(
        playerNode: PlaybackNode,
        initialEpoch: UInt64 = 1,
        targetFormat: AVAudioFormat = AudioFormats.playback,
        pumpTimerEnabled: Bool = true,
        engineIsRunning: @escaping () -> Bool = { true }
    ) {
        self.playerNode = playerNode
        self.targetFormat = targetFormat
        self.jitterBuffer = JitterBuffer(initialEpoch: initialEpoch)
        self.pumpTimerEnabled = pumpTimerEnabled
        self.engineIsRunning = engineIsRunning
    }

    // MARK: - AudioPlaying

    var isRunning: Bool {
        queue.sync { running }
    }

    var hasBufferedOrScheduledAudio: Bool {
        queue.sync {
            stateLock.lock()
            let pending = pendingScheduledBuffers
            stateLock.unlock()
            return pending > 0 || jitterBuffer.size() > 0
        }
    }

    var onActivityChange: (@MainActor () -> Void)? {
        get {
            stateLock.lock()
            defer { stateLock.unlock() }
            return activityHandler
        }
        set {
            stateLock.lock()
            activityHandler = newValue
            stateLock.unlock()
        }
    }

    func start() {
        queue.sync {
            guard !running else { return }
            running = true
            primed = false
            ttsInputExpected = true
            invalidatePendingLocked()
            timer?.cancel()
            timer = nil
            jitterBuffer.reset()
            playerNode.stop()
            playerNode.volume = muted ? 0 : 1
            playIfEngineRunningLocked()
            startTimerLocked()
            notifyActivity(force: true)
            AppLog.info("AudioPlayback", "playback started")
        }
    }

    func stop() {
        queue.sync {
            running = false
            primed = false
            ttsInputExpected = true
            timer?.cancel()
            timer = nil
            invalidatePendingLocked()
            playerNode.stop()
            jitterBuffer.reset()
            notifyActivity(force: true)
            AppLog.info("AudioPlayback", "playback stopped underruns=\(self.underruns) scheduled=\(self.scheduledFrameCount)")
        }
    }

    func setMuted(_ muted: Bool) {
        queue.sync {
            self.muted = muted
            playerNode.volume = muted ? 0 : 1
        }
    }

    func flush(reason: PlaybackFlushReason, epoch: UInt64) {
        queue.sync {
            invalidatePendingLocked()
            let dropped = jitterBuffer.flush(epoch: epoch)
            guard running else {
                notifyActivity(force: true)
                return
            }
            // Player-node scheduling has no per-buffer cancel; restarting the
            // node clears already-scheduled old-generation audio.
            playerNode.stop()
            primed = false
            playIfEngineRunningLocked()
            notifyActivity(force: true)
            AppLog.info("AudioPlayback", "playback flush reason=\(String(describing: reason)) dropped=\(dropped) epoch=\(epoch)")
        }
    }

    func setTTSInputExpected(_ expected: Bool) {
        queue.sync {
            guard ttsInputExpected != expected else { return }
            ttsInputExpected = expected
            if expected {
                primed = false
            } else if running {
                pumpLocked()
            }
            notifyActivity(force: true)
        }
    }

    /// Stops the player without discarding the jitter buffer. Used when the
    /// shared engine must be reconfigured (for example to release the
    /// microphone) and playback should resume afterwards.
    func prepareForAudioReconfiguration() {
        queue.sync {
            running = false
            primed = false
            timer?.cancel()
            timer = nil
            invalidatePendingLocked()
            playerNode.stop()
            notifyActivity(force: true)
        }
    }

    /// Resumes the pump without resetting the jitter buffer or its epoch.
    func resumePreservingBuffer() {
        queue.sync {
            guard !running else { return }
            running = true
            primed = false
            invalidatePendingLocked()
            playerNode.volume = muted ? 0 : 1
            playIfEngineRunningLocked()
            startTimerLocked()
            notifyActivity(force: true)
        }
    }

    // MARK: - LivaChatAudioSink

    func livaChat(_ sdk: LivaChatSDK, didReceiveAudio frame: AudioPCMFrame) {
        jitterBuffer.push(frame)
        queue.async { [weak self] in
            guard let self else { return }
            // Throttled even while stopped: background-muted media keeps
            // receiving TTS at ~50 Hz, and a forced MainActor hop per frame
            // would defeat the power saving of the teardown. One notification
            // per 100 ms is enough to drive the coordinator's reconcile.
            self.notifyActivity(force: false)
        }
    }

    // MARK: - Debug / tests

    func pushTestSamples(_ samples: [Int16], muted: Bool = false) {
        queue.sync {
            testSequence &+= 1
            let frame = AudioPCMFrame(
                samples: samples,
                sampleRate: 16_000,
                sampleBits: 16,
                durationMs: samples.count * 1000 / 16_000,
                sequenceNumber: testSequence,
                playbackEpoch: jitterBuffer.currentAcceptedEpoch(),
                turnID: ""
            )
            jitterBuffer.push(frame)
            notifyActivity(force: !running)
        }
    }

    func stats() -> (underruns: UInt64, scheduled: UInt64, buffered: Int, pending: Int) {
        queue.sync {
            let pending = pendingCountLocked()
            return (underruns, scheduledFrameCount, jitterBuffer.size(), pending)
        }
    }

    func debugState() -> (
        running: Bool,
        isPlaying: Bool,
        buffered: Int,
        scheduled: UInt64,
        pending: Int,
        generation: UInt64,
        inputExpected: Bool
    ) {
        queue.sync {
            stateLock.lock()
            let pending = pendingScheduledBuffers
            let generation = playbackGeneration
            stateLock.unlock()
            return (
                running,
                playerNode.isPlaying,
                jitterBuffer.size(),
                scheduledFrameCount,
                pending,
                generation,
                ttsInputExpected
            )
        }
    }

    func pumpNowForTesting() {
        queue.sync {
            pumpLocked()
        }
    }

    // MARK: - Pump

    private func startTimerLocked() {
        guard pumpTimerEnabled else { return }
        timer?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(
            deadline: .now() + 0.02,
            repeating: 0.02,
            leeway: .milliseconds(2)
        )
        timer.setEventHandler { [weak self] in
            self?.pumpLocked()
        }
        self.timer = timer
        timer.resume()
    }

    private func pumpLocked() {
        guard running else { return }

        let engineRunning = engineIsRunning()
        if engineRunning != engineRunningObserved {
            engineRunningObserved = engineRunning
            if engineRunning {
                // Completion handlers scheduled before the engine stopped may
                // never fire. Drop their accounting so pending cannot latch,
                // and kick the node: with an empty jitter buffer no new frame
                // will arrive to retry play() from scheduleLocked().
                invalidatePendingLocked()
                playIfEngineRunningLocked()
            }
        }
        guard engineRunning else { return }

        if !playerNode.isPlaying {
            playIfEngineRunningLocked()
        }

        if !primed {
            if ttsInputExpected {
                guard jitterBuffer.isReady() else { return }
            } else {
                guard jitterBuffer.size() > 0 else { return }
            }
            primed = true
        }

        var scheduled = 0
        while scheduled < 2, let frame = jitterBuffer.pull() {
            if scheduleLocked(frame) {
                scheduled += 1
                scheduledFrameCount &+= 1
            }
        }
        if scheduled == 0 {
            underruns &+= 1
        }
    }

    private func scheduleLocked(_ frame: AudioPCMFrame) -> Bool {
        guard !frame.samples.isEmpty else { return false }
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: targetFormat,
            frameCapacity: AVAudioFrameCount(frame.samples.count)
        ) else {
            return false
        }
        buffer.frameLength = AVAudioFrameCount(frame.samples.count)
        guard let destination = buffer.floatChannelData?[0] else { return false }

        frame.samples.withUnsafeBufferPointer { source in
            for index in 0..<source.count {
                destination[index] = Float(source[index]) / 32_768.0
            }
        }

        stateLock.lock()
        let generation = playbackGeneration
        pendingScheduledBuffers += 1
        stateLock.unlock()

        playerNode.schedule(buffer) { [weak self] in
            self?.handleScheduledCompletion(generation: generation)
        }
        if !playerNode.isPlaying {
            playIfEngineRunningLocked()
        }
        notifyActivity(force: false)
        return true
    }

    /// `AVAudioPlayerNode.play()` raises an Objective-C exception when the
    /// engine is not running; Swift cannot catch it. Skip the call and let the
    /// pump retry once it observes the engine running again.
    private func playIfEngineRunningLocked() {
        guard engineIsRunning() else { return }
        playerNode.play()
    }

    private func handleScheduledCompletion(generation: UInt64) {
        stateLock.lock()
        guard generation == playbackGeneration else {
            stateLock.unlock()
            return
        }
        if pendingScheduledBuffers > 0 {
            pendingScheduledBuffers -= 1
        }
        let pending = pendingScheduledBuffers
        stateLock.unlock()

        queue.async { [weak self] in
            guard let self else { return }
            let buffered = self.jitterBuffer.size()
            self.notifyActivity(force: pending == 0 && buffered == 0)
        }
    }

    private func pendingCountLocked() -> Int {
        stateLock.lock()
        defer { stateLock.unlock() }
        return pendingScheduledBuffers
    }

    /// Invalidates completions from an older playback generation and clears
    /// the outstanding count. Must be called on the playback queue.
    private func invalidatePendingLocked() {
        stateLock.lock()
        playbackGeneration &+= 1
        pendingScheduledBuffers = 0
        stateLock.unlock()
    }

    /// Dispatches the activity callback on the main actor. The callback is
    /// never invoked while holding a lock or on the playback queue.
    private func notifyActivity(force: Bool) {
        let now = ProcessInfo.processInfo.systemUptime
        stateLock.lock()
        guard let handler = activityHandler else {
            stateLock.unlock()
            return
        }
        if !force, now - lastActivityNotifyUptime < 0.1 {
            stateLock.unlock()
            return
        }
        lastActivityNotifyUptime = now
        stateLock.unlock()

        Task { @MainActor in
            handler()
        }
    }
}
