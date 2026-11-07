import Foundation

/// Splits arbitrary PCM sample runs into fixed-size 20 ms frames.
struct PCMChunker {
    let frameSamples: Int
    private var pending: [Int16] = []

    init(frameSamples: Int = 320) {
        precondition(frameSamples > 0)
        self.frameSamples = frameSamples
    }

    mutating func append(_ samples: [Int16]) -> [[Int16]] {
        guard !samples.isEmpty else { return [] }
        pending.append(contentsOf: samples)

        var frames: [[Int16]] = []
        frames.reserveCapacity(pending.count / frameSamples)
        while pending.count >= frameSamples {
            frames.append(Array(pending.prefix(frameSamples)))
            pending.removeFirst(frameSamples)
        }
        return frames
    }

    mutating func reset() {
        pending.removeAll(keepingCapacity: true)
    }

    var pendingCount: Int { pending.count }
}
