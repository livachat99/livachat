import XCTest
@testable import LivaChatProtocol

final class DecodingTests: XCTestCase {
    private let decoder = ProtocolJSON.makeDecoder()

    private func decodeServer(_ type: String) throws -> ServerMessage {
        try decoder.decode(
            ServerMessage.self,
            from: Fixtures.data(direction: "s2c", type: type)
        )
    }

    private func decodeClient(_ type: String) throws -> ClientMessage {
        try decoder.decode(
            ClientMessage.self,
            from: Fixtures.data(direction: "c2s", type: type)
        )
    }

    func testAllFixtureTypesDecodeAndExposeType() throws {
        for type in Fixtures.clientTypes {
            XCTAssertEqual(try decodeClient(type).type, type)
        }
        for type in Fixtures.serverTypes {
            XCTAssertEqual(try decodeServer(type).type, type)
        }
    }

    func testMessageHeaderPreParse() throws {
        let header = try decoder.decode(
            MessageHeader.self,
            from: Fixtures.data(direction: "s2c", type: "status")
        )
        XCTAssertEqual(header.type, "status")
        XCTAssertEqual(header.seq, 20)
        XCTAssertEqual(header.timestamp, 1_712_345_683_600)
    }

    func testSessionReady() throws {
        let message = try decodeServer("session.ready")
        guard case let .sessionReady(seq, timestamp, payload) = message else {
            return XCTFail("expected session.ready")
        }
        XCTAssertEqual(seq, 1)
        XCTAssertEqual(timestamp, 1_712_345_678_200)
        XCTAssertEqual(payload.sessionId, "sess_abc123def456ghijkl")
        XCTAssertFalse(payload.sessionRestored)
        XCTAssertEqual(payload.serverTimeMs, 1_712_345_678_200)
        XCTAssertEqual(payload.keepaliveIntervalS, 15)
        XCTAssertEqual(payload.serverVersion, "0.1.0")
    }

    func testSessionEnded() throws {
        let message = try decodeServer("session.ended")
        guard case let .sessionEnded(_, _, payload) = message else {
            return XCTFail("expected session.ended")
        }
        XCTAssertEqual(payload.reason, .userQuit)
        XCTAssertEqual(payload.sessionId, "sess_abc123def456ghijkl")
    }

    func testSessionChanged() throws {
        let message = try decodeServer("session.changed")
        guard case let .sessionChanged(_, _, payload) = message else {
            return XCTFail("expected session.changed")
        }
        XCTAssertEqual(payload.mode, .translate)
        XCTAssertEqual(payload.myLanguage, "zh")
        XCTAssertEqual(payload.localLanguage, "ja")
        XCTAssertNil(payload.usecase)
    }

    func testTranscription() throws {
        let message = try decodeServer("transcription")
        guard case let .transcription(_, _, payload) = message else {
            return XCTFail("expected transcription")
        }
        XCTAssertEqual(payload.text, "今天天气真不错")
        XCTAssertFalse(payload.isFinal)
        XCTAssertEqual(payload.transcriptionId, "trans_a1b2c3d4e5f6")
    }

    func testAiText() throws {
        let message = try decodeServer("ai_text")
        guard case let .aiText(_, _, payload) = message else {
            return XCTFail("expected ai_text")
        }
        XCTAssertEqual(payload.text, "今天天气确实")
        XCTAssertEqual(payload.messageId, "msg_xyz789abcdef")
        XCTAssertEqual(payload.index, 1)
        XCTAssertFalse(payload.isFinal)
        XCTAssertNil(payload.source)
        XCTAssertNil(payload.image)
    }

    func testJobStatus() throws {
        let message = try decodeServer("job_status")
        guard case let .jobStatus(_, _, payload) = message else {
            return XCTFail("expected job_status")
        }
        XCTAssertEqual(payload.jobs.count, 1)
        let job = try XCTUnwrap(payload.jobs.first)
        XCTAssertEqual(job.jobId, "9f3c2ab4d1e84f0c9f3c2ab4d1e84f0c")
        XCTAssertEqual(job.agentName, "hermes")
        XCTAssertEqual(job.summary, "正在查询东京机票")
        XCTAssertEqual(job.startedAtMs, 1_712_345_640_000)
        XCTAssertEqual(job.runningTimeMs, 47_000)
    }

    func testAiAudio() throws {
        let message = try decodeServer("ai_audio")
        guard case let .aiAudio(_, _, payload) = message else {
            return XCTFail("expected ai_audio")
        }
        let expected = try XCTUnwrap(
            Data(base64Encoded: "Ly9VUXhBQUFBQUtBQVlBQUFBQ0FBb0FCZ0FBQUFJQUFBQURBQUE9PQ==")
        )
        XCTAssertEqual(payload.data, expected)
        XCTAssertEqual(payload.codec, "opus")
        XCTAssertEqual(payload.messageId, "msg_xyz789abcdef")
        XCTAssertEqual(payload.index, 0)
        XCTAssertEqual(payload.durationMs, 40)
        XCTAssertFalse(payload.isFinal)
    }

