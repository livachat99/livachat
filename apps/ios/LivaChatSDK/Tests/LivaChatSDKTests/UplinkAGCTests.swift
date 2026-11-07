import XCTest
@testable import LivaChatSDK

final class UplinkAGCTests: XCTestCase {
    func testDigitalSilenceIsUntouched() {
        let agc = UplinkAGC()
        var pcm = [Int16](repeating: 0, count: 320)
        let applied = agc.process(&pcm)
        XCTAssertEqual(applied, 0, accuracy: 1e-9)
        XCTAssertEqual(agc.currentGainDb, 0, accuracy: 1e-9)
        XCTAssertTrue(pcm.allSatisfy { $0 == 0 })
    }

    func testQuietSpeechIsBoosted() {
        let agc = UplinkAGC()
        var pcm = (0..<320).map { index in
            Int16(300.0 * sin(2 * Double.pi * 220 * Double(index) / 16_000.0))
        }
        let before = pcm.map { abs(Int($0)) }.max() ?? 0
        let applied = agc.process(&pcm)
        let after = pcm.map { abs(Int($0)) }.max() ?? 0
        XCTAssertGreaterThan(applied, 0)
        XCTAssertLessThanOrEqual(applied, 18)
        XCTAssertGreaterThan(after, before)
        XCTAssertGreaterThan(agc.currentGainDb, 0)
    }

    func testBelowNoiseGateIsNotBoosted() {
        let agc = UplinkAGC()
        var pcm = [Int16](repeating: 1, count: 320)
        let original = pcm
        let applied = agc.process(&pcm)
        XCTAssertEqual(applied, 0, accuracy: 1e-9)
        XCTAssertEqual(pcm, original)
    }

    func testPeakCeilingPreventsBoost() {
        let agc = UplinkAGC()
        var pcm = (0..<320).map { index in
            Int16(32_000.0 * sin(2 * Double.pi * 220 * Double(index) / 16_000.0))
        }
        let original = pcm
        let applied = agc.process(&pcm)
        XCTAssertEqual(applied, 0, accuracy: 1e-9)
        XCTAssertEqual(pcm, original)
    }

    func testResetReturnsToUnity() {
        let agc = UplinkAGC()
        var pcm = (0..<320).map { index in
            Int16(300.0 * sin(2 * Double.pi * 220 * Double(index) / 16_000.0))
        }
        _ = agc.process(&pcm)
        XCTAssertGreaterThan(agc.currentGainDb, 0)
        agc.reset()
        XCTAssertEqual(agc.currentGainDb, 0, accuracy: 1e-9)
    }
}
