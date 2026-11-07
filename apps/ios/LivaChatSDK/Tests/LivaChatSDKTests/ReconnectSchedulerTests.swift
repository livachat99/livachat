import XCTest
@testable import LivaChatSDK

final class ReconnectSchedulerTests: XCTestCase {
    func testExponentialDelaysWithZeroJitter() throws {
        let scheduler = ReconnectScheduler(
            config: ReconnectConfig(maxAttempts: 5, baseDelay: 1, maxDelay: 30),
            jitterProvider: { 0 }
        )
        XCTAssertEqual(try XCTUnwrap(scheduler.nextDelay()), 1, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(scheduler.nextDelay()), 2, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(scheduler.nextDelay()), 4, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(scheduler.nextDelay()), 8, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(scheduler.nextDelay()), 16, accuracy: 1e-9)
        XCTAssertNil(scheduler.nextDelay())
    }

    func testDelayCap() throws {
        let scheduler = ReconnectScheduler(
            config: ReconnectConfig(maxAttempts: 6, baseDelay: 1, maxDelay: 10),
            jitterProvider: { 0 }
        )
        let delays = (0..<6).compactMap { _ in scheduler.nextDelay() }
        XCTAssertEqual(delays, [1, 2, 4, 8, 10, 10])
    }

    func testJitterIsAppliedAndClamped() throws {
        let positive = ReconnectScheduler(
            config: ReconnectConfig(maxAttempts: 1, baseDelay: 1, maxDelay: 30),
            jitterProvider: { 0.5 }
        )
        XCTAssertEqual(try XCTUnwrap(positive.nextDelay()), 1.5, accuracy: 1e-9)

        let negative = ReconnectScheduler(
            config: ReconnectConfig(maxAttempts: 1, baseDelay: 1, maxDelay: 30),
            jitterProvider: { -2 }
        )
        XCTAssertEqual(try XCTUnwrap(negative.nextDelay()), 0, accuracy: 1e-9)
    }

    func testReset() throws {
        let scheduler = ReconnectScheduler(
            config: ReconnectConfig(maxAttempts: 2, baseDelay: 1, maxDelay: 30),
            jitterProvider: { 0 }
        )
        _ = scheduler.nextDelay()
        _ = scheduler.nextDelay()
        XCTAssertNil(scheduler.nextDelay())
        scheduler.reset()
        XCTAssertEqual(scheduler.currentAttempt, 0)
        XCTAssertEqual(try XCTUnwrap(scheduler.nextDelay()), 1, accuracy: 1e-9)
    }
}
