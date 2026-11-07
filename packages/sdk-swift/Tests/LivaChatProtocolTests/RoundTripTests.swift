import XCTest
@testable import LivaChatProtocol

final class RoundTripTests: XCTestCase {
    private let encoder = ProtocolJSON.makeEncoder()
    private let decoder = ProtocolJSON.makeDecoder()

    func testClientMessagesRoundTrip() throws {
        for type in Fixtures.clientTypes {
            let original = try decoder.decode(
                ClientMessage.self,
                from: Fixtures.data(direction: "c2s", type: type)
            )
            let encoded = try encoder.encode(original)
            let decoded = try decoder.decode(ClientMessage.self, from: encoded)
            XCTAssertEqual(decoded, original, "round-trip failed for \(type)")
            XCTAssertEqual(decoded.type, type)
        }
    }

    func testServerMessagesRoundTrip() throws {
        for type in Fixtures.serverTypes {
            let original = try decoder.decode(
                ServerMessage.self,
                from: Fixtures.data(direction: "s2c", type: type)
            )
            let encoded = try encoder.encode(original)
            let decoded = try decoder.decode(ServerMessage.self, from: encoded)
            XCTAssertEqual(decoded, original, "round-trip failed for \(type)")
            XCTAssertEqual(decoded.type, type)
        }
    }
}
