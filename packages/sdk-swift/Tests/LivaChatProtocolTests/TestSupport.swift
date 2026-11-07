import Foundation
import XCTest
@testable import LivaChatProtocol

enum FixtureError: Error {
    case missingResource
    case missingMessage(direction: String, type: String)
    case invalidShape
}

enum Fixtures {
    static let clientTypes = [
        "session.start", "session.end", "session.change", "audio",
        "video", "interrupt", "ping", "pong",
    ]

    static let serverTypes = [
        "session.ready", "session.ended", "session.changed", "transcription",
        "ai_text", "job_status", "ai_audio", "ai_video_ack", "status",
        "error", "ping", "pong",
    ]

    static func data(direction: String, type: String) throws -> Data {
        guard let url = Bundle.module.url(
            forResource: "schema-examples",
            withExtension: "json",
            subdirectory: "Fixtures"
        ) else {
            throw FixtureError.missingResource
        }
        let rootData = try Data(contentsOf: url)
        guard let root = try JSONSerialization.jsonObject(with: rootData) as? [String: Any],
              let messages = root[direction] as? [String: Any],
              let message = messages[type] else {
            throw FixtureError.missingMessage(direction: direction, type: type)
        }
        return try JSONSerialization.data(withJSONObject: message)
    }

    static func object(direction: String, type: String) throws -> [String: Any] {
        let data = try data(direction: direction, type: type)
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw FixtureError.invalidShape
        }
        return object
    }

    static func encode(_ object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object)
    }
}

extension XCTestCase {
    func payload(of object: [String: Any]) throws -> [String: Any] {
        try XCTUnwrap(object["payload"] as? [String: Any])
    }

    func int(_ value: Any?) -> Int? {
        (value as? NSNumber)?.intValue
    }

    func uint32(_ value: Any?) -> UInt32? {
        (value as? NSNumber)?.uint32Value
    }

    func uint64(_ value: Any?) -> UInt64? {
        (value as? NSNumber)?.uint64Value
    }
}
