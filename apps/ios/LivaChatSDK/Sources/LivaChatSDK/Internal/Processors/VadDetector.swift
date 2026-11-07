import Foundation

#if canImport(CVad)
import CVad
#endif

internal protocol VadDetecting: AnyObject {
    func detect(_ pcm: UnsafeBufferPointer<Int16>) -> VadState
}

internal enum VadProcessorFactory {
    static func make(sampleRate: Int, sensitivity: VadSensitivity) throws -> VadDetecting {
        #if canImport(CVad)
        return try VadDetectorImpl(sampleRate: sampleRate, sensitivity: sensitivity)
        #else
        throw LivaChatError.vadUnavailable
        #endif
    }
}

#if canImport(CVad)
internal final class VadDetectorImpl: VadDetecting {
    private var handle: UnsafeMutableRawPointer?
    private let sampleRate: Int
    private let frameSamples: Int

    init(sampleRate: Int, sensitivity: VadSensitivity) throws {
        frameSamples = sampleRate * 20 / 1000
        guard lc_vad_valid_rate_and_frame_length(Int32(sampleRate), frameSamples) == 0 else {
            throw LivaChatError.unsupportedSampleRate(sampleRate)
        }
        guard let handle = lc_vad_create(Int32(sensitivity.rawValue)) else {
            throw LivaChatError.vadUnavailable
        }
        self.handle = handle
        self.sampleRate = sampleRate
    }

    deinit {
        if let handle {
            lc_vad_destroy(handle)
        }
    }

    func detect(_ pcm: UnsafeBufferPointer<Int16>) -> VadState {
        guard let handle, pcm.count >= frameSamples, let base = pcm.baseAddress else {
            return .unknown
        }
        let result = lc_vad_process(
            handle,
            Int32(sampleRate),
            base,
            frameSamples
        )
        switch result {
        case 1: return .speech
        case 0: return .silence
        default: return .unknown
        }
    }
}
#endif
