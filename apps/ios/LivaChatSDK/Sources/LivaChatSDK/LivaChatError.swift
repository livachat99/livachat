import Foundation

public enum LivaChatError: Error, Sendable, Equatable {
    case invalidConfiguration(String)
    case unsupportedSampleRate(Int)
    case unsupportedFrameDuration(Int)
    case audioCodecUnavailable
    case vadUnavailable
}
