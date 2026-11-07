import AVFoundation
import LivaChatSDK
import XCTest
@testable import LivaChatApp

private final class FakePlaybackNode: PlaybackNode, @unchecked Sendable {
    private let lock = NSLock()
    private var storedVolume: Float = 1
    private var storedIsPlaying = false
    private var completions: [@Sendable () -> Void] = []
    private var scheduleCalls = 0
    private var playCalls = 0

    var volume: Float {
        get {
            lock.lock()
            defer { lock.unlock() }
            return storedVolume
        }
        set {
            lock.lock()
            storedVolume = newValue
            lock.unlock()
        }
    }

    var isPlaying: Bool {
        lock.lock()
        defer { lock.unlock() }
        return storedIsPlaying
    }

    var scheduleCallCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return scheduleCalls
    }

    var playCallCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return playCalls
    }

    var outstandingCompletionCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return completions.count
    }

    func play() {
        lock.lock()
        playCalls += 1
        storedIsPlaying = true
        lock.unlock()
    }

    func stop() {
        lock.lock()
        storedIsPlaying = false
        lock.unlock()
    }

    func schedule(_ buffer: AVAudioPCMBuffer, completion: @escaping @Sendable () -> Void) {
        lock.lock()
        scheduleCalls += 1
        completions.append(completion)
        lock.unlock()
    }

    func fireAllCompletions() {
        lock.lock()
        let pending = completions
        completions.removeAll()
        lock.unlock()
        pending.forEach { $0() }
    }
}

private final class EngineRunningFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var storedValue: Bool

    init(_ value: Bool) { storedValue = value }

    var isRunning: Bool {
        get {
            lock.lock()
            defer { lock.unlock() }
            return storedValue
        }
        set {
            lock.lock()
            storedValue = newValue
            lock.unlock()
        }
    }
}

final class AudioPlaybackControllerTests: XCTestCase {
    private func makePlayback(
        _ node: FakePlaybackNode,
        engineRunning: @escaping () -> Bool = { true }
    ) -> AudioPlaybackController {
        AudioPlaybackController(
            playerNode: node,
            pumpTimerEnabled: false,
            engineIsRunning: engineRunning
        )
    }

    private let samples = Array(repeating: Int16(1_000), count: 320)

    func testMutedVolumeAndFlushDropQueuedFrames() {
        let node = FakePlaybackNode()
        let playback = AudioPlaybackController(playerNode: node)

        playback.setMuted(true)
        XCTAssertEqual(node.volume, 0)
        playback.setMuted(false)
        XCTAssertEqual(node.volume, 1)

        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        XCTAssertEqual(playback.stats().buffered, 2)

        playback.flush(reason: .interruptAck, epoch: 2)
        XCTAssertEqual(playback.stats().buffered, 0)
    }

    func testSinkPushIsNonBlockingWithoutStart() throws {
        let node = FakePlaybackNode()
        let playback = AudioPlaybackController(playerNode: node)
        let config = LivaChatConfig(
            serverURL: try XCTUnwrap(URL(string: "wss://example.com/v1/ws/stream")),
            token: "token",
            deviceID: UUID().uuidString,
            clientVersion: "ios-1.0.0",
            enableVideo: false
        )
        let sdk = try LivaChatSDK(config: config, audioSink: playback)
        let frame = AudioPCMFrame(
            samples: samples,
            sampleRate: 16_000,
            sampleBits: 16,
            durationMs: 20,
            sequenceNumber: 1,
            playbackEpoch: 1,
            turnID: ""
        )

        playback.livaChat(sdk, didReceiveAudio: frame)
        XCTAssertEqual(playback.stats().buffered, 1)
    }

