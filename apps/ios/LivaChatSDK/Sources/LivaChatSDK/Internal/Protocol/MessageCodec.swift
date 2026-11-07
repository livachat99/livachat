import Foundation
import LivaChatProtocol

internal enum CodecError: Error {
    case encodingFailed(Error)
    case invalidUTF8
}

/// JSON codec for protocol messages. Instances are not thread-safe; each
/// serial context must own one.
internal final class MessageCodec: @unchecked Sendable {
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init() {
        encoder = ProtocolJSON.makeEncoder()
        decoder = ProtocolJSON.makeDecoder()
    }

    func encode(_ message: ClientMessage) throws -> String {
        do {
            let data = try encoder.encode(message)
            guard let text = String(data: data, encoding: .utf8) else {
                throw CodecError.invalidUTF8
            }
            return text
        } catch let error as CodecError {
            throw error
        } catch {
            throw CodecError.encodingFailed(error)
        }
    }

    func decode(_ text: String) -> ServerMessage? {
        guard let data = text.data(using: .utf8) else { return nil }
        return try? decoder.decode(ServerMessage.self, from: data)
    }
}
