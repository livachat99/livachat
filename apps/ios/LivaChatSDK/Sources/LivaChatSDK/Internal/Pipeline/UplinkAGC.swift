import Foundation

/// Makeup-only uplink AGC. Must run after VAD and before Opus encoding.
internal final class UplinkAGC {
    private let targetDbfs: Double
    private let maxGainDb: Double
    private let gateDbfs: Double
    private let ceilingDbfs: Double
    private let attackCoef: Double
    private let releaseCoef: Double

    private(set) var currentGainDb: Double = 0

    init(
        targetDbfs: Double = -20,
        maxGainDb: Double = 18,
        gateDbfs: Double = -55,
        ceilingDbfs: Double = -3,
        attackMs: Int = 20,
        releaseMs: Int = 400,
        frameMs: Int = 20
    ) {
        self.targetDbfs = targetDbfs
        self.maxGainDb = maxGainDb
        self.gateDbfs = gateDbfs
        self.ceilingDbfs = ceilingDbfs
        attackCoef = exp(-Double(frameMs) / Double(attackMs))
        releaseCoef = exp(-Double(frameMs) / Double(releaseMs))
    }

    @discardableResult
    func process(_ pcm: inout [Int16], offset: Int = 0, len: Int? = nil) -> Double {
        let available = max(0, pcm.count - offset)
        let count = min(len ?? available, available)
        guard count > 0 else { return 0 }

        var sum = 0.0
        var peak = 0
        for index in offset..<(offset + count) {
            let sample = Int(pcm[index])
            sum += Double(sample * sample)
            peak = max(peak, abs(sample))
        }

        let rms = sqrt(sum / Double(count))
        let rmsDb = 20 * log10(max(rms, 1e-9) / 32768.0)
        let peakDb = 20 * log10(max(Double(peak), 1.0) / 32768.0)

        let desiredDb: Double
        if rmsDb < gateDbfs {
            desiredDb = 0
        } else {
            desiredDb = min(max(targetDbfs - rmsDb, 0), maxGainDb)
        }

        let coefficient = desiredDb > currentGainDb ? attackCoef : releaseCoef
        currentGainDb = desiredDb + (currentGainDb - desiredDb) * coefficient

        guard peak > 0 else { return 0 }

        let appliedDb = max(0, min(currentGainDb, ceilingDbfs - peakDb))
        guard appliedDb > 0 else { return 0 }

        let gain = pow(10, appliedDb / 20)
        for index in offset..<(offset + count) {
            let value = (Double(pcm[index]) * gain).rounded()
            pcm[index] = Int16(clamping: Int(value))
        }
        return appliedDb
    }

    func reset() {
        currentGainDb = 0
    }
}