    func testHasBufferedOrScheduledAudioTracksJitterAndPending() {
        let node = FakePlaybackNode()
        let playback = makePlayback(node)

        playback.start()
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        XCTAssertTrue(playback.hasBufferedOrScheduledAudio)

        playback.pumpNowForTesting()
        XCTAssertEqual(playback.stats().buffered, 1)
        XCTAssertEqual(playback.stats().pending, 2)

        node.fireAllCompletions()
        XCTAssertEqual(playback.stats().pending, 0)
        XCTAssertEqual(playback.stats().buffered, 1)
        XCTAssertTrue(playback.hasBufferedOrScheduledAudio)

        playback.pumpNowForTesting()
        XCTAssertEqual(playback.stats().pending, 1)
        node.fireAllCompletions()
        XCTAssertFalse(playback.hasBufferedOrScheduledAudio)
    }

    func testTailBypassPlaysFewerThanTargetFramesWhenInputEnded() {
        let node = FakePlaybackNode()
        let playback = makePlayback(node)
        playback.start()
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)

        playback.pumpNowForTesting()
        XCTAssertEqual(node.scheduleCallCount, 0, "two frames must not pass the 3-frame priming threshold")

        playback.setTTSInputExpected(false)
        XCTAssertEqual(node.scheduleCallCount, 2)
        XCTAssertEqual(playback.stats().pending, 2)
    }

    func testNewUtteranceReprimesAfterTailDrain() {
        let node = FakePlaybackNode()
        let playback = makePlayback(node)
        playback.start()
        playback.pushTestSamples(samples)
        playback.setTTSInputExpected(false)
        XCTAssertEqual(node.scheduleCallCount, 1)
        node.fireAllCompletions()
        XCTAssertFalse(playback.hasBufferedOrScheduledAudio)

        playback.setTTSInputExpected(true)
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        playback.pumpNowForTesting()
        XCTAssertEqual(node.scheduleCallCount, 1, "a new utterance must wait for targetFrames again")

        playback.pushTestSamples(samples)
        playback.pumpNowForTesting()
        XCTAssertEqual(node.scheduleCallCount, 3)
    }

    func testActivityCallbackFiresOnPushWhileStopped() async {
        let playback = makePlayback(FakePlaybackNode())
        let activity = expectation(description: "activity")
        playback.onActivityChange = {
            XCTAssertTrue(Thread.isMainThread)
            activity.fulfill()
        }

        playback.pushTestSamples(samples)
        await fulfillment(of: [activity], timeout: 1)
        XCTAssertTrue(playback.hasBufferedOrScheduledAudio)
    }

    func testActivityCallbackFiresOnDrain() async {
        let node = FakePlaybackNode()
        let playback = makePlayback(node)
        playback.start()
        playback.pushTestSamples(samples)
        playback.setTTSInputExpected(false)

        let activity = expectation(description: "drain")
        playback.onActivityChange = {
            XCTAssertTrue(Thread.isMainThread)
            activity.fulfill()
        }

        node.fireAllCompletions()
        await fulfillment(of: [activity], timeout: 1)
        XCTAssertFalse(playback.hasBufferedOrScheduledAudio)
    }

    func testResumePreservingBufferDoesNotResetJitter() {
        let node = FakePlaybackNode()
        let playback = makePlayback(node)
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        XCTAssertTrue(playback.hasBufferedOrScheduledAudio)

        playback.resumePreservingBuffer()
        XCTAssertTrue(playback.isRunning)
        XCTAssertEqual(playback.stats().buffered, 3)

        playback.pumpNowForTesting()
        XCTAssertEqual(playback.stats().buffered, 1)
        XCTAssertEqual(playback.stats().pending, 2)
    }

    func testPrepareForAudioReconfigurationPreservesJitterAndDropsScheduled() {
        let node = FakePlaybackNode()
        let playback = makePlayback(node)
        playback.start()
        for _ in 0..<6 {
            playback.pushTestSamples(samples)
        }
        playback.pumpNowForTesting()
        XCTAssertEqual(playback.stats().pending, 2)
        XCTAssertEqual(playback.stats().buffered, 4)

        playback.prepareForAudioReconfiguration()
        XCTAssertFalse(playback.isRunning)
        XCTAssertEqual(playback.stats().pending, 0)
        XCTAssertEqual(playback.stats().buffered, 4)

        node.fireAllCompletions()
        XCTAssertEqual(playback.stats().pending, 0, "stale completions must not revive pending count")

        playback.resumePreservingBuffer()
        XCTAssertTrue(playback.isRunning)
        playback.pumpNowForTesting()
        XCTAssertEqual(playback.stats().pending, 2)
    }

    func testStopIsUnconditionalAndInvalidatesCompletions() {
        let node = FakePlaybackNode()
        let playback = makePlayback(node)
        playback.start()
        playback.pushTestSamples(samples)
        playback.setTTSInputExpected(false)
        XCTAssertEqual(playback.stats().pending, 1)

        playback.stop()
        XCTAssertFalse(playback.isRunning)
        XCTAssertEqual(playback.stats().pending, 0)
        XCTAssertFalse(playback.hasBufferedOrScheduledAudio)

        node.fireAllCompletions()
        XCTAssertEqual(playback.stats().pending, 0)

        playback.stop()
        XCTAssertEqual(playback.stats().pending, 0)
    }

    func testStopClearsFramesBufferedWhileStopped() {
        let playback = makePlayback(FakePlaybackNode())
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        XCTAssertTrue(playback.hasBufferedOrScheduledAudio)

        playback.stop()
        XCTAssertFalse(playback.hasBufferedOrScheduledAudio)
    }

    func testStartRemainsDestructiveFresh() {
        let playback = makePlayback(FakePlaybackNode())
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        XCTAssertTrue(playback.hasBufferedOrScheduledAudio)

        playback.start()
        XCTAssertTrue(playback.isRunning)
        XCTAssertFalse(playback.hasBufferedOrScheduledAudio)
    }

    func testEngineStopDefersSchedulingUntilEngineReturns() {
        let node = FakePlaybackNode()
        let engine = EngineRunningFlag(false)
        let playback = makePlayback(node, engineRunning: { engine.isRunning })
        playback.start()
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)
        playback.pushTestSamples(samples)

        playback.pumpNowForTesting()
        XCTAssertEqual(node.scheduleCallCount, 0, "must not schedule into a stopped engine")
        XCTAssertEqual(playback.stats().buffered, 3)
        XCTAssertEqual(node.playCallCount, 0)

        engine.isRunning = true
        playback.pumpNowForTesting()
        XCTAssertEqual(node.scheduleCallCount, 2)
        XCTAssertEqual(playback.stats().buffered, 1)
        XCTAssertEqual(node.playCallCount, 1)
    }

    func testEngineReturnRetriesPlayAndInvalidatesStalePending() {
        let node = FakePlaybackNode()
        let engine = EngineRunningFlag(true)
        let playback = makePlayback(node, engineRunning: { engine.isRunning })
        playback.start()
        playback.pushTestSamples(samples)
        playback.setTTSInputExpected(false)
        XCTAssertEqual(playback.stats().pending, 1)
        XCTAssertEqual(node.playCallCount, 1)

        engine.isRunning = false
        playback.pumpNowForTesting()
        XCTAssertEqual(playback.stats().pending, 1)

        engine.isRunning = true
        playback.pumpNowForTesting()
        XCTAssertEqual(node.playCallCount, 2, "play must be retried when the engine returns")
        XCTAssertEqual(playback.stats().pending, 0, "completions lost while stopped must not latch")
        XCTAssertFalse(playback.hasBufferedOrScheduledAudio)

        node.fireAllCompletions()
        XCTAssertEqual(playback.stats().pending, 0)
    }

    func testFlushInvalidatesPendingCompletions() {
        let node = FakePlaybackNode()
        let playback = makePlayback(node)
        playback.start()
        for _ in 0..<3 {
            playback.pushTestSamples(samples)
        }
        playback.pumpNowForTesting()
        XCTAssertEqual(playback.stats().pending, 2)

        playback.flush(reason: .sessionEnded, epoch: 2)
        XCTAssertEqual(playback.stats().pending, 0)
        XCTAssertEqual(playback.stats().buffered, 0)

        node.fireAllCompletions()
        XCTAssertEqual(playback.stats().pending, 0)
    }
}