    func testAiVideoAck() throws {
        let message = try decodeServer("ai_video_ack")
        guard case let .aiVideoAck(_, _, payload) = message else {
            return XCTFail("expected ai_video_ack")
        }
        XCTAssertEqual(payload.frameId, 7)
        XCTAssertEqual(payload.ackFrameId, 7)
        XCTAssertTrue(payload.processed)
    }

    func testStatus() throws {
        let message = try decodeServer("status")
        guard case let .status(_, _, payload) = message else {
            return XCTFail("expected status")
        }
        XCTAssertEqual(payload.sessionStatus, .thinking)
        XCTAssertEqual(payload.reason, .asrFinal)
        XCTAssertNil(payload.hint)
    }

    func testError() throws {
        let message = try decodeServer("error")
        guard case let .error(_, _, payload) = message else {
            return XCTFail("expected error")
        }
        XCTAssertEqual(payload.code, 1001)
        XCTAssertEqual(payload.message, "ASR service unavailable")
        XCTAssertFalse(payload.fatal)
        XCTAssertTrue(payload.recoverable)
    }

    func testPingAndPong() throws {
        let ping = try decodeServer("ping")
        guard case .ping = ping else {
            return XCTFail("expected ping")
        }
        let pong = try decodeServer("pong")
        guard case let .pong(_, _, payload) = pong else {
            return XCTFail("expected pong")
        }
        XCTAssertEqual(payload.pingSeq, 60)
    }

    func testClientSessionStart() throws {
        let message = try decodeClient("session.start")
        guard case let .sessionStart(_, _, payload) = message else {
            return XCTFail("expected session.start")
        }
        XCTAssertNil(payload.sessionId)
        XCTAssertTrue(payload.config.enableVideo)
        XCTAssertEqual(payload.config.audioSampleRate, .hz24000)
        XCTAssertEqual(payload.config.vadSensitivity, .medium)
        XCTAssertEqual(payload.config.clientVersion, "ios-1.0.0")
        XCTAssertEqual(payload.config.timezone, "Asia/Shanghai")
        XCTAssertEqual(payload.config.language, "zh-Hans")
    }

    func testUnknownFieldsAreIgnored() throws {
        var object = try Fixtures.object(direction: "s2c", type: "status")
        var payload = try payload(of: object)
        payload["unexpected_field"] = "ignored"
        object["payload"] = payload

        let message = try decoder.decode(
            ServerMessage.self,
            from: Fixtures.encode(object)
        )
        guard case let .status(_, _, decoded) = message else {
            return XCTFail("expected status")
        }
        XCTAssertEqual(decoded.sessionStatus, .thinking)
    }

    func testUnknownMessageTypeThrows() throws {
        let data = Data(
            #"{"type":"new.thing","seq":9,"timestamp":10,"payload":{}}"#.utf8
        )
        XCTAssertThrowsError(
            try decoder.decode(ServerMessage.self, from: data)
        ) { error in
            guard case let ProtocolDecodingError.unknownMessageType(
                type, seq, timestamp
            ) = error else {
                return XCTFail("unexpected error: \(error)")
            }
            XCTAssertEqual(type, "new.thing")
            XCTAssertEqual(seq, 9)
            XCTAssertEqual(timestamp, 10)
        }
    }

    func testUnknownEnumValueThrows() throws {
        var object = try Fixtures.object(direction: "s2c", type: "status")
        var payload = try payload(of: object)
        payload["session_status"] = "brand_new_state"
        object["payload"] = payload

        XCTAssertThrowsError(
            try decoder.decode(
                ServerMessage.self,
                from: Fixtures.encode(object)
            )
        )
    }

    func testMissingRequiredNullableSessionIdThrows() throws {
        var object = try Fixtures.object(direction: "c2s", type: "session.start")
        var payload = try payload(of: object)
        payload.removeValue(forKey: "session_id")
        object["payload"] = payload

        XCTAssertThrowsError(
            try decoder.decode(
                ClientMessage.self,
                from: Fixtures.encode(object)
            )
        )
    }

