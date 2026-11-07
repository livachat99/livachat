import Foundation

/// Errors thrown while decoding protocol messages.
public enum ProtocolDecodingError: Error, Equatable, Sendable {
    /// The discriminator did not match any known message type.
    case unknownMessageType(type: String, seq: UInt32, timestamp: UInt64)
}

/// Minimal envelope header for seq/reorder handling before full decode.
public struct MessageHeader: Decodable, Equatable, Sendable {
    public let type: String
    public let seq: UInt32
    public let timestamp: UInt64

    public init(type: String, seq: UInt32, timestamp: UInt64) {
        self.type = type
        self.seq = seq
        self.timestamp = timestamp
    }
}

/// Codec factories for the generated DTOs.
///
/// The generated types rely on default key names (explicit `CodingKeys`) and
/// the Base64 `Data` strategy. `JSONEncoder` / `JSONDecoder` are not
/// thread-safe; create one per serial context.
public enum ProtocolJSON {
    public static func makeEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dataEncodingStrategy = .base64
        encoder.keyEncodingStrategy = .useDefaultKeys
        return encoder
    }

    public static func makeDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dataDecodingStrategy = .base64
        decoder.keyDecodingStrategy = .useDefaultKeys
        return decoder
    }
}
