import Foundation
import XCTest
@testable import LivaChatSDK

#if os(iOS)
final class NativeProcessorTests: XCTestCase {
    func testOpusRoundtrip() throws {
        let encoder = try OpusProcessorFactory.makeEncoder(sampleRate: 16_000)
        let decoder = try OpusProcessorFactory.makeDecoder(sampleRate: 16_000)

        let pcm = (0..<320).map { index in
            Int16(3_000.0 * sin(2 * Double.pi * 220 * Double(index) / 16_000.0))
        }
        let opus = try pcm.withUnsafeBufferPointer { buffer in
            try encoder.encode(buffer)
        }
        XCTAssertFalse(opus.isEmpty)

        let decoded = try decoder.decode(opus)
        XCTAssertEqual(decoded.count, 320)
    }

    func testVadClassifiesSilence() throws {
        let vad = try VadProcessorFactory.make(sampleRate: 16_000, sensitivity: .medium)
        let silence = [Int16](repeating: 0, count: 320)
        let state = silence.withUnsafeBufferPointer { vad.detect($0) }
        XCTAssertEqual(state, .silence)
    }

    func testVadRejectsUnsupportedSampleRate() {
        XCTAssertThrowsError(
            try VadProcessorFactory.make(sampleRate: 24_000, sensitivity: .medium)
        )
    }
}
#endif