// MARK: - Audio session configuration

@MainActor
private final class FakeSessionBacking: AudioSessionBacking {
    var category: AVAudioSession.Category
    var mode: AVAudioSession.Mode
    var categoryOptions: AVAudioSession.CategoryOptions
    private(set) var active: Bool
    private(set) var setCategoryCalls: [String] = []
    private(set) var setActiveCalls: [Bool] = []
    var failSetCategoryCallNumbers: Set<Int> = []

    init(
        category: AVAudioSession.Category = .soloAmbient,
        mode: AVAudioSession.Mode = .default,
        options: AVAudioSession.CategoryOptions = [],
        active: Bool = false
    ) {
        self.category = category
        self.mode = mode
        self.categoryOptions = options
        self.active = active
    }

    func setCategory(
        _ category: AVAudioSession.Category,
        mode: AVAudioSession.Mode,
        options: AVAudioSession.CategoryOptions
    ) throws {
        setCategoryCalls.append(category.rawValue)
        if failSetCategoryCallNumbers.contains(setCategoryCalls.count) {
            throw NSError(domain: "FakeSessionBacking", code: setCategoryCalls.count)
        }
        self.category = category
        self.mode = mode
        self.categoryOptions = options
    }

    func setActive(_ active: Bool, options: AVAudioSession.SetActiveOptions) throws {
        setActiveCalls.append(active)
        self.active = active
    }
}

