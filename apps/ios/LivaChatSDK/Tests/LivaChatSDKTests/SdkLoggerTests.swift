import Foundation
import XCTest
@testable import LivaChatSDK

final class SdkLoggerTests: XCTestCase {
    func testFileLineUsesISO8601UTCTimestampAndSDKComponent() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("livachat-sdk-log-\(UUID().uuidString)", isDirectory: true)
        let url = directory.appendingPathComponent("livachat.log")
        let logger = SdkLogger(logFileURL: url)

        logger.info("WS", "hello\nworld")
        SDKQueue.log.sync {}

        let text = try String(contentsOf: url, encoding: .utf8)
        let line = try XCTUnwrap(text.split(separator: "\n").first.map(String.init))
        let fields = line.split(separator: " ", maxSplits: 2).map(String.init)
        XCTAssertEqual(fields.count, 3)
        XCTAssertNotNil(
            fields[0].range(
                of: #"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"#,
                options: .regularExpression
            )
        )
        XCTAssertEqual(fields[1], "I/SDK/WS")
        XCTAssertEqual(fields[2], "hello\\nworld")
    }

    func testDeinitOnLogQueueDoesNotDeadlock() {
        let released = expectation(description: "released")
        SDKQueue.log.async {
            var logger: SdkLogger? = SdkLogger(logFileURL: nil)
            logger?.info("Test", "deinit on log queue")
            logger = nil
            released.fulfill()
        }
        wait(for: [released], timeout: 2)
    }
}