    func testIntegerBounds() throws {
        let valid = Data(
            #"{"type":"ai_video_ack","seq":4294967295,"timestamp":18446744073709551615,"payload":{"frame_id":4294967295,"ack_frame_id":0,"processed":true}}"#.utf8
        )
        let message = try decoder.decode(ServerMessage.self, from: valid)
        guard case let .aiVideoAck(seq, timestamp, ack) = message else {
            return XCTFail("expected ai_video_ack")
        }
        XCTAssertEqual(seq, UInt32.max)
        XCTAssertEqual(timestamp, UInt64.max)
        XCTAssertEqual(ack.frameId, UInt32.max)

        let invalidFrame = Data(
            #"{"type":"ai_video_ack","seq":1,"timestamp":1,"payload":{"frame_id":4294967296,"ack_frame_id":0,"processed":true}}"#.utf8
        )
        XCTAssertThrowsError(
            try decoder.decode(ServerMessage.self, from: invalidFrame)
        )

        var video = try Fixtures.object(direction: "c2s", type: "video")
        var videoPayload = try payload(of: video)
        videoPayload["quality"] = 256
        video["payload"] = videoPayload
        XCTAssertThrowsError(
            try decoder.decode(
                ClientMessage.self,
                from: Fixtures.encode(video)
            )
        )
    }

    func testClientSessionStartNonNilSessionIdDecodes() throws {
        var object = try Fixtures.object(direction: "c2s", type: "session.start")
        var payload = try payload(of: object)
        payload["session_id"] = "sess_abc123def456ghijkl"
        object["payload"] = payload

        let message = try decoder.decode(
            ClientMessage.self,
            from: Fixtures.encode(object)
        )
        guard case let .sessionStart(_, _, decoded) = message else {
            return XCTFail("expected session.start")
        }
        XCTAssertEqual(decoded.sessionId, "sess_abc123def456ghijkl")
    }

    func testAiTextAgentFields() throws {
        let json = Data(
            """
            {"type":"ai_text","seq":1,"timestamp":2,"payload":{"text":"ok","message_id":"msg_xyz789abcdef","index":0,"is_final":true,"source":"agent","reply_to":"trans_a1b2c3d4e5f6","agent_name":"hermes","proactive":true}}
            """.utf8
        )
        let message = try decoder.decode(ServerMessage.self, from: json)
        guard case let .aiText(_, _, payload) = message else {
            return XCTFail("expected ai_text")
        }
        XCTAssertEqual(payload.source, .agent)
        XCTAssertEqual(payload.replyTo, "trans_a1b2c3d4e5f6")
        XCTAssertEqual(payload.agentName, "hermes")
        XCTAssertEqual(payload.proactive, true)
    }

    func testSessionChangedUsecase() throws {
        var object = try Fixtures.object(direction: "s2c", type: "session.changed")
        var payload = try payload(of: object)
        payload["usecase"] = "lecture"
        object["payload"] = payload

        let message = try decoder.decode(
            ServerMessage.self,
            from: Fixtures.encode(object)
        )
        guard case let .sessionChanged(_, _, decoded) = message else {
            return XCTFail("expected session.changed")
        }
        XCTAssertEqual(decoded.usecase, .lecture)
    }

    func testStatusHint() throws {
        var object = try Fixtures.object(direction: "s2c", type: "status")
        var payload = try payload(of: object)
        payload["hint"] = "reduce_video_quality"
        object["payload"] = payload

        let message = try decoder.decode(
            ServerMessage.self,
            from: Fixtures.encode(object)
        )
        guard case let .status(_, _, decoded) = message else {
            return XCTFail("expected status")
        }
        XCTAssertEqual(decoded.hint, "reduce_video_quality")
    }

    func testInvalidBase64Throws() throws {
        var object = try Fixtures.object(direction: "s2c", type: "ai_audio")
        var payload = try payload(of: object)
        payload["data"] = "not-base64!"
        object["payload"] = payload

        XCTAssertThrowsError(
            try decoder.decode(
                ServerMessage.self,
                from: Fixtures.encode(object)
            )
        )
    }

    func testUInt16Bounds() throws {
        var object = try Fixtures.object(direction: "c2s", type: "video")
        var payload = try payload(of: object)
        payload["width"] = 65536
        object["payload"] = payload

        XCTAssertThrowsError(
            try decoder.decode(
                ClientMessage.self,
                from: Fixtures.encode(object)
            )
        )
    }

    func testAiTextImageDataIsData() throws {
        let json = Data(
            """
            {"type":"ai_text","seq":1,"timestamp":2,"payload":{"text":"ok","message_id":"msg_xyz789abcdef","index":0,"is_final":true,"image":{"data":"AQID","mime":"image/jpeg","width":1,"height":1,"orientation":0,"camera_facing":"front"}}}
            """.utf8
        )
        let message = try decoder.decode(ServerMessage.self, from: json)
        guard case let .aiText(_, _, payload) = message else {
            return XCTFail("expected ai_text")
        }
        let image = try XCTUnwrap(payload.image)
        XCTAssertEqual(image.data, Data([0x01, 0x02, 0x03]))
        XCTAssertEqual(image.mime, "image/jpeg")
        XCTAssertEqual(image.width, 1)
        XCTAssertEqual(image.height, 1)
        XCTAssertEqual(image.orientation, .degrees0)
        XCTAssertEqual(image.cameraFacing, .front)
    }
}
