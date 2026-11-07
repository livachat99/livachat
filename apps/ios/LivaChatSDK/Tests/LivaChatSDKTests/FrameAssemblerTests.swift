import XCTest
@testable import LivaChatSDK

final class FrameAssemblerTests: XCTestCase {
    func testAssemblesAcrossChunks() {
        var frames: [[Int16]] = []
        let assembler = FrameAssembler(sampleRate: 16_000, frameMs: 20) { frames.append($0) }
        XCTAssertEqual(assembler.frameSamples, 320)

        assembler.push([Int16](repeating: 1, count: 100))
        XCTAssertTrue(frames.isEmpty)

        assembler.push([Int16](repeating: 2, count: 220))
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames[0].count, 320)
        XCTAssertEqual(frames[0].first, 1)
        XCTAssertEqual(frames[0].last, 2)
    }

    func testEmitsMultipleFrames() {
        var frames: [[Int16]] = []
        let assembler = FrameAssembler(sampleRate: 16_000, frameMs: 20) { frames.append($0) }
        assembler.push([Int16](repeating: 3, count: 640))
        XCTAssertEqual(frames.count, 2)
        XCTAssertEqual(frames[0].count, 320)
        XCTAssertEqual(frames[1].count, 320)
    }

    func testOffsetAndLength() {
        var frames: [[Int16]] = []
        let assembler = FrameAssembler(sampleRate: 16_000, frameMs: 20) { frames.append($0) }
        let pcm = (0..<400).map { Int16($0) }
        assembler.push(pcm, offset: 50, len: 320)
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames[0].first, 50)
        XCTAssertEqual(frames[0].last, 369)
    }

    func testResetDropsPartialFrame() {
        var frames: [[Int16]] = []
        let assembler = FrameAssembler(sampleRate: 16_000, frameMs: 20) { frames.append($0) }
        assembler.push([Int16](repeating: 1, count: 100))
        assembler.reset()
        assembler.push([Int16](repeating: 2, count: 220))
        XCTAssertTrue(frames.isEmpty)
        assembler.push([Int16](repeating: 2, count: 100))
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames[0].first, 2)
    }
}
