import AVFoundation
import XCTest
@testable import LivaChatApp

private final class LockedCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0

    func increment() {
        lock.lock()
        value += 1
        lock.unlock()
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

private final class LockedString: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: String?

    var value: String? {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }

    func set(_ value: String) {
        lock.lock()
        stored = value
        lock.unlock()
    }
}

private final class LockedFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: Bool

    init(_ value: Bool) {
        stored = value
    }

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

private final class RetryAttemptBox: @unchecked Sendable {
    private let lock = NSLock()
    private var failNext = false
    private var attemptCount = 0

    func failNextAttempt() {
        lock.lock()
        failNext = true
        lock.unlock()
    }

    func attempt() throws {
        lock.lock()
        attemptCount += 1
        let shouldFail = failNext
        failNext = false
        lock.unlock()

        if shouldFail {
            throw NSError(
                domain: "AudioCaptureRetryTests",
                code: 4,
                userInfo: [NSLocalizedDescriptionKey: "immediate retry injection"]
            )
        }
    }

    var attempts: Int {
        lock.lock()
        defer { lock.unlock() }
        return attemptCount
    }
}

private final class RetryLevelSink: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [Int] = []

    func append(_ value: Int) {
        lock.lock()
        storage.append(value)
        lock.unlock()
    }

    var levels: [Int] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}

private final class RetryGate: @unchecked Sendable {
    private let lock = NSLock()
    private var open = false
    private var waiters: [CheckedContinuation<Void, Error>] = []
    private var sleepCount = 0

    private func beginSleep() -> Bool {
        lock.lock()
        sleepCount += 1
        let alreadyOpen = open
        lock.unlock()
        return alreadyOpen
    }

    func sleep(_ nanoseconds: UInt64) async throws {
        if beginSleep() { return }

        try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            if open {
                lock.unlock()
                continuation.resume()
            } else {
                waiters.append(continuation)
                lock.unlock()
            }
        }
    }

    func releaseAll() {
        lock.lock()
        open = true
        let pending = waiters
        waiters.removeAll()
        lock.unlock()
        pending.forEach { $0.resume() }
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return sleepCount
    }
}

@MainActor
private func drainLevelDelivery(_ delivery: LevelDeliveryBox) {
    for action in delivery.takeAll() {
        action()
    }
}

