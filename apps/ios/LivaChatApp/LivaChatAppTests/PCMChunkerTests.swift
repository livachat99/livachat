import XCTest
@testable import LivaChatApp

final class PCMChunkerTests: XCTestCase {
    func testSplitsExactFrames() {
        var chunker = PCMChunker(frameSamples: 320)
        let frames = chunker.append(Array(repeating: 1, count: 640))
        XCTAssertEqual(frames.count, 2)
        XCTAssertEqual(frames[0].count, 320)
        XCTAssertEqual(frames[1].count, 320)
        XCTAssertEqual(chunker.pendingCount, 0)
    }

    func testCarriesRemainderAcrossAppends() {
        var chunker = PCMChunker(frameSamples: 320)
        XCTAssertEqual(chunker.append(Array(repeating: 1, count: 400)).count, 1)
        XCTAssertEqual(chunker.pendingCount, 80)
        XCTAssertEqual(chunker.append(Array(repeating: 2, count: 240)).count, 1)
        XCTAssertEqual(chunker.pendingCount, 0)
    }

    func testEmptyInputProducesNothing() {
        var chunker = PCMChunker(frameSamples: 320)
        XCTAssertTrue(chunker.append([]).isEmpty)
        XCTAssertEqual(chunker.pendingCount, 0)
    }

    func testResetDropsPendingSamples() {
        var chunker = PCMChunker(frameSamples: 320)
        _ = chunker.append(Array(repeating: 1, count: 100))
        chunker.reset()
        XCTAssertEqual(chunker.pendingCount, 0)
        XCTAssertEqual(chunker.append(Array(repeating: 1, count: 320)).count, 1)
    }
}
