import AVFoundation
import Foundation

final class AudioCaptureController: AudioCapturing, @unchecked Sendable {
    enum CaptureError: LocalizedError {
        case permissionDenied
        case invalidInputFormat
        case converterUnavailable
        case engineStartFailed(Error)

        var errorDescription: String? {
            switch self {
            case .permissionDenied:
                return "Microphone permission is required"
            case .invalidInputFormat:
                return "Microphone input format is unavailable"
            case .converterUnavailable:
                return "Unable to convert microphone audio to 16 kHz mono"
            case let .engineStartFailed(error):
                return "Audio engine failed to start: \(error.localizedDescription)"
            }
        }
    }

    private let engine: AVAudioEngine
    private let targetFormat: AVAudioFormat
    private let permissionCheck: () -> Bool
    private let startAttemptOverride: (() throws -> Void)?
    private let retryDelaysNanoseconds: [UInt64]
    private let retrySleep: @Sendable (UInt64) async throws -> Void
    private let controlQueue: DispatchQueue
    private let uplink: @Sendable ([Int16], Bool) -> Void
    private let uptime: @Sendable () -> TimeInterval
    private let levelDelivery: @Sendable (@escaping @MainActor @Sendable () -> Void) -> Void
    private let beforeUplink: @Sendable () -> Void
    private let beforeStopBaseline: @Sendable () -> Void
    private let zeroFrame = [Int16](repeating: 0, count: 320)
    private let lock = NSLock()
    private let controlLock = NSLock()
    /// Serializes real-PCM delivery with generation invalidation. `resetLevelLocked`
    /// updates `uplinkGeneration` while holding this lock, so a mute/stop/restart
    /// either waits for an in-flight uplink or prevents a not-yet-started one.
    private let uplinkLock = NSLock()
    private var uplinkGeneration: UInt64 = 0

    private var converter: AVAudioConverter?
    private var chunker = PCMChunker()
    private var gate = SilenceFlushGate()
    private var meter = MicLevelMeter()
    private var currentMuted = false
    /// One-shot, best-effort guard for the first tap callback after unmute.
    /// The buffer in flight when `setMuted(false)` runs may already contain
    /// audio captured while muted, even though its callback starts afterwards.
    /// Generation filtering drops taps that started before the edge; this
    /// token drops the first callback that starts after it. The platform does
    /// not document the callback queue depth, so this is not a proof that
    /// every queued pre-unmute buffer is excluded.
    private var discardNextTapBuffer = false
    private var capturing = false
    private var tapInstalled = false
    private var lastLevelReport: TimeInterval = 0
    private var levelGeneration: UInt64 = 0
    private var levelReportSequence: UInt64 = 0
    private var lastAppliedLevelReportSequence: UInt64 = 0
    private var lastMeterLevel = 0
    private var tapCount: UInt64 = 0
    private var convertedSamples: UInt64 = 0
    private var emptyConversions: UInt64 = 0
    private var conversionErrors: UInt64 = 0
    private var conversionFailureStreak = 0
    /// Monotonic token for the latest conversion failure/success. A tap-thread
    /// failure dispatch revalidates this token under `controlLock` + `lock` so
    /// a success or a newer failure that landed while the control queue was
    /// busy cannot leave a stale recovery decision in flight.
    private var conversionFailureToken: UInt64 = 0

    private var startGeneration: UInt64 = 0
    private var retryTask: Task<Void, Never>?
    private var recovering = false

    var onLevel: (@MainActor (Int) -> Void)?
    var onFailure: (@MainActor (AppErrorMessage) -> Void)?

    var lifecycleState: CaptureLifecycleState {
        controlLock.lock()
        if recovering {
            controlLock.unlock()
            return .recovering
        }
        lock.lock()
        let isCapturing = capturing
        lock.unlock()
        controlLock.unlock()
        return isCapturing ? .running : .stopped
    }