@MainActor
private final class FakeVoiceProcessingBacking: VoiceProcessingBacking {
    var isEnabled: Bool
    private(set) var setEnabledCalls: [Bool] = []
    var failNextDisable = false

    init(enabled: Bool) {
        isEnabled = enabled
    }

    func setEnabled(_ enabled: Bool) throws {
        if !enabled, failNextDisable {
            failNextDisable = false
            throw NSError(domain: "FakeVoiceProcessingBacking", code: 1)
        }
        setEnabledCalls.append(enabled)
        isEnabled = enabled
    }
}

@MainActor
final class AudioSessionControllerConfigurationTests: XCTestCase {
    private func makeController(
        session: FakeSessionBacking,
        voiceProcessing: FakeVoiceProcessingBacking,
        engineRunning: Bool = false
    ) -> AudioSessionController {
        AudioSessionController(
            sessionBacking: session,
            voiceProcessingBacking: voiceProcessing,
            engineRunningOverride: { engineRunning }
        )
    }

    func testVoiceDescriptorMatchesCurrentBehavior() {
        let descriptor = AudioSessionController.descriptor(for: .voice)
        XCTAssertEqual(descriptor.category, .playAndRecord)
        XCTAssertEqual(descriptor.mode, .voiceChat)
        XCTAssertTrue(descriptor.options.contains(.defaultToSpeaker))
        XCTAssertTrue(descriptor.options.contains(.allowBluetoothHFP))
        XCTAssertTrue(descriptor.options.contains(.allowBluetoothA2DP))
        XCTAssertTrue(descriptor.wantsVoiceProcessing)
    }

    func testOriginalUsesMeasurementAndDisablesVoiceProcessing() {
        let descriptor = AudioSessionController.descriptor(for: .original)
        XCTAssertEqual(descriptor.category, .playAndRecord)
        XCTAssertEqual(descriptor.mode, .measurement)
        XCTAssertFalse(descriptor.wantsVoiceProcessing)
    }

    func testActivateAppliesVoiceConfiguration() throws {
        let session = FakeSessionBacking()
        let voiceProcessing = FakeVoiceProcessingBacking(enabled: false)
        let controller = makeController(session: session, voiceProcessing: voiceProcessing)

        try controller.activate(captureSource: .voice)

        XCTAssertEqual(controller.appliedCaptureSource, .voice)
        XCTAssertEqual(controller.lastApplyPath, .inPlace)
        XCTAssertEqual(session.category, .playAndRecord)
        XCTAssertEqual(session.mode, .voiceChat)
        XCTAssertTrue(session.active)
        XCTAssertTrue(voiceProcessing.isEnabled)
        XCTAssertTrue(controller.voiceProcessingEnabled)
    }

