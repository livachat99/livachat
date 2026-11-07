import CryptoKit
import Foundation
import Network

/// Minimal RFC 6455 server used only by transport E2E tests.
final class TestWebSocketServer: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "dev.livachat.test.ws")
    private let lock = NSLock()
    private let ready = DispatchSemaphore(value: 0)

    private var connection: NWConnection?
    private var received = Data()
    private var frames = Data()
    private var handshakeComplete = false
    private var types: [String] = []

    private(set) var port: UInt16 = 0
    var onText: ((String) -> Void)?
    var onClose: ((UInt16) -> Void)?
    var closeAfterHandshake: UInt16?
    var rejectWithUnauthorized = false
    var blackhole = false

    init() throws {
        listener = try NWListener(using: .tcp, on: .any)
    }

    func start() throws {
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            if case .ready = state {
                self.port = self.listener.port?.rawValue ?? 0
                self.ready.signal()
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }
        listener.start(queue: queue)
        guard ready.wait(timeout: .now() + 5) == .success, port != 0 else {
            throw NSError(domain: "TestWebSocketServer", code: 1)
        }
    }

    func stop() {
        lock.lock()
        let connection = self.connection
        self.connection = nil
        onText = nil
        onClose = nil
        lock.unlock()
        connection?.cancel()
        listener.cancel()
    }

    var receivedTypes: [String] {
        lock.lock()
        defer { lock.unlock() }
        return types
    }

    func sendText(_ text: String) {
        sendFrame(opcode: 0x1, payload: Data(text.utf8))
    }

    func sendBinary(_ data: Data) {
        sendFrame(opcode: 0x2, payload: data)
    }

    private func sendFrame(opcode: UInt8, payload: Data) {
        var frame = Data([0x80 | opcode])
        if payload.count < 126 {
            frame.append(UInt8(payload.count))
        } else if payload.count <= 65_535 {
            frame.append(126)
            frame.append(UInt8((payload.count >> 8) & 0xFF))
            frame.append(UInt8(payload.count & 0xFF))
        } else {
            frame.append(127)
            let length = UInt64(payload.count)
            for shift in stride(from: 56, through: 0, by: -8) {
                frame.append(UInt8((length >> UInt64(shift)) & 0xFF))
            }
        }
        frame.append(payload)
        lock.lock()
        let connection = self.connection
        lock.unlock()
        connection?.send(content: frame, completion: .contentProcessed { _ in })
    }

    func sendClose(code: UInt16) {
        var payload = Data()
        payload.append(UInt8((code >> 8) & 0xFF))
        payload.append(UInt8(code & 0xFF))
        var frame = Data([0x88, UInt8(payload.count)])
        frame.append(payload)
        lock.lock()
        let connection = self.connection
        lock.unlock()
        connection?.send(content: frame, completion: .contentProcessed { _ in
            connection?.cancel()
        })
    }

    private func accept(_ connection: NWConnection) {
        lock.lock()
        self.connection = connection
        received.removeAll()
        frames.removeAll()
        handshakeComplete = false
        lock.unlock()
        connection.start(queue: queue)
        if blackhole {
            return
        }
        receiveHandshake(connection)
    }

    private func receiveHandshake(_ connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            if let data {
                self.lock.lock()
                self.received.append(data)
                self.lock.unlock()
            }
            if error != nil || isComplete {
                connection.cancel()
                return
            }

            self.lock.lock()
            let headerEnd = self.received.range(of: Data("\r\n\r\n".utf8))
            let requestData = headerEnd.map { Data(self.received[..<$0.lowerBound]) }
            self.lock.unlock()

            guard let headerEnd, let requestData else {
                self.receiveHandshake(connection)
                return
            }

            let request = String(data: requestData, encoding: .utf8) ?? ""
            guard let key = Self.header("Sec-WebSocket-Key", in: request) else {
                connection.cancel()
                return
            }

            if self.rejectWithUnauthorized {
                let response = "HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                connection.send(content: Data(response.utf8), completion: .contentProcessed { _ in
                    connection.cancel()
                })
                return
            }

            let response = Self.handshakeResponse(key: key)
            connection.send(content: Data(response.utf8), completion: .contentProcessed { _ in })

            self.lock.lock()
            self.frames.append(contentsOf: self.received[headerEnd.upperBound...])
            self.received.removeAll()
            self.handshakeComplete = true
            self.lock.unlock()

            self.consumeFrames()
            if let closeCode = self.closeAfterHandshake {
                self.sendClose(code: closeCode)
                return
            }
            self.receiveFrames(connection)
        }
    }

    private func receiveFrames(_ connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            if let data {
                self.lock.lock()
                self.frames.append(data)
                self.lock.unlock()
            }
            if error != nil || isComplete {
                connection.cancel()
                return
            }
            self.consumeFrames()
            self.receiveFrames(connection)
        }
    }

    private func consumeFrames() {
        while true {
            lock.lock()
            let bytes = [UInt8](frames)
            lock.unlock()
            guard bytes.count >= 2 else { return }

            let opcode = bytes[0] & 0x0F
            let masked = (bytes[1] & 0x80) != 0
            var length = Int(bytes[1] & 0x7F)
            var index = 2

            if length == 126 {
                guard bytes.count >= 4 else { return }
                length = (Int(bytes[2]) << 8) | Int(bytes[3])
                index = 4
            } else if length == 127 {
                guard bytes.count >= 10 else { return }
                var value: UInt64 = 0
                for offset in 2..<10 {
                    value = (value << 8) | UInt64(bytes[offset])
                }
                guard value <= UInt64(Int.max) else {
                    connection?.cancel()
                    return
                }
                length = Int(value)
                index = 10
            }

            var mask: [UInt8] = []
            if masked {
                guard bytes.count >= index + 4 else { return }
                mask = Array(bytes[index..<(index + 4)])
                index += 4
            }
            guard bytes.count >= index + length else { return }

            var payload = Array(bytes[index..<(index + length)])
            if masked {
                for offset in payload.indices {
                    payload[offset] ^= mask[offset % 4]
                }
            }

            let frameLength = index + length
            lock.lock()
            frames.removeFirst(frameLength)
            lock.unlock()

            switch opcode {
            case 0x1:
                guard let text = String(bytes: payload, encoding: .utf8) else { continue }
                lock.lock()
                let type = (try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])?["type"] as? String
                if let type {
                    types.append(type)
                }
                let handler = onText
                lock.unlock()
                handler?(text)
            case 0x8:
                let code: UInt16 = payload.count >= 2
                    ? (UInt16(payload[0]) << 8) | UInt16(payload[1])
                    : 1005
                lock.lock()
                let handler = onClose
                let connection = self.connection
                lock.unlock()
                handler?(code)
                connection?.cancel()
                return
            case 0x9:
                // ping -> pong
                var pong = Data([0x8A])
                pong.append(UInt8(payload.count))
                pong.append(contentsOf: payload)
                lock.lock()
                let connection = self.connection
                lock.unlock()
                connection?.send(content: pong, completion: .contentProcessed { _ in })
            default:
                break
            }
        }
    }

    private static func header(_ name: String, in request: String) -> String? {
        for line in request.components(separatedBy: "\r\n") {
            let parts = line.split(separator: ":", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            if parts[0].caseInsensitiveCompare(name) == .orderedSame {
                return parts[1].trimmingCharacters(in: .whitespaces)
            }
        }
        return nil
    }

    private static func handshakeResponse(key: String) -> String {
        let magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        let digest = Insecure.SHA1.hash(data: Data((key + magic).utf8))
        let accept = Data(digest).base64EncodedString()
        return [
            "HTTP/1.1 101 Switching Protocols",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Accept: \(accept)",
            "",
            ""
        ].joined(separator: "\r\n")
    }
}