    init(
        engine: AVAudioEngine,
        targetFormat: AVAudioFormat = AudioFormats.capture,
        permissionCheck: @escaping () -> Bool = {
            AVAudioSession.sharedInstance().recordPermission == .granted
        },
        startAttemptOverride: (() throws -> Void)? = nil,
        retryDelaysNanoseconds: [UInt64] = [200_000_000, 400_000_000, 800_000_000],
        retrySleep: @escaping @Sendable (UInt64) async throws -> Void = {
            try await Task.sleep(nanoseconds: $0)
        },
        controlQueue: DispatchQueue = .global(qos: .userInitiated),
        uplink: @escaping @Sendable ([Int16], Bool) -> Void,
        uptime: @escaping @Sendable () -> TimeInterval = {
            ProcessInfo.processInfo.systemUptime
        },
        levelDelivery: @escaping @Sendable (@escaping @MainActor @Sendable () -> Void) -> Void = { work in
            Task { @MainActor in work() }
        },
        beforeUplink: @escaping @Sendable () -> Void = {},
        beforeStopBaseline: @escaping @Sendable () -> Void = {}
    ) {
        self.engine = engine
        self.targetFormat = targetFormat
        self.permissionCheck = permissionCheck
        self.startAttemptOverride = startAttemptOverride
        self.retryDelaysNanoseconds = retryDelaysNanoseconds
        self.retrySleep = retrySleep
        self.controlQueue = controlQueue
        self.uplink = uplink
        self.uptime = uptime
        self.levelDelivery = levelDelivery
        self.beforeUplink = beforeUplink
        self.beforeStopBaseline = beforeStopBaseline
    }

    func start() throws {
        guard permissionCheck() else {
            throw CaptureError.permissionDenied
        }

        controlLock.lock()
        retryTask?.cancel()
        retryTask = nil
        startGeneration &+= 1
        let generation = startGeneration
        recovering = true
        controlLock.unlock()

        lock.lock()
        if capturing {
            lock.unlock()
            withControlLock {
                if startGeneration == generation {
                    recovering = false
                }
            }
            return
        }
        tapCount = 0
        convertedSamples = 0
        emptyConversions = 0
        conversionErrors = 0
        conversionFailureStreak = 0
        lock.unlock()

        do {
            try performStartAttempt(generation: generation)
            withControlLock {
                if startGeneration == generation {
                    recovering = false
                }
            }
        } catch is CancellationError {
            withControlLock {
                if startGeneration == generation {
                    recovering = false
                }
            }
            return
        } catch CaptureError.permissionDenied {
            withControlLock {
                if startGeneration == generation {
                    recovering = false
                }
            }
            throw CaptureError.permissionDenied
        } catch {
            beginRetries(generation: generation, attempt: 1, lastError: error.localizedDescription)
        }
    }

    func stop() {
        controlLock.lock()
        startGeneration &+= 1
        let wasRecovering = recovering || retryTask != nil
        recovering = false
        retryTask?.cancel()
        retryTask = nil
        let wasCapturing = teardownLocked()
        let baseline: (generation: UInt64, sequence: UInt64)
        if wasCapturing || wasRecovering {
            lock.lock()
            baseline = resetLevelLocked()
            lock.unlock()
        } else {
            // No lifecycle reset, but snapshot the generation and reserve the
            // zero's sequence while still holding controlLock. Reading the
            // current generation after unlocking could re-attribute this old
            // stop's zero to a newer start and overwrite its first level.
            lock.lock()
            levelReportSequence &+= 1
            baseline = (levelGeneration, levelReportSequence)
            lock.unlock()
        }
        controlLock.unlock()

        beforeStopBaseline()
        reportLevel(0, generation: baseline.generation, sequence: baseline.sequence)
        if wasCapturing {
            AppLog.info("AudioCapture", "capture stopped")
        }
    }

