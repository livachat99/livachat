import Foundation

#if canImport(COpus)
import COpus
#endif

internal protocol OpusEncoding: AnyObject {
    func encode(_ pcm: UnsafeBufferPointer<Int16>) throws -> Data
}

internal protocol OpusDecoding: AnyObject {
    func decode(_ opus: Data) throws -> [Int16]
}

internal enum OpusProcessorFactory {
    static func makeEncoder(sampleRate: Int) throws -> OpusEncoding {
        #if canImport(COpus)
        return try OpusEncoderImpl(sampleRate: sampleRate)
        #else
        throw LivaChatError.audioCodecUnavailable
        #endif
    }

    static func makeDecoder(sampleRate: Int) throws -> OpusDecoding {
        #if canImport(COpus)
        return try OpusDecoderImpl(sampleRate: sampleRate)
        #else
        throw LivaChatError.audioCodecUnavailable
        #endif
    }
}

#if canImport(COpus)
internal final class OpusEncoderImpl: OpusEncoding {
    private var handle: UnsafeMutableRawPointer?
    private let frameSamples: Int

    init(sampleRate: Int, channels: Int = 1) throws {
        var error: Int32 = 0
        guard let handle = lc_opus_encoder_create(
            Int32(sampleRate),
            Int32(channels),
            &error
        ), error == 0 else {
            throw LivaChatError.audioCodecUnavailable
        }
        self.handle = handle
        frameSamples = sampleRate * 20 / 1000
    }

    deinit {
        if let handle {
            lc_opus_encoder_destroy(handle)
        }
    }

    func encode(_ pcm: UnsafeBufferPointer<Int16>) throws -> Data {
        guard let handle else {
            throw LivaChatError.audioCodecUnavailable
        }
        guard pcm.count >= frameSamples, let base = pcm.baseAddress else {
            throw LivaChatError.invalidConfiguration("PCM frame too short")
        }

        var output = [UInt8](repeating: 0, count: 256)
        let bytes = output.withUnsafeMutableBufferPointer { buffer in
            lc_opus_encode(
                handle,
                base,
                Int32(frameSamples),
                buffer.baseAddress,
                Int32(buffer.count)
            )
        }
        guard bytes > 0 else {
            throw LivaChatError.audioCodecUnavailable
        }
        return Data(output.prefix(Int(bytes)))
    }
}

internal final class OpusDecoderImpl: OpusDecoding {
    private var handle: UnsafeMutableRawPointer?
    static let maxSamples = 5_760

    init(sampleRate: Int, channels: Int = 1) throws {
        var error: Int32 = 0
        guard let handle = lc_opus_decoder_create(
            Int32(sampleRate),
            Int32(channels),
            &error
        ), error == 0 else {
            throw LivaChatError.audioCodecUnavailable
        }
        self.handle = handle
    }

    deinit {
        if let handle {
            lc_opus_decoder_destroy(handle)
        }
    }

    func decode(_ opus: Data) throws -> [Int16] {
        guard let handle else {
            throw LivaChatError.audioCodecUnavailable
        }
        guard !opus.isEmpty else {
            throw LivaChatError.audioCodecUnavailable
        }

        var pcm = [Int16](repeating: 0, count: Self.maxSamples)
        let samples = opus.withUnsafeBytes { raw -> Int32 in
            guard let input = raw.bindMemory(to: UInt8.self).baseAddress else { return -1 }
            return pcm.withUnsafeMutableBufferPointer { output in
                lc_opus_decode(
                    handle,
                    input,
                    Int32(opus.count),
                    output.baseAddress,
                    Int32(output.count)
                )
            }
        }
        guard samples > 0 else {
            throw LivaChatError.audioCodecUnavailable
        }
        return Array(pcm.prefix(Int(samples)))
    }
}
#endif
