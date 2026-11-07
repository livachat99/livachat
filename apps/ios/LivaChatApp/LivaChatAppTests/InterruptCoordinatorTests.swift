import LivaChatSDK
import XCTest
@testable import LivaChatApp

@MainActor
final class InterruptCoordinatorTests: XCTestCase {
    private final class FakeHaptics: HapticProviding {
        var taps = 0
        func lightTap() { taps += 1 }
    }

    private func makeCoordinator(
        haptics: FakeHaptics = FakeHaptics(),
        clock: @escaping () -> TimeInterval
    ) -> (InterruptCoordinator, () -> Int) {
        var interrupts = 0
        let coordinator = InterruptCoordinator(
            sendInterrupt: { interrupts += 1 },
            haptics: haptics,
            now: clock
        )
        return (coordinator, { interrupts })
    }

    func testSpeakingAndSpeechTriggersInterruptAndHaptic() {
        var now: TimeInterval = 100
        let haptics = FakeHaptics()
        let (coordinator, interrupts) = makeCoordinator(haptics: haptics, clock: { now })
        coordinator.start()

        coordinator.update(sessionState: .speaking, vadState: .speech)
        XCTAssertEqual(interrupts(), 1)
        XCTAssertEqual(haptics.taps, 1)

        now += 0.1
        coordinator.update(sessionState: .speaking, vadState: .silence)
        coordinator.update(sessionState: .speaking, vadState: .speech)
        XCTAssertEqual(interrupts(), 1, "within throttle window")

        now += 0.5
        coordinator.update(sessionState: .speaking, vadState: .speech)
        XCTAssertEqual(interrupts(), 2)
        XCTAssertEqual(haptics.taps, 2)
    }

    func testOnlySpeakingTriggers() {
        var now: TimeInterval = 0
        let haptics = FakeHaptics()
        let (coordinator, interrupts) = makeCoordinator(haptics: haptics, clock: { now })
        coordinator.start()

        coordinator.update(sessionState: .listening, vadState: .speech)
        coordinator.update(sessionState: .thinking, vadState: .speech)
        coordinator.update(sessionState: .idle, vadState: .speech)
        XCTAssertEqual(interrupts(), 0)
        XCTAssertEqual(haptics.taps, 0)
    }

    func testDisabledHapticsStillSendsInterrupt() {
        var now: TimeInterval = 0
        let haptics = FakeHaptics()
        let (coordinator, interrupts) = makeCoordinator(haptics: haptics, clock: { now })
        coordinator.start()
        coordinator.setHapticEnabled(false)

        coordinator.update(sessionState: .speaking, vadState: .speech)
        XCTAssertEqual(interrupts(), 1)
        XCTAssertEqual(haptics.taps, 0)
    }

    func testStopResetsThrottle() {
        var now: TimeInterval = 10
        let haptics = FakeHaptics()
        let (coordinator, interrupts) = makeCoordinator(haptics: haptics, clock: { now })
        coordinator.start()
        coordinator.update(sessionState: .speaking, vadState: .speech)
        XCTAssertEqual(interrupts(), 1)

        coordinator.stop()
        coordinator.start()
        coordinator.update(sessionState: .speaking, vadState: .speech)
        XCTAssertEqual(interrupts(), 2)
    }
}