    func setMuted(_ muted: Bool) {
        lock.lock()
        let changed = currentMuted != muted
        currentMuted = muted
        let baseline: (generation: UInt64, sequence: UInt64)?
        let generation: UInt64
        if changed {
            let reset = resetLevelLocked()
            baseline = reset
            generation = reset.generation
            if muted {
                discardNextTapBuffer = false
            } else {
                chunker.reset()
                // A tap buffer converted around the unmute can still contain
                // pre-unmute audio. Drop the first whole tap buffer whose
                // callback starts after the edge. This is a one-buffer,
                // best-effort privacy guard: a tap already in flight is
                // dropped separately by its stale generation, so this can
                // discard two consecutive buffers, and the platform queue
                // depth is undocumented, so it cannot guarantee that no
                // further pre-unmute buffer is queued.
                discardNextTapBuffer = true
            }
        } else {
            baseline = nil
            generation = levelGeneration
        }
        let shouldReport = changed || muted
        lock.unlock()

        if shouldReport {
            if let baseline {
                reportLevel(0, generation: baseline.generation, sequence: baseline.sequence)
            } else {
                reportLevel(0, generation: generation)
            }
        }
    }

    func topUpSilenceWindow() {
        lock.lock()
        let remaining = capturing ? gate.consumeRemainingForTopUp() : 0
        lock.unlock()

        guard remaining > 0 else { return }
        for _ in 0..<remaining {
            uplink(zeroFrame, true)
        }
        AppLog.info("AudioCapture", "mute window topped up: \(remaining) frames")
    }

    func debugStats() -> (taps: UInt64, samples: UInt64, empty: UInt64, errors: UInt64) {
        lock.lock()
        defer { lock.unlock() }
        return (tapCount, convertedSamples, emptyConversions, conversionErrors)
    }

    // MARK: - Capture lifecycle

    /// Runs on a control thread while `controlLock` is held by the caller.
    private func performStartAttempt(generation: UInt64) throws {
        controlLock.lock()
        defer { controlLock.unlock() }

        guard startGeneration == generation else {
            throw CancellationError()
        }
        guard permissionCheck() else {
            throw CaptureError.permissionDenied
        }

        lock.lock()
        let alreadyCapturing = capturing
        lock.unlock()
        if alreadyCapturing { return }

        // Fault-injection seam for unit tests; no effect in production.
        if let startAttemptOverride {
            try startAttemptOverride()
            lock.lock()
            converter = nil
            chunker.reset()
            discardNextTapBuffer = false
            gate.resetBaseline(muted: currentMuted)
            let baseline = resetLevelLocked()
            capturing = true
            lastLevelReport = 0
            lock.unlock()
            reportLevel(0, generation: baseline.generation, sequence: baseline.sequence)
            AppLog.info("AudioCapture", "capture started (override)")
            return
        }

        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            throw CaptureError.invalidInputFormat
        }
        guard let converter = AVAudioConverter(from: inputFormat, to: targetFormat) else {
            throw CaptureError.converterUnavailable
        }

        if tapInstalled {
            inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }

