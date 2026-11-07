import AVFoundation
import XCTest
@testable import LivaChatApp

private final class LevelClock: @unchecked Sendable {
    private let lock = NSLock()
    private var value: TimeInterval

    init(_ value: TimeInterval = 100) {
        self.value = value
    }

    var now: TimeInterval {
        lock.lock()
        defer { lock.unlock() }
        return value
    }

    func advance(_ delta: TimeInterval = 0.1) {
        lock.lock()
        value += delta
        lock.unlock()
    }
}

final class LevelDeliveryBox: @unchecked Sendable {
    private let lock = NSLock()
    private var actions: [@MainActor @Sendable () -> Void] = []

    func deliver(_ action: @escaping @MainActor @Sendable () -> Void) {
        lock.lock()
        actions.append(action)
        lock.unlock()
    }

    func takeAll() -> [@MainActor @Sendable () -> Void] {
        lock.lock()
        let result = actions
        actions.removeAll()
        lock.unlock()
        return result
    }
}

private final class LevelFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var stored = false

    var value: Bool {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }

    func set(_ value: Bool) {
        lock.lock()
        stored = value
        lock.unlock()
    }
}

private final class UplinkBox: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [([Int16], Bool)] = []

    func append(_ samples: [Int16], _ muted: Bool) {
        lock.lock()
        storage.append((samples, muted))
        lock.unlock()
    }

    var frames: [([Int16], Bool)] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}

/// `AVAudioPCMBuffer` is not Sendable; the box only carries a pre-built
/// buffer to the paused tap thread while the test waits for that call to end.
private final class TapBufferBox: @unchecked Sendable {
    let buffer: AVAudioPCMBuffer

    init(_ buffer: AVAudioPCMBuffer) {
        self.buffer = buffer
    }
}

@MainActor
final class AudioCaptureLevelTests: XCTestCase {
    private var levels: [Int] = []
    private var clock = LevelClock()
    private var delivery = LevelDeliveryBox()
    private var uplink = UplinkBox()
    private var controller: AudioCaptureController!

