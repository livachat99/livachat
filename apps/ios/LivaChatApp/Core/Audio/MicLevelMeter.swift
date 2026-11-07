import Foundation

/// Microphone input level meter: RMS → dBFS → `[-45, -8]` dB window →
/// attack/release → `0...levels` segments. Mirrors Android `MicLevelMeter`.
///
/// Update once per 20 ms (320 samples at 16 kHz) frame so the release
/// cadence is independent of the AVAudioEngine tap buffer size.
struct MicLevelMeter {
    static let levels = 4
    static let floorDb: Float = -45
    static let ceilDb: Float = -8
    static let releasePerFrame: Float = 0.85

    private var smoothed: Float = 0

    mutating func update(samples: [Int16], muted: Bool) -> Int {
        guard !muted, !samples.isEmpty else {
            smoothed = 0
            return 0
        }

        var sumSquares = 0.0
        for sample in samples {
            let value = Double(sample)
            sumSquares += value * value
        }
        let rms = sqrt(sumSquares / Double(samples.count))
        let db = 20.0 * log10(max(rms, 1.0) / 32_768.0)
        let span = Double(Self.ceilDb - Self.floorDb)
        let norm = Float((db - Double(Self.floorDb)) / span)
            .clamped(to: 0...1)

        // Attack instantly, release by 0.85 per frame. This is not
        // `max(norm, smoothed * 0.85)`: when `norm` sits between the
        // released value and the previous value, Android keeps releasing.
        smoothed = norm >= smoothed ? norm : smoothed * Self.releasePerFrame
        let quantized = Int((smoothed * Float(Self.levels)).rounded())
        return quantized.clamped(to: 0...Self.levels)
    }

    mutating func reset() {
        smoothed = 0
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