        lock.lock()
        self.converter = converter
        chunker.reset()
        discardNextTapBuffer = false
        gate.resetBaseline(muted: currentMuted)
        let baseline = resetLevelLocked()
        capturing = true
        lastLevelReport = 0
        lock.unlock()
        reportLevel(0, generation: baseline.generation, sequence: baseline.sequence)

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            self?.handleTap(buffer)
        }
        tapInstalled = true

        if !engine.isRunning {
            engine.prepare()
            do {
                try engine.start()
            } catch {
                inputNode.removeTap(onBus: 0)
                tapInstalled = false
                lock.lock()
                capturing = false
                self.converter = nil
                lock.unlock()
                throw CaptureError.engineStartFailed(error)
            }
        }

        AppLog.info("AudioCapture", "capture started: input=\(inputFormat.description)")
    }

    /// Control lock must be held by the caller.
    private func teardownLocked() -> Bool {
        lock.lock()
        let wasCapturing = capturing
        capturing = false
        converter = nil
        chunker.reset()
        discardNextTapBuffer = false
        gate.reset()
        lock.unlock()

        if tapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        return wasCapturing
    }

    private func withControlLock<T>(_ body: () -> T) -> T {
        controlLock.lock()
        defer { controlLock.unlock() }
        return body()
    }

    private func beginRetries(generation: UInt64, attempt: Int, lastError: String) {
        AppLog.warn(
            "AudioCapture",
            "capture start failed; retrying attempt=\(attempt) error=\(lastError)"
        )

        // Create and publish the task in the same critical section that sets
        // `recovering`. Every task path that clears `recovering`/`retryTask`
        // takes `controlLock` first, so an immediate `retrySleep` cannot let
        // the task finish before the creator has stored `retryTask` and
        // published `recovering = true`. Creating the task before taking the
        // lock leaves that window: the creator could then store an already
        // completed task and re-set `recovering`, latching `.recovering` with
        // no task left to clear it.
        controlLock.lock()
        guard startGeneration == generation else {
            controlLock.unlock()
            return
        }
        retryTask?.cancel()
        recovering = true
        retryTask = makeRetryTask(
            generation: generation,
            attempt: attempt,
            lastError: lastError
        )
        controlLock.unlock()
    }

    private func makeRetryTask(
        generation: UInt64,
        attempt: Int,
        lastError: String
    ) -> Task<Void, Never> {
        Task { [weak self] in
            guard let self else { return }
            var retry = attempt
            var last = lastError
            let delays = self.retryDelaysNanoseconds

            while retry <= delays.count {
                do {
                    try await self.retrySleep(delays[retry - 1])
                } catch {
                    return
                }
                guard !Task.isCancelled else { return }

                let current = self.withControlLock {
                    let current = self.startGeneration == generation
                    if current { self.recovering = true }
                    return current
                }
                guard current else { return }

                do {
                    try self.performStartAttempt(generation: generation)
                    self.withControlLock {
                        if self.startGeneration == generation {
                            self.recovering = false
                            self.retryTask = nil
                        }
                    }
                    AppLog.info("AudioCapture", "capture recovered after retry \(retry)")
                    return
                } catch is CancellationError {
                    return
                } catch CaptureError.permissionDenied {
                    last = "Microphone permission denied"
                    break
                } catch {
                    last = error.localizedDescription
                    AppLog.warn("AudioCapture", "capture retry \(retry) failed: \(last)")
                }
                retry += 1
            }

            // Capture the level generation in the same critical section as the
            // start-generation check. A new `start()` racing this terminal
            // path bumps both generations; tagging the zero with the captured
            // one makes the stale report fail generation filtering instead of
            // overwriting the new session's first level.
            let terminalLevelGeneration = self.withControlLock { () -> UInt64? in
                guard self.startGeneration == generation else { return nil }
                self.recovering = false
                self.retryTask = nil
                self.lock.lock()
                let levelGeneration = self.levelGeneration
                self.lock.unlock()
                return levelGeneration
            }

            guard let terminalLevelGeneration else { return }
            self.reportLevel(0, generation: terminalLevelGeneration)
            self.reportFailure(
                AppErrorMessage(
                    "conv_mic_unavailable",
                    logDetail: "Microphone unavailable after retries: \(last)"
                ),
                expectedStartGeneration: generation
            )
        }
    }

    private func requestConversionRecovery(
        reason: String,
        originatingGeneration: UInt64,
        failureToken: UInt64? = nil
    ) {
        controlLock.lock()
        lock.lock()
        // Validate the originating capture and perform the recovery state
        // transition atomically. Without this, a stale conversion error can
        // pass a prior generation check and then recover (or fail) the
        // replacement capture started by an intervening stop/start. The
        // failure token additionally rejects a recovery that was overtaken by
        // a successful conversion or a newer failure while the control queue
        // was busy.
        let isCurrent = capturing
            && !recovering
            && originatingGeneration == levelGeneration
            && (failureToken == nil || failureToken == conversionFailureToken)
        guard isCurrent else {
            lock.unlock()
            controlLock.unlock()
            return
        }
        recovering = true
        startGeneration &+= 1
        let generation = startGeneration
        retryTask?.cancel()
        retryTask = nil
        lock.unlock()
        controlLock.unlock()

        // Do not remove the tap from inside the tap callback; tear down on a
        // control thread, then retry the pipeline with backoff. Keep
        // `recovering` true across the teardown so observers never see a
        // transient `.stopped` state.
        controlQueue.async { [weak self] in
            guard let self else { return }
            self.controlLock.lock()
            guard self.startGeneration == generation else {
                self.controlLock.unlock()
                return
            }
            _ = self.teardownLocked()
            self.lock.lock()
            let baseline = self.resetLevelLocked()
            self.lock.unlock()
            self.controlLock.unlock()

            self.reportLevel(0, generation: baseline.generation, sequence: baseline.sequence)
            self.beginRetries(generation: generation, attempt: 1, lastError: reason)
        }
    }

    private func reportPersistentConversionFailure(
        reason: String,
        originatingGeneration: UInt64,
        failureToken: UInt64? = nil
    ) {
        controlLock.lock()
        lock.lock()
        let isCurrent = (capturing || recovering)
            && originatingGeneration == levelGeneration
            && (failureToken == nil || failureToken == conversionFailureToken)
        guard isCurrent else {
            lock.unlock()
            controlLock.unlock()
            return
        }
        recovering = true
        startGeneration &+= 1
        let generation = startGeneration
        retryTask?.cancel()
        retryTask = nil
        lock.unlock()
        controlLock.unlock()

        controlQueue.async { [weak self] in
            guard let self else { return }
            self.controlLock.lock()
            guard self.startGeneration == generation else {
                self.controlLock.unlock()
                return
            }
            _ = self.teardownLocked()
            self.lock.lock()
            let baseline = self.resetLevelLocked()
            self.lock.unlock()
            self.controlLock.unlock()

            self.reportLevel(0, generation: baseline.generation, sequence: baseline.sequence)

            let current = self.withControlLock { () -> Bool in
                guard self.startGeneration == generation else { return false }
                self.recovering = false
                return true
            }
            guard current else { return }
            self.reportFailure(
                AppErrorMessage(
                    "conv_capture_failed",
                    logDetail: "Microphone audio conversion failed repeatedly: \(reason)"
                ),
                expectedStartGeneration: generation
            )
        }
    }

    /// Records a conversion failure and dispatches its recovery decision away
    /// from the tap thread. `AVAudioEngine.removeTap(onBus:)` synchronously
    /// flushes in-flight tap callbacks; if a callback took `controlLock` while
    /// the teardown path held it, the two would deadlock. The failure token
    /// makes the queued decision a no-op if a successful conversion or a newer
    /// failure lands before the control queue runs.
    private func dispatchConversionFailure(message: String, tapGeneration: UInt64) {
        lock.lock()
        let isCurrentGeneration = tapGeneration == levelGeneration
        let failureToken: UInt64
        if isCurrentGeneration {
            conversionErrors &+= 1
            conversionFailureStreak += 1
            conversionFailureToken &+= 1
            failureToken = conversionFailureToken
        } else {
            failureToken = 0
        }
        let streak = conversionFailureStreak
        lock.unlock()

        // A buffer that began converting before a mute/unmute/restart must not
        // drive recovery on the new capture generation.
        guard isCurrentGeneration else { return }
        controlQueue.async { [weak self] in
            guard let self else { return }
            if streak >= 3 {
                self.reportPersistentConversionFailure(
                    reason: message,
                    originatingGeneration: tapGeneration,
                    failureToken: failureToken
                )
            } else {
                self.requestConversionRecovery(
                    reason: "audio conversion failed: \(message)",
                    originatingGeneration: tapGeneration,
                    failureToken: failureToken
                )
            }
        }
    }

