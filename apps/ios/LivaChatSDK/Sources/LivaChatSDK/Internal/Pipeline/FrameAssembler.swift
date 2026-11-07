import Foundation

/// Accumulates arbitrary-length PCM chunks into complete 20ms frames.
internal final class FrameAssembler {
    let frameSamples: Int

    private var pending: [Int16]
    private var pendingLen = 0
    private let onFrame: ([Int16]) -> Void

    init(sampleRate: Int, frameMs: Int = 20, onFrame: @escaping ([Int16]) -> Void) {
        precondition(sampleRate > 0 && frameMs > 0)
        frameSamples = sampleRate * frameMs / 1000
        precondition(frameSamples > 0)
        pending = [Int16](repeating: 0, count: frameSamples * 2)
        self.onFrame = onFrame
    }

    func push(_ pcm: [Int16], offset: Int = 0, len: Int? = nil) {
        guard offset >= 0, offset <= pcm.count else { return }
        var off = offset
        var remaining = min(len ?? (pcm.count - offset), pcm.count - offset)
        guard remaining > 0 else { return }

        while remaining > 0 {
            let copyCount = min(frameSamples - pendingLen, remaining)
            for index in 0..<copyCount {
                pending[pendingLen + index] = pcm[off + index]
            }
            pendingLen += copyCount
            off += copyCount
            remaining -= copyCount

            if pendingLen == frameSamples {
                let frame = Array(pending[0..<frameSamples])
                // Reset before the callback so a throwing caller cannot cause
                // the same frame to be re-emitted.
                pendingLen = 0
                onFrame(frame)
            }
        }
    }

    func reset() {
        pendingLen = 0
    }
}
