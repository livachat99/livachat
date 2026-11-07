import XCTest
@testable import LivaChatApp

final class AudioGalleryMathTests: XCTestCase {
    func testSegmentBodyFallback() {
        XCTAssertEqual(segmentBodyText(transcript: "abcdef", summary: "s"), "abcdef")
        XCTAssertEqual(segmentBodyText(transcript: "abcde", summary: "s"), "s")
        XCTAssertEqual(segmentBodyText(transcript: nil, summary: "s"), "s")
        XCTAssertNil(segmentBodyText(transcript: nil, summary: nil))
    }

    func testTimecodeFormatting() {
        XCTAssertEqual(formatTimecode(0), "0:00")
        XCTAssertEqual(formatTimecode(65_000), "1:05")
        XCTAssertEqual(formatTimecode(-1_000), "0:00")
        let arabic = Locale(identifier: "ar_SA")
        XCTAssertEqual(formatTimecode(0, locale: arabic), "٠:٠٠")
        XCTAssertEqual(formatTimecode(65_000, locale: arabic), "١:٠٥")
    }

    func testEnvelopeBarsNormalizesAndDegrades() {
        let full = EnergyEnvelope(frameMs: 20, dbMin: -70, dbMax: 0, values: [0, 255, 0, 255])
        let bars = envelopeBars(full, barCount: 2)
        XCTAssertEqual(bars.count, 2)
        XCTAssertEqual(bars[0], 1, accuracy: 0.001)
        XCTAssertEqual(bars[1], 1, accuracy: 0.001)

        let partial = EnergyEnvelope(frameMs: 20, dbMin: -70, dbMax: 0, values: [0, 128])
        let partialBars = envelopeBars(partial, barCount: 2)
        XCTAssertEqual(partialBars.count, 2)
        XCTAssertEqual(partialBars[0], 0, accuracy: 0.001)
        XCTAssertEqual(partialBars[1], 1, accuracy: 0.001)

        let flat = EnergyEnvelope(frameMs: 20, dbMin: -70, dbMax: 0, values: Array(repeating: 0, count: 20))
        XCTAssertTrue(envelopeBars(flat, barCount: 8).isEmpty)

        let empty = EnergyEnvelope(frameMs: 20, dbMin: -70, dbMax: 0, values: [])
        XCTAssertTrue(envelopeBars(empty, barCount: 8).isEmpty)
    }

    func testEnvelopeBarsUsesMaxPerBarAndAllowsBarsWiderThanFrames() {
        let envelope = EnergyEnvelope(frameMs: 20, dbMin: -70, dbMax: 0, values: [0, 255, 0])
        XCTAssertEqual(envelopeBars(envelope, barCount: 1).count, 1)
        XCTAssertEqual(envelopeBars(envelope, barCount: 10).count, 10)
    }
}
