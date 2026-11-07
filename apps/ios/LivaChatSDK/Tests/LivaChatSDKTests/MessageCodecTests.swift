import Foundation
import LivaChatProtocol
import XCTest
@testable import LivaChatSDK

final class MessageCodecTests: XCTestCase {
    func testEncodesPing() throws {
        let codec = MessageCodec()
        let json = try codec.encode(
            .ping(seq: 1, timestamp: 2, payload: ClientPingPayload())
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        XCTAssertEqual(object["type"] as? String, "ping")
        XCTAssertEqual(object["seq"] as? UInt32, 1)
        XCTAssertEqual(object["timestamp"] as? UInt64, 2)
        XCTAssertNotNil(object["payload"])
    }

    func testDecodesServerMessage() throws {
        let encoder = ProtocolJSON.makeEncoder()
        let original = ServerMessage.sessionReady(
            seq: 7,
            timestamp: 9,
            payload: ServerSessionReadyPayload(
                sessionId: "sess_test",
                sessionRestored: false,
                serverTimeMs: 10_000,
                keepaliveIntervalS: 15
            )
        )
        let data = try encoder.encode(original)
        let text = try XCTUnwrap(String(data: data, encoding: .utf8))

        let codec = MessageCodec()
        let decoded = try XCTUnwrap(codec.decode(text))
        guard case let .sessionReady(seq, timestamp, payload) = decoded else {
            return XCTFail("wrong message type")
        }
        XCTAssertEqual(seq, 7)
        XCTAssertEqual(timestamp, 9)
        XCTAssertEqual(payload.sessionId, "sess_test")
        XCTAssertEqual(payload.serverTimeMs, 10_000)
        XCTAssertEqual(payload.keepaliveIntervalS, 15)
    }

    func testMalformedAndUnknownReturnNil() {
        let codec = MessageCodec()
        XCTAssertNil(codec.decode("not json"))
        XCTAssertNil(codec.decode(#"{"type":"does.not.exist","seq":1}"#))
    }
}