#if DEBUG
    /// Test seam: drive the same recovery path as a transient conversion failure.
    func simulateConversionFailure(_ reason: String = "injected conversion failure") {
        lock.lock()
        let generation = levelGeneration
        lock.unlock()
        requestConversionRecovery(
            reason: reason,
            originatingGeneration: generation
        )
    }

    /// Test seam: drive the same terminal path as repeated conversion failures.
    func simulatePersistentConversionFailure(_ reason: String = "injected conversion failure") {
        lock.lock()
        let generation = levelGeneration
        lock.unlock()
        reportPersistentConversionFailure(
            reason: reason,
            originatingGeneration: generation
        )
    }

    /// Test seam: drive the production tap-conversion-failure dispatch path
    /// without an AVAudioConverter.
    func simulateTapConversionFailureForTesting(
        message: String = "injected tap conversion failure"
    ) {
        lock.lock()
        let generation = levelGeneration
        lock.unlock()
        dispatchConversionFailure(message: message, tapGeneration: generation)
    }

    /// Test seam: mark a successful conversion for the current generation,
    /// invalidating any queued tap conversion-failure recovery.
    func simulateTapConversionSuccessForTesting() {
        lock.lock()
        conversionFailureStreak = 0
        conversionFailureToken &+= 1
        lock.unlock()
    }

    /// Test seam: inject a conversion error from an older generation.
    func simulateStaleConversionFailure(
        originatingGeneration: UInt64,
        reason: String = "injected stale conversion failure"
    ) {
        requestConversionRecovery(
            reason: reason,
            originatingGeneration: originatingGeneration
        )
    }

    /// Test seam: inject a persistent conversion error from an older generation.
    func simulateStalePersistentConversionFailure(
        originatingGeneration: UInt64,
        reason: String = "injected stale persistent conversion failure"
    ) {
        reportPersistentConversionFailure(
            reason: reason,
            originatingGeneration: originatingGeneration
        )
    }

    /// Test seam: drive the exact production `process` path with samples.
    ///
    /// Pass `tapGeneration` to simulate a buffer whose tap began converting
    /// before a generation bump; `nil` ignores the generation guard.
    func ingestSamplesForTesting(_ samples: [Int16], tapGeneration: UInt64? = nil) {
        process(samples, tapGeneration: tapGeneration)
    }

    /// Test seam: install the converter that `handleTap` uses when capture was
    /// started through `startAttemptOverride` (which leaves `converter` nil).
    @discardableResult
    func installTapConverterForTesting(inputFormat: AVAudioFormat) -> Bool {
        guard let converter = AVAudioConverter(from: inputFormat, to: targetFormat) else {
            return false
        }
        lock.lock()
        guard capturing else {
            lock.unlock()
            return false
        }
        self.converter = converter
        lock.unlock()
        return true
    }

    /// Test seam: drive the full production tap path, including the one-shot
    /// discard token, level-generation snapshot, `AVAudioConverter`, `process`,
    /// meter, and uplink. The buffer format must match the format passed to
    /// `installTapConverterForTesting`.
    func ingestTapBufferForTesting(_ buffer: AVAudioPCMBuffer) {
        handleTap(buffer)
    }

    /// Test seam: consume the one-shot post-unmute discard token exactly as
    /// the next `handleTap` invocation does.
    func consumeTapDiscardForTesting() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return takeTapDiscardTokenLocked()
    }

    func debugLevelState() -> (
        generation: UInt64,
        reportSequence: UInt64,
        lastApplied: UInt64,
        lastMeterLevel: Int
    ) {
        lock.lock()
        defer { lock.unlock() }
        return (
            levelGeneration,
            levelReportSequence,
            lastAppliedLevelReportSequence,
            lastMeterLevel
        )
    }
