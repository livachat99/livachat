import XCTest
@testable import LivaChatApp

final class LogFileWriterTests: XCTestCase {
    private let timestampPattern = #"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"#

    func testLinesUseISO8601UTCFirstFieldAndEscapeNewlines() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("livachat-log-\(UUID().uuidString)", isDirectory: true)
        let url = directory.appendingPathComponent("livachat.log")
        let writer = LogFileWriter(fileURL: url)

        writer.log(level: "I", component: "App/Test", message: "first line\nsecond")
        writer.log(level: "E", component: "SDK/WS", message: "boom")
        writer.flush()

        let text = try String(contentsOf: url, encoding: .utf8)
        let lines = text.split(separator: "\n").map(String.init)
        XCTAssertEqual(lines.count, 2)

        let first = lines[0].split(separator: " ", maxSplits: 2).map(String.init)
        let second = lines[1].split(separator: " ", maxSplits: 2).map(String.init)
        XCTAssertEqual(first.count, 3)
        XCTAssertEqual(second.count, 3)
        XCTAssertNotNil(first[0].range(of: timestampPattern, options: .regularExpression))
        XCTAssertNotNil(second[0].range(of: timestampPattern, options: .regularExpression))
        XCTAssertLessThanOrEqual(first[0], second[0])
        XCTAssertEqual(first[1], "I/App/Test")
        XCTAssertEqual(first[2], "first line\\nsecond")
        XCTAssertEqual(second[1], "E/SDK/WS")
    }
}