final class AudioCaptureRetryTests: XCTestCase {
    private func waitForLifecycle(
        _ controller: AudioCaptureController,
        _ expected: CaptureLifecycleState,
        timeout: TimeInterval = 2
    ) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if controller.lifecycleState == expected { return true }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return controller.lifecycleState == expected
    }

    private func loudFrame() -> [Int16] {
        (0..<320).map { $0.isMultiple(of: 2) ? Int16(32_767) : Int16(-32_768) }
    }

    func testStartRetriesThreeTimesThenReportsFailure() async {
        let attempts = LockedCounter()
        let failure = LockedString()
        let reported = expectation(description: "onFailure")
        let gate = RetryGate()

        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {
                attempts.increment()
                throw NSError(
                    domain: "AudioCaptureRetryTests",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "injected failure"]
                )
            },
            retryDelaysNanoseconds: [0, 0, 0],
            retrySleep: { try await gate.sleep($0) },
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.control")
        ) { _, _ in }

        controller.onFailure = { message in
            failure.set(message.logDetail)
            reported.fulfill()
        }

        XCTAssertNoThrow(try controller.start())
        XCTAssertEqual(controller.lifecycleState, .recovering)
        gate.releaseAll()
        await fulfillment(of: [reported], timeout: 3)

        XCTAssertEqual(attempts.count, 4, "initial attempt + 3 retries")
        XCTAssertTrue(failure.value?.contains("after retries") == true)
        XCTAssertEqual(controller.lifecycleState, .stopped)
        XCTAssertEqual(gate.count, 3)
        controller.stop()
    }

    func testSuccessfulStartOverrideEndsRunning() throws {
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.control")
        ) { _, _ in }

        try controller.start()
        XCTAssertEqual(controller.lifecycleState, .running)

        controller.stop()
        XCTAssertEqual(controller.lifecycleState, .stopped)
        controller.stop()
        XCTAssertEqual(controller.lifecycleState, .stopped)
    }

    func testImmediateRetrySleepDoesNotLatchRecovering() async {
        let attempts = RetryAttemptBox()
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: { try attempts.attempt() },
            retryDelaysNanoseconds: [0],
            // No suspension: the retry task can reach its recovery path while
            // beginRetries is still publishing the task and `recovering`.
            retrySleep: { _ in },
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.immediate")
        ) { _, _ in }

        for iteration in 0..<100 {
            attempts.failNextAttempt()
            XCTAssertNoThrow(try controller.start())
            let running = await waitForLifecycle(controller, .running)
            XCTAssertTrue(
                running,
                "immediate retry \(iteration) must not latch .recovering "
                    + "(state is \(controller.lifecycleState))"
            )
            XCTAssertEqual(controller.lifecycleState, .running)

            controller.stop()
            XCTAssertEqual(controller.lifecycleState, .stopped)
        }

        XCTAssertEqual(attempts.attempts, 200, "initial attempt + immediate retry per cycle")
    }

    func testStaleConversionFailureDoesNotRecoverReplacementCapture() throws {
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.control")
        ) { _, _ in }

        try controller.start()
        let staleGeneration = controller.debugLevelState().generation
        controller.stop()
        try controller.start()
        let replacementGeneration = controller.debugLevelState().generation
        XCTAssertNotEqual(staleGeneration, replacementGeneration)
        XCTAssertEqual(controller.lifecycleState, .running)

        controller.simulateStaleConversionFailure(
            originatingGeneration: staleGeneration
        )

        XCTAssertEqual(controller.lifecycleState, .running)
        XCTAssertEqual(controller.debugLevelState().generation, replacementGeneration)
        controller.stop()
    }

    func testStalePersistentConversionFailureDoesNotTearDownReplacementCapture() throws {
        let failures = LockedCounter()
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.control")
        ) { _, _ in }
        controller.onFailure = { _ in failures.increment() }

        try controller.start()
        let staleGeneration = controller.debugLevelState().generation
        controller.stop()
        try controller.start()
        let replacementGeneration = controller.debugLevelState().generation

        controller.simulateStalePersistentConversionFailure(
            originatingGeneration: staleGeneration
        )

        XCTAssertEqual(controller.lifecycleState, .running)
        XCTAssertEqual(controller.debugLevelState().generation, replacementGeneration)
        XCTAssertEqual(failures.count, 0)
        controller.stop()
    }

    func testSuccessfulRetryPublishesZeroBaseline() async {
        let attempts = LockedCounter()
        let delivery = LevelDeliveryBox()
        let sink = RetryLevelSink()
        let gate = RetryGate()
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {
                attempts.increment()
                if attempts.count == 1 {
                    throw NSError(
                        domain: "AudioCaptureRetryTests",
                        code: 1,
                        userInfo: [NSLocalizedDescriptionKey: "injected failure"]
                    )
                }
            },
            retryDelaysNanoseconds: [0],
            retrySleep: { try await gate.sleep($0) },
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.control"),
            uplink: { _, _ in },
            uptime: { 100 },
            levelDelivery: { action in delivery.deliver(action) }
        )
        controller.onLevel = { level in sink.append(level) }

        XCTAssertNoThrow(try controller.start())
        XCTAssertEqual(controller.lifecycleState, .recovering)

        gate.releaseAll()
        let running = await waitForLifecycle(controller, .running)
        XCTAssertTrue(running)

        await drainLevelDelivery(delivery)
        XCTAssertEqual(controller.debugLevelState().lastMeterLevel, 0)
        XCTAssertEqual(sink.levels.last, 0)
        controller.stop()
    }

    func testRetryExhaustionPublishesZeroBaseline() async {
        let delivery = LevelDeliveryBox()
        let sink = RetryLevelSink()
        let reported = expectation(description: "onFailure")
        let gate = RetryGate()
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {
                throw NSError(
                    domain: "AudioCaptureRetryTests",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "injected failure"]
                )
            },
            retryDelaysNanoseconds: [0],
            retrySleep: { try await gate.sleep($0) },
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.control"),
            uplink: { _, _ in },
            uptime: { 100 },
            levelDelivery: { action in delivery.deliver(action) }
        )
        controller.onLevel = { level in sink.append(level) }
        controller.onFailure = { _ in reported.fulfill() }

        XCTAssertNoThrow(try controller.start())
        gate.releaseAll()
        await fulfillment(of: [reported], timeout: 3)

        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels.last, 0)
        XCTAssertEqual(controller.lifecycleState, .stopped)
        controller.stop()
    }

    func testStopWhileRecoveringCancelsRetryWithoutFailure() async throws {
        let gate = RetryGate()
        let failure = LockedString()
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {
                throw NSError(domain: "AudioCaptureRetryTests", code: 2)
            },
            retryDelaysNanoseconds: [0],
            retrySleep: { try await gate.sleep($0) },
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.control")
        ) { _, _ in }
        controller.onFailure = { message in failure.set(message.logDetail) }

        try controller.start()
        XCTAssertEqual(controller.lifecycleState, .recovering)

        controller.stop()
        XCTAssertEqual(controller.lifecycleState, .stopped)
        gate.releaseAll()
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertNil(failure.value)
        XCTAssertEqual(controller.lifecycleState, .stopped)
    }

    func testStartWhileRecoveringInvalidatesOldRetryGeneration() async throws {
        let gate = RetryGate()
        let failure = LockedString()
        let succeeds = LockedFlag(false)
        let attempts = LockedCounter()
        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {
                attempts.increment()
                if !succeeds.value {
                    throw NSError(domain: "AudioCaptureRetryTests", code: 3)
                }
            },
            retryDelaysNanoseconds: [0],
            retrySleep: { try await gate.sleep($0) },
            controlQueue: DispatchQueue(label: "AudioCaptureRetryTests.control")
        ) { _, _ in }
        controller.onFailure = { message in failure.set(message.logDetail) }

        try controller.start()
        XCTAssertEqual(controller.lifecycleState, .recovering)

        succeeds.set(true)
        try controller.start()
        XCTAssertEqual(controller.lifecycleState, .running)

        gate.releaseAll()
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertNil(failure.value)
        XCTAssertEqual(controller.lifecycleState, .running)
        controller.stop()
    }

    func testTapConversionFailureDispatchesOffTapThreadAndRecovers() async throws {
        let gate = RetryGate()
        let delivery = LevelDeliveryBox()
        let sink = RetryLevelSink()
        let controlQueue = DispatchQueue(label: "AudioCaptureRetryTests.tapFailure")
        let controlBlocked = DispatchSemaphore(value: 0)
        controlQueue.async { controlBlocked.wait() }

        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            retryDelaysNanoseconds: [0],
            retrySleep: { try await gate.sleep($0) },
            controlQueue: controlQueue,
            uplink: { _, _ in },
            uptime: { 100 },
            levelDelivery: { action in delivery.deliver(action) }
        )
        controller.onLevel = { level in sink.append(level) }

        try controller.start()
        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels, [0])

        controller.simulateTapConversionFailureForTesting()
        XCTAssertEqual(
            controller.lifecycleState,
            .running,
            "the tap path must not transition lifecycle state synchronously"
        )

        controlBlocked.signal()
        let recovering = await waitForLifecycle(controller, .recovering)
        XCTAssertTrue(recovering)
        gate.releaseAll()
        let running = await waitForLifecycle(controller, .running)
        XCTAssertTrue(running)

        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels.last, 0)
        controller.stop()
    }

    func testTapConversionSuccessInvalidatesQueuedFailure() async throws {
        let controlQueue = DispatchQueue(label: "AudioCaptureRetryTests.tapSuccess")
        let controlBlocked = DispatchSemaphore(value: 0)
        controlQueue.async { controlBlocked.wait() }
        let failure = LockedString()

        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            controlQueue: controlQueue,
            uplink: { _, _ in }
        )
        controller.onFailure = { message in failure.set(message.logDetail) }

        try controller.start()
        controller.simulateTapConversionFailureForTesting()
        controller.simulateTapConversionSuccessForTesting()
        XCTAssertEqual(controller.lifecycleState, .running)

        controlBlocked.signal()
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertEqual(
            controller.lifecycleState,
            .running,
            "a successful conversion must invalidate the queued failure"
        )
        XCTAssertNil(failure.value)
        controller.stop()
    }

    func testQueuedTapConversionFailuresTerminateOnThirdFailure() async throws {
        let controlQueue = DispatchQueue(label: "AudioCaptureRetryTests.tapPersistent")
        let controlBlocked = DispatchSemaphore(value: 0)
        controlQueue.async { controlBlocked.wait() }
        let failure = LockedString()
        let reported = expectation(description: "onFailure")

        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            controlQueue: controlQueue,
            uplink: { _, _ in }
        )
        controller.onFailure = { message in
            failure.set(message.logDetail)
            reported.fulfill()
        }

        try controller.start()
        controller.simulateTapConversionFailureForTesting()
        controller.simulateTapConversionFailureForTesting()
        controller.simulateTapConversionFailureForTesting()
        XCTAssertEqual(controller.lifecycleState, .running)

        controlBlocked.signal()
        await fulfillment(of: [reported], timeout: 3)

        XCTAssertEqual(controller.lifecycleState, .stopped)
        XCTAssertTrue(failure.value?.contains("failed repeatedly") == true)
        controller.stop()
    }

    func testTapConversionSuccessCancelsQueuedPersistentFailure() async throws {
        let controlQueue = DispatchQueue(label: "AudioCaptureRetryTests.tapPersistentSuccess")
        let controlBlocked = DispatchSemaphore(value: 0)
        controlQueue.async { controlBlocked.wait() }
        let failure = LockedString()

        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            controlQueue: controlQueue,
            uplink: { _, _ in }
        )
        controller.onFailure = { message in failure.set(message.logDetail) }

        try controller.start()
        controller.simulateTapConversionFailureForTesting()
        controller.simulateTapConversionFailureForTesting()
        controller.simulateTapConversionFailureForTesting()
        controller.simulateTapConversionSuccessForTesting()
        XCTAssertEqual(controller.lifecycleState, .running)

        controlBlocked.signal()
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertEqual(
            controller.lifecycleState,
            .running,
            "a success before the queued persistent decision proves recovery and cancels teardown"
        )
        XCTAssertNil(failure.value)
        controller.stop()
    }

    func testConversionFailureEntersRecoveringAndRecovers() async throws {
        let gate = RetryGate()
        let failure = LockedString()
        let delivery = LevelDeliveryBox()
        let sink = RetryLevelSink()
        let controlQueue = DispatchQueue(label: "AudioCaptureRetryTests.control")
        let controlBlocked = DispatchSemaphore(value: 0)
        controlQueue.async { controlBlocked.wait() }

        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            retryDelaysNanoseconds: [0],
            retrySleep: { try await gate.sleep($0) },
            controlQueue: controlQueue,
            uplink: { _, _ in },
            uptime: { 100 },
            levelDelivery: { action in delivery.deliver(action) }
        )
        controller.onFailure = { message in failure.set(message.logDetail) }
        controller.onLevel = { level in sink.append(level) }

        try controller.start()
        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels, [0])

        controller.ingestSamplesForTesting(loudFrame())
        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels.last, 4)

        controller.simulateConversionFailure()
        XCTAssertEqual(controller.lifecycleState, .recovering)

        controlBlocked.signal()
        let recovering = await waitForLifecycle(controller, .recovering)
        XCTAssertTrue(recovering)
        gate.releaseAll()
        let running = await waitForLifecycle(controller, .running)
        XCTAssertTrue(running)
        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels.last, 0, "conversion recovery must publish a zero baseline")
        XCTAssertNil(failure.value)
        controller.stop()
    }

    func testPersistentConversionFailureEndsStoppedAndReports() async throws {
        let failure = LockedString()
        let delivery = LevelDeliveryBox()
        let sink = RetryLevelSink()
        let controlQueue = DispatchQueue(label: "AudioCaptureRetryTests.control")
        let controlBlocked = DispatchSemaphore(value: 0)
        controlQueue.async { controlBlocked.wait() }
        let reported = expectation(description: "onFailure")

        let controller = AudioCaptureController(
            engine: AVAudioEngine(),
            permissionCheck: { true },
            startAttemptOverride: {},
            controlQueue: controlQueue,
            uplink: { _, _ in },
            uptime: { 100 },
            levelDelivery: { action in delivery.deliver(action) }
        )
        controller.onFailure = { message in
            failure.set(message.logDetail)
            reported.fulfill()
        }
        controller.onLevel = { level in sink.append(level) }

        try controller.start()
        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels, [0])

        controller.ingestSamplesForTesting(loudFrame())
        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels.last, 4)

        controller.simulatePersistentConversionFailure()
        XCTAssertEqual(controller.lifecycleState, .recovering)

        controlBlocked.signal()
        await fulfillment(of: [reported], timeout: 3)
        XCTAssertEqual(controller.lifecycleState, .stopped)
        await drainLevelDelivery(delivery)
        XCTAssertEqual(sink.levels.last, 0, "persistent failure must publish a zero baseline")
        XCTAssertTrue(failure.value?.contains("failed repeatedly") == true)
    }
}
