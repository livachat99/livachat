import XCTest
@testable import LivaChatSDK

final class HeartbeatSchedulerTests: XCTestCase {
    private final class TestClock {
        var now: TimeInterval = 0
    }

    func testPingOncePerSilenceWindowAndTimeout() {
        let clock = TestClock()
        let scheduler = HeartbeatScheduler(
            keepaliveInterval: 15,
            keepaliveTimeout: 35,
            clock: { clock.now }
        )

        XCTAssertFalse(scheduler.shouldSendPing())
        XCTAssertFalse(scheduler.isTimeout())

        scheduler.start()
        XCTAssertFalse(scheduler.shouldSendPing())

        clock.now = 14.9
        XCTAssertFalse(scheduler.shouldSendPing())

        clock.now = 15
        XCTAssertTrue(scheduler.shouldSendPing())
        scheduler.onPingSent()
        XCTAssertFalse(scheduler.shouldSendPing())

        clock.now = 34.9
        XCTAssertFalse(scheduler.isTimeout())

        clock.now = 35
        XCTAssertTrue(scheduler.isTimeout())
    }

    func testInboundResetsWindow() {
        let clock = TestClock()
        let scheduler = HeartbeatScheduler(
            keepaliveInterval: 15,
            keepaliveTimeout: 35,
            clock: { clock.now }
        )
        scheduler.start()

        clock.now = 10
        scheduler.onInbound()
        clock.now = 20
        XCTAssertFalse(scheduler.shouldSendPing())

        clock.now = 25
        XCTAssertTrue(scheduler.shouldSendPing())
        scheduler.onPingSent()
        clock.now = 26
        scheduler.onInbound()
        XCTAssertFalse(scheduler.shouldSendPing())
        XCTAssertFalse(scheduler.isTimeout())

        clock.now = 60
        XCTAssertFalse(scheduler.isTimeout())
        clock.now = 61
        XCTAssertTrue(scheduler.isTimeout())
    }
}