    func testActivateNoOpWhenAlreadyApplied() throws {
        let session = FakeSessionBacking()
        let voiceProcessing = FakeVoiceProcessingBacking(enabled: false)
        let controller = makeController(session: session, voiceProcessing: voiceProcessing)

        try controller.activate(captureSource: .voice)
        let calls = session.setCategoryCalls.count
        try controller.activate(captureSource: .voice)

        XCTAssertEqual(controller.lastApplyPath, .noChange)
        XCTAssertEqual(session.setCategoryCalls.count, calls)
    }

    func testActivateRequiresStoppedEngineForVoiceProcessingChange() throws {
        let session = FakeSessionBacking(
            category: .playAndRecord,
            mode: .voiceChat,
            options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP],
            active: true
        )
        let voiceProcessing = FakeVoiceProcessingBacking(enabled: true)
        let controller = makeController(
            session: session,
            voiceProcessing: voiceProcessing,
            engineRunning: true
        )
        let categoryBefore = session.category
        let callsBefore = session.setCategoryCalls.count

        XCTAssertThrowsError(try controller.activate(captureSource: .original)) { error in
            XCTAssertTrue(error is AudioSessionControllerError)
        }
        XCTAssertEqual(session.category, categoryBefore)
        XCTAssertEqual(session.setCategoryCalls.count, callsBefore)
    }

    func testVoiceProcessingDisableFailureAbortsSourceSwitch() throws {
        let session = FakeSessionBacking()
        let voiceProcessing = FakeVoiceProcessingBacking(enabled: false)
        let controller = makeController(session: session, voiceProcessing: voiceProcessing)

        try controller.activate(captureSource: .voice)
        voiceProcessing.failNextDisable = true

        XCTAssertThrowsError(try controller.activate(captureSource: .original))
        XCTAssertEqual(session.category, .playAndRecord)
        XCTAssertEqual(controller.appliedCaptureSource, .voice)
        XCTAssertTrue(voiceProcessing.isEnabled)
    }

    func testCategoryFailureFallsBackToReactivation() throws {
        let session = FakeSessionBacking()
        let voiceProcessing = FakeVoiceProcessingBacking(enabled: false)
        let controller = makeController(session: session, voiceProcessing: voiceProcessing)

        try controller.activate(captureSource: .original)
        session.failSetCategoryCallNumbers = [session.setCategoryCalls.count + 1]

        try controller.activate(captureSource: .voice)
        XCTAssertEqual(controller.lastApplyPath, .reactivated)
        XCTAssertEqual(session.category, .playAndRecord)
        XCTAssertEqual(session.mode, .voiceChat)
        XCTAssertTrue(session.active)
    }

    func testReactivationFailureRollsBackPreviousConfiguration() throws {
        let session = FakeSessionBacking()
        let voiceProcessing = FakeVoiceProcessingBacking(enabled: false)
        let controller = makeController(session: session, voiceProcessing: voiceProcessing)

        try controller.activate(captureSource: .original)
        let firstApplyCall = session.setCategoryCalls.count + 1
        session.failSetCategoryCallNumbers = [firstApplyCall, firstApplyCall + 1]

        XCTAssertThrowsError(try controller.activate(captureSource: .voice))
        XCTAssertEqual(session.category, .playAndRecord)
        XCTAssertEqual(session.mode, .measurement)
        XCTAssertEqual(controller.appliedCaptureSource, .original)
        XCTAssertTrue(session.active)
    }

    func testDeactivateClearsAppliedCaptureSource() throws {
        let session = FakeSessionBacking()
        let voiceProcessing = FakeVoiceProcessingBacking(enabled: false)
        let controller = makeController(session: session, voiceProcessing: voiceProcessing)

        try controller.activate(captureSource: .voice)
        controller.deactivate()
        XCTAssertNil(controller.appliedCaptureSource)
        XCTAssertFalse(session.active)
    }
}
