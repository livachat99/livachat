import XCTest
@testable import LivaChatApp

final class MicLevelMeterTests: XCTestCase {
    private let frameSamples = 320

    private func frame(amplitude: Int16) -> [Int16] {
        Array(repeating: amplitude, count: frameSamples)
    }

    private func fullScaleFrame() -> [Int16] {
        (0..<frameSamples).map { index in
            index.isMultiple(of: 2) ? 32_767 : -32_768
        }
    }

    func testSilenceIsZero() {
        var meter = MicLevelMeter()
        XCTAssertEqual(meter.update(samples: frame(amplitude: 0), muted: false), 0)
    }

    func testFullScaleIsTopLevel() {
        var meter = MicLevelMeter()
        XCTAssertEqual(meter.update(samples: fullScaleFrame(), muted: false), MicLevelMeter.levels)
    }

    func testQuietInputIsZero() {
        var meter = MicLevelMeter()
        XCTAssertEqual(meter.update(samples: frame(amplitude: 30), muted: false), 0)
    }

    func testNormalSpeechIsTwoOrThree() {
        var meter = MicLevelMeter()
        let level = meter.update(samples: frame(amplitude: 3_277), muted: false)
        XCTAssertTrue((2...3).contains(level), "unexpected level \(level)")
    }

    func testMutedClearsSmoothing() {
        var meter = MicLevelMeter()
        XCTAssertEqual(meter.update(samples: fullScaleFrame(), muted: false), 4)
        for _ in 0..<5 {
            XCTAssertEqual(meter.update(samples: fullScaleFrame(), muted: true), 0)
        }
        XCTAssertEqual(meter.update(samples: frame(amplitude: 1_920), muted: false), 2)
    }

    func testFastAttack() {
        var meter = MicLevelMeter()
        for _ in 0..<10 {
            _ = meter.update(samples: frame(amplitude: 0), muted: false)
        }
        XCTAssertEqual(meter.update(samples: fullScaleFrame(), muted: false), 4)
    }

    func testReleaseDoesNotFollowIntermediateNorm() {
        var meter = MicLevelMeter()
        XCTAssertEqual(meter.update(samples: fullScaleFrame(), muted: false), 4)
        // norm(8519) ≈ 0.9. Android releases to 0.85 → 3 segments; a
        // `max(norm, smoothed * 0.85)` implementation would return 4.
        XCTAssertEqual(meter.update(samples: frame(amplitude: 8_519), muted: false), 3)
    }

    func testSlowReleaseReachesZeroAroundThirteenFrames() {
        var meter = MicLevelMeter()
        XCTAssertEqual(meter.update(samples: fullScaleFrame(), muted: false), 4)

        var levels: [Int] = []
        for _ in 0..<40 {
            levels.append(meter.update(samples: frame(amplitude: 0), muted: false))
        }

        XCTAssertEqual(levels, levels.sorted(by: >), "release must be monotonic")
        guard let firstZero = levels.firstIndex(of: 0) else {
            return XCTFail("meter never reached zero")
        }
        XCTAssertTrue(
            (11...15).contains(firstZero + 1),
            "first zero after \(firstZero + 1) frames"
        )
    }

    func testEmptyFrameIsZero() {
        var meter = MicLevelMeter()
        XCTAssertEqual(meter.update(samples: [], muted: false), 0)
    }

    func testResetClearsSmoothing() {
        var meter = MicLevelMeter()
        XCTAssertEqual(meter.update(samples: fullScaleFrame(), muted: false), 4)
        meter.reset()
        XCTAssertEqual(meter.update(samples: frame(amplitude: 1_920), muted: false), 2)
        meter.reset()
        XCTAssertEqual(meter.update(samples: frame(amplitude: 0), muted: false), 0)
    }
}