    private func makeController(
        beforeUplink: @escaping @Sendable () -> Void = {},
        beforeStopBaseline: @escaping @Sendable () -> Void = {}
    ) {
        levels = []
        clock = LevelClock()
        delivery = LevelDeliveryBox()
        uplink = UplinkBox()
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            retryDelaysNanoseconds: [0],
            retrySleep: { _ in },
            controlQueue: DispatchQueue(label: "AudioCaptureLevelTests"),
            uplink: { [uplink] samples, muted in
                uplink.append(samples, muted)
            },
            uptime: { [clock] in clock.now },
            levelDelivery: { [delivery] action in
                delivery.deliver(action)
            },
            beforeUplink: beforeUplink,
            beforeStopBaseline: beforeStopBaseline
        )
        controller.onLevel = { [weak self] level in
            self?.levels.append(level)
        }
        self.controller = controller
    }

    private func drain() {
        for action in delivery.takeAll() {
            action()
        }
    }

    private func start() throws {
        try controller.start()
        drain()
    }

    private func shortFrame(amplitude: Int16 = 32_000) -> [Int16] {
        Array(repeating: amplitude, count: 160)
    }

    private func frame(amplitude: Int16) -> [Int16] {
        Array(repeating: amplitude, count: 320)
    }

    private func fullFrame() -> [Int16] {
        (0..<320).map { $0.isMultiple(of: 2) ? 32_767 : -32_768 }
    }

    private func silenceFrame() -> [Int16] {
        Array(repeating: 0, count: 320)
    }

    private func installTapConverter() -> AVAudioFormat {
        let format = AudioFormats.capture
        XCTAssertTrue(controller.installTapConverterForTesting(inputFormat: format))
        return format
    }

    private func tapBuffer(_ format: AVAudioFormat, samples: [Int16]) -> AVAudioPCMBuffer {
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: format,
            frameCapacity: AVAudioFrameCount(samples.count)
        ) else {
            fatalError("unable to allocate tap buffer")
        }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        for (index, sample) in samples.enumerated() {
            buffer.int16ChannelData![0][index] = sample
        }
        return buffer
    }

    func testShortTapDefersMeterUntilChunkCompletes() throws {
        makeController()
        try start()
        XCTAssertEqual(levels, [0])

        clock.advance()
        controller.ingestSamplesForTesting(shortFrame())
        drain()
        XCTAssertEqual(levels, [0], "no complete chunk must not update the meter")

        controller.ingestSamplesForTesting(shortFrame())
        drain()
        XCTAssertEqual(levels.last, 4)
    }

    func testMultipleChunksUseLastLevel() throws {
        makeController()
        try start()
        clock.advance()

        controller.ingestSamplesForTesting(fullFrame() + silenceFrame())
        drain()

        XCTAssertEqual(levels.last, 3, "the last 320-sample chunk drives the report")
    }

    func testZeroSampleRateTapBufferIsIgnored() throws {
        makeController()
        try start()
        _ = installTapConverter()

        let format = try XCTUnwrap(
            AVAudioFormat(standardFormatWithSampleRate: 0, channels: 1)
        )
        let buffer = try XCTUnwrap(
            AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1_024)
        )
        buffer.frameLength = 1_024

        controller.ingestTapBufferForTesting(buffer)

        let stats = controller.debugStats()
        XCTAssertEqual(stats.empty, 1)
        XCTAssertEqual(stats.samples, 0)
        XCTAssertTrue(uplink.frames.isEmpty)
    }

    func testLevelReportsAreThrottledToFiftyMilliseconds() throws {
        makeController()
        try start()
        clock.advance(0.01)

        controller.ingestSamplesForTesting(fullFrame())
        drain()
        XCTAssertEqual(levels.last, 4, "first report after the throttle window is emitted")

        clock.advance(0.02)
        controller.ingestSamplesForTesting(silenceFrame())
        drain()
        XCTAssertEqual(levels, [0, 4], "a report inside the 50 ms window must be suppressed")

        clock.advance(0.04)
        controller.ingestSamplesForTesting(silenceFrame())
        drain()
        XCTAssertEqual(levels.last, 3, "the next report is emitted once 50 ms have elapsed")
    }

    func testMuteReportsZeroAndDropsLateNonzero() throws {
        makeController()
        try start()
        clock.advance()
        controller.ingestSamplesForTesting(fullFrame())
        controller.setMuted(true)

        drain()

        XCTAssertFalse(levels.contains(4), "old-generation nonzero must be dropped")
        XCTAssertEqual(levels, [0, 0], "mute must append a current-generation zero baseline")
    }

    func testSameGenerationOutOfOrderAppliesNewest() throws {
        makeController()
        try start()
        clock.advance()
        controller.ingestSamplesForTesting(fullFrame())
        clock.advance()
        controller.ingestSamplesForTesting(silenceFrame())

        let actions = delivery.takeAll()
        for action in actions.reversed() {
            action()
        }

        XCTAssertEqual(levels.last, 3, "sequence must reject the older report")
    }

    func testLateUnmuteBaselineDoesNotOverrideNewerLevel() throws {
        makeController()
        try start()
        controller.setMuted(true)
        drain()
        clock.advance()

        controller.setMuted(false)
        controller.ingestSamplesForTesting(fullFrame())

        let actions = delivery.takeAll()
        for action in actions.reversed() {
            action()
        }

        XCTAssertEqual(
            levels.last,
            4,
            "a newer same-generation level must survive a late zero baseline"
        )
    }

    func testStopAndRestartProduceBaselines() throws {
        makeController()
        try start()
        clock.advance()
        controller.ingestSamplesForTesting(fullFrame())
        drain()
        XCTAssertEqual(levels, [0, 4])

        controller.stop()
        drain()
        XCTAssertEqual(levels, [0, 4, 0], "stop must publish a current-generation zero baseline")

        try controller.start()
        drain()
        XCTAssertEqual(levels, [0, 4, 0, 0], "restart must publish a new-generation zero baseline")
        XCTAssertEqual(controller.debugLevelState().lastMeterLevel, 0)
    }

    func testRepeatedUnmuteDoesNotDropPendingSamples() throws {
        makeController()
        try start()
        clock.advance()

        controller.ingestSamplesForTesting(shortFrame())
        controller.setMuted(false) // already unmuted: must not reset the chunker
        controller.ingestSamplesForTesting(shortFrame())
        drain()

        XCTAssertEqual(levels.last, 4)
    }

    func testUnmuteDropsPreUnmutePendingSamples() throws {
        makeController()
        try start()
        let format = installTapConverter()
        controller.setMuted(true)
        drain()

        let preUnmute = Array(repeating: Int16(1_234), count: 100)
        controller.ingestTapBufferForTesting(tapBuffer(format, samples: preUnmute))
        controller.setMuted(false)

        let firstPostUnmute = Array(repeating: Int16(2_000), count: 320)
        controller.ingestTapBufferForTesting(tapBuffer(format, samples: firstPostUnmute))

        let secondPostUnmute = (0..<320).map { Int16(3_000 + $0 % 50) }
        controller.ingestTapBufferForTesting(tapBuffer(format, samples: secondPostUnmute))
        drain()

        XCTAssertEqual(
            uplink.frames.count,
            1,
            "only the second post-unmute tap may reach uplink"
        )
        let realFrames = uplink.frames.filter { !$0.1 }
        XCTAssertEqual(realFrames.last?.0, secondPostUnmute)
        XCTAssertFalse(
            realFrames.contains { $0.0.contains(1_234) },
            "pre-unmute pending must not be sent as real PCM"
        )
        XCTAssertFalse(
            realFrames.contains { $0.0.contains(2_000) },
            "the first post-unmute tap is consumed by the production discard token"
        )
    }

    func testStaleInFlightTapAndFirstPostUnmuteTapAreBothDropped() throws {
        let armed = LevelFlag()
        let paused = DispatchSemaphore(value: 0)
        let release = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        makeController(beforeUplink: {
            guard armed.value else { return }
            armed.set(false)
            paused.signal()
            release.wait()
        })
        try start()
        let format = installTapConverter()
        controller.setMuted(true)
        drain()
        let mutedGeneration = controller.debugLevelState().generation

        let inFlight = Array(repeating: Int16(4_000), count: 320)
        let inFlightBox = TapBufferBox(tapBuffer(format, samples: inFlight))
        let controller = self.controller!
        armed.set(true)
        DispatchQueue.global(qos: .userInitiated).async {
            controller.ingestTapBufferForTesting(inFlightBox.buffer)
            finished.signal()
        }
        XCTAssertEqual(paused.wait(timeout: .now() + 2), .success)

        controller.setMuted(false) // generation bump + one-shot discard token
        release.signal()
        XCTAssertEqual(finished.wait(timeout: .now() + 2), .success)

        let firstPostUnmute = Array(repeating: Int16(5_000), count: 320)
        controller.ingestTapBufferForTesting(tapBuffer(format, samples: firstPostUnmute))
        let secondPostUnmute = Array(repeating: Int16(6_000), count: 320)
        controller.ingestTapBufferForTesting(tapBuffer(format, samples: secondPostUnmute))
        drain()

        XCTAssertEqual(
            uplink.frames.count,
            1,
            "the stale in-flight tap's silence and the first post-unmute tap must both be dropped"
        )
        XCTAssertEqual(uplink.frames.last?.0, secondPostUnmute)
        XCTAssertEqual(
            controller.debugLevelState().generation,
            mutedGeneration + 1,
            "unmute must bump the generation exactly once"
        )
    }

    /// Process-level seam: isolates `process`'s staging generation filter.
    /// The full `handleTap` composition (token + conversion + process) is
    /// covered by `testStaleInFlightTapAndFirstPostUnmuteTapAreBothDropped`.
    func testInFlightTapBufferIsDroppedAfterUnmute() throws {
        makeController()
        try start()
        controller.setMuted(true)
        drain()
        let mutedGeneration = controller.debugLevelState().generation

        controller.setMuted(false)
        let preUnmute = Array(repeating: Int16(1_234), count: 320)
        controller.ingestSamplesForTesting(preUnmute, tapGeneration: mutedGeneration)
        drain()

        XCTAssertTrue(
            uplink.frames.isEmpty,
            "a buffer that began converting before unmute must not be uplinked as real PCM"
        )
        XCTAssertEqual(levels.last, 0, "the dropped buffer must not drive the meter")
    }

    /// Token-level seam: isolates one-shot arming semantics. Production
    /// consumption through `handleTap` is covered by the composition tests.
    func testUnmuteDiscardsOnlyFirstTapBuffer() throws {
        makeController()
        try start()
        controller.setMuted(true)

        controller.setMuted(false)
        XCTAssertTrue(controller.consumeTapDiscardForTesting(), "first buffer after unmute is discarded")
        XCTAssertFalse(controller.consumeTapDiscardForTesting(), "only the first buffer is discarded")

        controller.setMuted(false)
        XCTAssertFalse(
            controller.consumeTapDiscardForTesting(),
            "repeated unmute must not arm another discard"
        )
    }

    func testMuteInvalidatesFramesPausedBeforeUplink() {
        let paused = DispatchSemaphore(value: 0)
        let release = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        makeController(beforeUplink: {
            paused.signal()
            release.wait()
        })
        try? start()
        XCTAssertEqual(levels, [0])

        let frame = fullFrame()
        let controller = self.controller!
        DispatchQueue.global(qos: .userInitiated).async {
            controller.ingestSamplesForTesting(frame)
            finished.signal()
        }

        XCTAssertEqual(paused.wait(timeout: .now() + 2), .success)
        controller.setMuted(true)
        release.signal()
        XCTAssertEqual(finished.wait(timeout: .now() + 2), .success)

        XCTAssertTrue(
            uplink.frames.isEmpty,
            "real frames paused before uplink must not be sent after mute"
        )
    }

    func testStopAndRestartInvalidateFramesPausedBeforeUplink() throws {
        let paused = DispatchSemaphore(value: 0)
        let release = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        makeController(beforeUplink: {
            paused.signal()
            release.wait()
        })
        try start()

        let frame = fullFrame()
        let controller = self.controller!
        DispatchQueue.global(qos: .userInitiated).async {
            controller.ingestSamplesForTesting(frame)
            finished.signal()
        }

        XCTAssertEqual(paused.wait(timeout: .now() + 2), .success)
        controller.stop()
        try controller.start()
        release.signal()
        XCTAssertEqual(finished.wait(timeout: .now() + 2), .success)

        XCTAssertTrue(
            uplink.frames.isEmpty,
            "real frames paused before uplink must not survive stop/restart"
        )
    }

    func testOldNoOpStopZeroDoesNotOverwriteNewCaptureLevel() {
        let armed = LevelFlag()
        let paused = DispatchSemaphore(value: 0)
        let release = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        makeController(beforeStopBaseline: {
            guard armed.value else { return }
            paused.signal()
            release.wait()
        })

        try? start()
        controller.stop() // capturing: resets and publishes a baseline; hook not armed
        drain()
        armed.set(true)

        let controller = self.controller!
        DispatchQueue.global(qos: .userInitiated).async {
            controller.stop() // no-op stop: snapshot is taken, then the hook pauses
            finished.signal()
        }
        XCTAssertEqual(paused.wait(timeout: .now() + 2), .success)

        try? controller.start() // replacement capture with a new generation
        clock.advance()
        controller.ingestSamplesForTesting(fullFrame()) // schedules level 4

        release.signal()
        XCTAssertEqual(finished.wait(timeout: .now() + 2), .success)
        drain()

        XCTAssertEqual(
            levels.last,
            4,
            "an old no-op stop's zero must not overwrite the replacement capture's level"
        )
    }

    func testMutedWindowStillSendsFiftySilenceFrames() throws {
        makeController()
        try start()
        controller.setMuted(true)

        for _ in 0..<51 {
            controller.ingestSamplesForTesting(fullFrame())
        }
        drain()

        XCTAssertEqual(uplink.frames.count, SilenceFlushGate.windowFrames)
        XCTAssertTrue(uplink.frames.allSatisfy { $0.1 })
    }
}
