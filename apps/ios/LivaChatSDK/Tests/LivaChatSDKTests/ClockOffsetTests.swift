import XCTest
@testable import LivaChatSDK

final class ClockOffsetTests: XCTestCase {
    func testConvertsBothDirections() {
        let offset = ClockOffset()
        offset.update(serverTimeMs: 10_000, localTimeMs: 9_000)
        XCTAssertEqual(offset.currentOffsetMs(), 1_000)
        XCTAssertEqual(offset.toLocalTime(serverTimeMs: 9_500), 8_500)
        XCTAssertEqual(offset.toServerTime(localTimeMs: 8_500), 9_500)
    }

    func testReset() {
        let offset = ClockOffset()
        offset.update(serverTimeMs: 10_000, localTimeMs: 9_000)
        offset.reset()
        XCTAssertEqual(offset.currentOffsetMs(), 0)
        XCTAssertEqual(offset.toLocalTime(serverTimeMs: 9_500), 9_500)
    }
}
