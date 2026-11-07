import Foundation
import XCTest
@testable import LivaChatSDK

#if os(iOS)
final class FacadeTests: XCTestCase {
    func testInitializesAndAcceptsPushesWithoutConnection() throws {
        let config = LivaChatConfig(
            serverURL: URL(string: "wss://127.0.0.1:1/v1/ws/stream")!,
            token: "token",
            deviceID: "device",
            clientVersion: "ios-test"
        )
        let sdk = try LivaChatSDK(config: config, audioSink: nil)

        XCTAssertEqual(sdk.state.connectionState, .disconnected)
        XCTAssertEqual(sdk.state.sessionState, .idle)
        XCTAssertEqual(sdk.state.localVadState, .unknown)
        XCTAssertEqual(sdk.state.playbackEpoch, 1)

        sdk.pushAudioPCM([Int16](repeating: 0, count: 320))
        sdk.pushVideoJPEG(
            Data([0xFF, 0xD8, 0xFF]),
            width: 10,
            height: 10,
            orientation: .portrait0
        )
        sdk.startSession()
        sdk.sendInterrupt()
        sdk.endSession()
        sdk.disconnect()
    }

    func testInvalidConfigThrows() {
        let config = LivaChatConfig(
            serverURL: URL(string: "wss://127.0.0.1:1/v1/ws/stream")!,
            token: "",
            deviceID: "device",
            clientVersion: "ios-test"
        )
        XCTAssertThrowsError(try LivaChatSDK(config: config, audioSink: nil))
    }
}
#endif
