import XCTest
@testable import LivaChatSDK

final class StateStoreTests: XCTestCase {
    func testPlaybackEpochIncrements() {
        let store = StateStore()
        XCTAssertEqual(store.snapshot().playbackEpoch, 1)
        XCTAssertEqual(store.incrementPlaybackEpoch(), 2)
        XCTAssertEqual(store.snapshot().playbackEpoch, 2)
        XCTAssertEqual(store.incrementPlaybackEpoch(), 3)
    }

    func testConnectionTransitionIncrementsOnlyWhenLeavingConnected() {
        let store = StateStore()

        store.transitionConnectionState(.connected, connectionEpoch: 1)
        XCTAssertEqual(store.snapshot().playbackEpoch, 1)

        store.transitionConnectionState(.reconnecting, connectionEpoch: 1)
        XCTAssertEqual(store.snapshot().playbackEpoch, 2)

        store.transitionConnectionState(.connected, connectionEpoch: 2)
        XCTAssertEqual(store.snapshot().playbackEpoch, 2)

        store.transitionConnectionState(.disconnected, connectionEpoch: 2)
        XCTAssertEqual(store.snapshot().playbackEpoch, 3)
    }

    func testStateStoreSnapshot() {
        let store = StateStore()
        XCTAssertEqual(store.snapshot(), .initial)

        store.update { state in
            state = LivaChatState(
                connectionState: .connected,
                connectionEpoch: 4,
                sessionState: .listening,
                localVadState: .speech,
                playbackEpoch: 2
            )
        }
        XCTAssertEqual(store.snapshot().connectionState, .connected)
        XCTAssertEqual(store.snapshot().connectionEpoch, 4)
        XCTAssertEqual(store.snapshot().sessionState, .listening)
        XCTAssertEqual(store.snapshot().localVadState, .speech)
        XCTAssertEqual(store.snapshot().playbackEpoch, 2)
    }
}
