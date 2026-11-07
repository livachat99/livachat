import XCTest
@testable import LivaChatProtocol

final class EncodingTests: XCTestCase {
    private let encoder = ProtocolJSON.makeEncoder()

    private func encode(_ message: ClientMessage) throws -> [String: Any] {
        let data = try encoder.encode(message)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private func sessionStart(
        sessionId: String?,
        timezone: String? = nil,
        language: String? = nil
    ) -> ClientMessage {
        let config = ClientSessionStartConfig(
            enableVideo: true,
            audioSampleRate: .hz24000,
            vadSensitivity: .medium,
            clientVersion: "ios-1.0.0.42",
            timezone: timezone,
            language: language
        )
        return .sessionStart(
            seq: 1,
            timestamp: 1_712_345_678_000,
            payload: .init(sessionId: sessionId, config: config)
        )
    }

    func testRequiredNullableSessionIdIsEmittedAsNull() throws {
        let json = try encode(sessionStart(sessionId: nil))
        XCTAssertEqual(json["type"] as? String, "session.start")
        XCTAssertEqual(uint32(json["seq"]), 1)
        XCTAssertEqual(uint64(json["timestamp"]), 1_712_345_678_000)

        let payload = try payload(of: json)
        XCTAssertTrue(payload.keys.contains("session_id"))
        XCTAssertTrue(payload["session_id"] is NSNull)

        let config = try XCTUnwrap(payload["config"] as? [String: Any])
        XCTAssertEqual(config["enable_video"] as? Bool, true)
        XCTAssertEqual(int(config["audio_sample_rate"]), 24000)
        XCTAssertEqual(config["vad_sensitivity"] as? String, "medium")
        XCTAssertEqual(config["client_version"] as? String, "ios-1.0.0.42")
        XCTAssertNil(config["timezone"])
        XCTAssertNil(config["language"])
    }

    func testNonNilSessionIdIsEmittedAsString() throws {
        let id = "sess_abc123def456ghijkl"
        let json = try encode(sessionStart(sessionId: id))
        let payload = try payload(of: json)
        XCTAssertEqual(payload["session_id"] as? String, id)
    }

    func testOptionalTimezoneIsEmittedWhenPresent() throws {
        let json = try encode(sessionStart(sessionId: nil, timezone: "Asia/Shanghai"))
        let payload = try payload(of: json)
        let config = try XCTUnwrap(payload["config"] as? [String: Any])
        XCTAssertEqual(config["timezone"] as? String, "Asia/Shanghai")
    }

    func testOptionalLanguageIsEmittedWhenPresent() throws {
        let json = try encode(sessionStart(sessionId: nil, language: "zh-Hans"))
        let payload = try payload(of: json)
        let config = try XCTUnwrap(payload["config"] as? [String: Any])
        XCTAssertEqual(config["language"] as? String, "zh-Hans")
    }

    func testOptionalVideoFieldsAreOmittedWhenNil() throws {
        let message = ClientMessage.video(
            seq: 2,
            timestamp: 2,
            payload: .init(
                data: Data([0x01, 0x02, 0x03]),
                frameId: 1,
                width: 1080,
                height: 1920,
                orientation: .degrees270,
                cameraFacing: .front,
                quality: 70,
                turnId: nil,
                text: nil
            )
        )
        let json = try encode(message)
        let payload = try payload(of: json)
        XCTAssertNil(payload["turn_id"])
        XCTAssertNil(payload["text"])
        XCTAssertEqual(int(payload["orientation"]), 270)
        XCTAssertEqual(payload["camera_facing"] as? String, "front")
        XCTAssertEqual(int(payload["quality"]), 70)
    }

    func testPingPayloadIsEmptyObject() throws {
        let message = ClientMessage.ping(
            seq: 3,
            timestamp: 3,
            payload: .init()
        )
        let json = try encode(message)
        let payload = try payload(of: json)
        XCTAssertTrue(payload.isEmpty)
    }

    func testAudioDataUsesBase64() throws {
        let message = ClientMessage.audio(
            seq: 4,
            timestamp: 4,
            payload: .init(
                data: Data([0x01, 0x02, 0x03]),
                codec: "opus",
                durationMs: 20,
                vadState: .speech
            )
        )
        let json = try encode(message)
        let payload = try payload(of: json)
        XCTAssertEqual(payload["data"] as? String, "AQID")
        XCTAssertEqual(int(payload["duration_ms"]), 20)
        XCTAssertEqual(payload["vad_state"] as? String, "speech")
    }

    func testVideoOptionalFieldsAreEmittedWhenPresent() throws {
        let message = ClientMessage.video(
            seq: 5,
            timestamp: 5,
            payload: .init(
                data: Data([0x01]),
                frameId: 1,
                width: 640,
                height: 480,
                orientation: .degrees0,
                cameraFacing: .back,
                quality: 60,
                turnId: 3,
                text: "描述图片"
            )
        )
        let json = try encode(message)
        let payload = try payload(of: json)
        XCTAssertEqual(uint32(payload["turn_id"]), 3)
        XCTAssertEqual(payload["text"] as? String, "描述图片")
    }

    func testSessionChangeUsecase() throws {
        let message = ClientMessage.sessionChange(
            seq: 6,
            timestamp: 6,
            payload: .init(
                mode: .translate,
                myLanguage: "zh",
                localLanguage: "ja",
                usecase: .lecture
            )
        )
        let json = try encode(message)
        let payload = try payload(of: json)
        XCTAssertEqual(payload["mode"] as? String, "translate")
        XCTAssertEqual(payload["usecase"] as? String, "lecture")
    }

    func testClientTypeProperty() {
        XCTAssertEqual(sessionStart(sessionId: nil).type, "session.start")
        XCTAssertEqual(
            ClientMessage.ping(seq: 1, timestamp: 1, payload: .init()).type,
            "ping"
        )
    }
}