#endif

    // MARK: - Capture tap

    /// Caller must hold `lock`.
    private func takeTapDiscardTokenLocked() -> Bool {
        let discard = discardNextTapBuffer
        discardNextTapBuffer = false
        return discard
    }

    private func handleTap(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        guard capturing, let converter else {
            lock.unlock()
            return
        }
        tapCount &+= 1
        let tapGeneration = levelGeneration
        // Consume the token before conversion so it covers the whole callback,
        // including the converter latency. A stale in-flight tap consumed the
        // token state from before the edge and is dropped by its generation,
        // leaving the token armed for the next callback.
        let discard = takeTapDiscardTokenLocked()
        lock.unlock()

        guard !discard else { return }

        // A route change can hand the tap a buffer whose format is not the
        // validated install-time format. Guard the division before converting
        // to AVAudioFrameCount, which traps on inf/NaN.
        let inputRate = buffer.format.sampleRate
        let targetRate = targetFormat.sampleRate
        guard inputRate > 0, targetRate > 0, buffer.frameLength > 0 else {
            lock.lock()
            if tapGeneration == levelGeneration {
                emptyConversions &+= 1
            }
            lock.unlock()
            return
        }
        let ratio = targetRate / inputRate
        let maxCapacity = Double(UInt32.max - 64)
        let capacityValue = min(maxCapacity, Double(buffer.frameLength) * ratio)
        let capacity = AVAudioFrameCount(capacityValue) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else {
            reportFailure(
                AppErrorMessage(
                    "conv_capture_failed",
                    logDetail: "Unable to allocate audio conversion buffer"
                ),
                expectedLevelGeneration: tapGeneration
            )
            return
        }

        var providedInput = false
        var conversionError: NSError?
        let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
            if providedInput {
                inputStatus.pointee = .noDataNow
                return nil
            }
            providedInput = true
            inputStatus.pointee = .haveData
            return buffer
        }

        if status == .error {
            let message = conversionError?.localizedDescription ?? "unknown conversion error"
            AppLog.error("AudioCapture", "conversion failed: \(message)")
            dispatchConversionFailure(message: message, tapGeneration: tapGeneration)
            return
        }
        guard output.frameLength > 0, let channelData = output.int16ChannelData?[0] else {
            lock.lock()
            if tapGeneration == levelGeneration {
                emptyConversions &+= 1
            }
            lock.unlock()
            return
        }

        let samples = Array(
            UnsafeBufferPointer(start: channelData, count: Int(output.frameLength))
        )
        lock.lock()
        let isCurrentGeneration = tapGeneration == levelGeneration
        if isCurrentGeneration {
            convertedSamples &+= UInt64(samples.count)
            // A stale success must not clear the replacement capture's
            // consecutive-failure streak. A current success invalidates any
            // conversion-failure recovery still queued on the control queue.
            conversionFailureStreak = 0
            conversionFailureToken &+= 1
        }
        lock.unlock()
        guard isCurrentGeneration else { return }
        process(samples, tapGeneration: tapGeneration)
    }

    private func process(_ samples: [Int16], tapGeneration: UInt64? = nil) {
        let now = uptime()
        lock.lock()
        guard capturing else {
            lock.unlock()
            return
        }
        // A tap buffer that began converting before a generation bump (mute,
        // unmute, stop, restart, recovery) may contain audio captured under
        // the old state. Drop it instead of appending it to the freshly reset
        // chunker and uplinking it as real PCM.
        if let tapGeneration, tapGeneration != levelGeneration {
            lock.unlock()
            return
        }

        let muted = currentMuted
        let chunked = chunker.append(samples)
        var outgoing: [([Int16], Bool)] = []
        outgoing.reserveCapacity(chunked.count)
        var latestLevel = lastMeterLevel
        for frame in chunked {
            latestLevel = meter.update(samples: frame, muted: muted)
            switch gate.decide(muted: muted) {
            case .sendReal:
                outgoing.append((frame, false))
            case .sendSilence:
                outgoing.append((zeroFrame, true))
            case .drop:
                break
            }
        }
        if !chunked.isEmpty {
            lastMeterLevel = latestLevel
        }

        let shouldReportLevel = !chunked.isEmpty && now - lastLevelReport >= 0.05
        if shouldReportLevel {
            lastLevelReport = now
        }
        let generation = levelGeneration
        lock.unlock()

        if !outgoing.isEmpty {
            beforeUplink()
            uplinkLock.lock()
            // A mute/stop/restart may have completed between `lock.unlock()`
            // and here. Deliver only if the generation is still current; an
            // in-flight delivery holds `uplinkLock`, so the reset waits for
            // it instead of letting it escape afterwards.
            if generation == uplinkGeneration {
                for (frame, isSilence) in outgoing {
                    uplink(frame, isSilence)
                }
            }
            uplinkLock.unlock()
        }
        if shouldReportLevel {
            reportLevel(latestLevel, generation: generation)
        }
    }

    /// Caller must hold `lock`.
    ///
    /// The zero baseline's report sequence is reserved in the same critical
    /// section as the generation bump, so any level report carrying the new
    /// generation necessarily receives a higher sequence. Otherwise a tap
    /// thread that processes a chunk between the generation bump and the
    /// baseline's `reportLevel` call could be rejected or overwritten by the
    /// baseline (for example immediately after unmute).
    private func resetLevelLocked() -> (generation: UInt64, sequence: UInt64) {
        levelGeneration &+= 1
        meter.reset()
        lastMeterLevel = 0
        levelReportSequence &+= 1
        // Invalidate old real-PCM deliveries in the same critical section as
        // the generation bump. If an uplink is in flight, wait for it; if one
        // is about to start, it will observe the new generation and drop.
        uplinkLock.lock()
        uplinkGeneration = levelGeneration
        uplinkLock.unlock()
        return (levelGeneration, levelReportSequence)
    }

    private func reportLevel(_ level: Int, generation: UInt64, sequence: UInt64? = nil) {
        let reportSequence: UInt64
        if let sequence {
            reportSequence = sequence
        } else {
            lock.lock()
            levelReportSequence &+= 1
            reportSequence = levelReportSequence
            lock.unlock()
        }

        let work: @MainActor @Sendable () -> Void = { [weak self] in
            self?.applyLevelReport(level, generation: generation, sequence: reportSequence)
        }
        levelDelivery(work)
    }

    @MainActor
    private func applyLevelReport(_ level: Int, generation: UInt64, sequence: UInt64) {
        lock.lock()
        guard generation == levelGeneration,
              sequence > lastAppliedLevelReportSequence else {
            lock.unlock()
            return
        }
        lastAppliedLevelReportSequence = sequence
        lock.unlock()
        onLevel?(level)
    }

    private func isLevelGenerationCurrent(_ generation: UInt64) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return levelGeneration == generation
    }

    private func reportFailure(
        _ error: AppErrorMessage,
        expectedStartGeneration: UInt64? = nil,
        expectedLevelGeneration: UInt64? = nil
    ) {
        AppLog.error("AudioCapture", "capture failure: \(error.logDetail)")
        Task { @MainActor [weak self] in
            guard let self, let onFailure = self.onFailure else { return }
            // A stale retry/recovery task must not tear down a newer start
            // that raced the terminal check.
            if let expectedStartGeneration {
                let current = self.withControlLock {
                    self.startGeneration == expectedStartGeneration
                }
                guard current else { return }
            }
            // Allocation failures carry only the level generation; a stale
            // report must not tear down a replacement capture.
            if let expectedLevelGeneration,
               !self.isLevelGenerationCurrent(expectedLevelGeneration) {
                return
            }
            onFailure(error)
        }
    }
}
