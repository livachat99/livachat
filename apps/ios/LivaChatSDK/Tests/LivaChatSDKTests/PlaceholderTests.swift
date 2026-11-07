import XCTest
@testable import LivaChatSDK

final class PlaceholderTests: XCTestCase {
    func testInitialState() {
        let state = LivaChatState.initial
        XCTAssertTrue(state.connectionState == .disconnected)
        XCTAssertEqual(state.connectionEpoch, 0)
        XCTAssertEqual(state.playbackEpoch, 1)
    }
}
