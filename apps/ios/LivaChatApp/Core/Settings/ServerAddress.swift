import CryptoKit
import Foundation

struct ServerAddress: Equatable {
    enum Scheme: Equatable {
        case secure

        var webSocket: String { "wss" }
        var http: String { "https" }
    }

    let host: String
    let port: Int
    let scheme: Scheme

    init(host: String, port: Int, scheme: Scheme = .secure) {
        self.host = host
        self.port = port
        self.scheme = scheme
    }

    /// Parses `ip:port` or `domain:port`. Product settings intentionally do
    /// not accept a scheme; development overrides use a full URL elsewhere.
    init?(input: String) {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              !trimmed.contains("://"),
              !trimmed.contains("/"),
              !trimmed.contains("?"),
              !trimmed.contains("#"),
              let components = URLComponents(string: "wss://\(trimmed)"),
              let host = components.host,
              !host.isEmpty,
              !host.contains("%"),
              host.rangeOfCharacter(from: .whitespacesAndNewlines) == nil,
              let port = components.port,
              (1...65_535).contains(port),
              components.user == nil,
              components.password == nil,
              components.path.isEmpty,
              components.query == nil,
              components.fragment == nil
        else {
            return nil
        }

        var normalizedHost = host.lowercased()
        if normalizedHost.hasPrefix("["), normalizedHost.hasSuffix("]") {
            normalizedHost.removeFirst()
            normalizedHost.removeLast()
        }
        self.host = normalizedHost
        self.port = port
        self.scheme = .secure
    }

    var normalizedInput: String {
        host.contains(":") ? "[\(host)]:\(port)" : "\(host):\(port)"
    }

    var webSocketURL: URL {
        var components = URLComponents()
        components.scheme = scheme.webSocket
        components.host = host
        components.port = port
        components.path = "/v1/ws/stream"
        return components.url!
    }

    var healthURL: URL {
        var components = URLComponents()
        components.scheme = scheme.http
        components.host = host
        components.port = port
        components.path = "/healthz_token"
        return components.url!
    }
}

/// HTTP base derived from the live WebSocket endpoint, used to resolve
/// `livachat_url://webroot/...` links (wss→https, ws→http).
struct ServerBase: Equatable {
    enum Scheme: String, Equatable {
        case https
        case http
    }

    let scheme: Scheme
    let host: String
    let port: Int
    let allowsSelfSigned: Bool

    init?(webSocketURL: URL, allowsSelfSigned: Bool) {
        guard let rawScheme = webSocketURL.scheme?.lowercased(),
              rawScheme == "wss" || rawScheme == "ws",
              let host = webSocketURL.host,
              !host.isEmpty else {
            return nil
        }
        self.scheme = rawScheme == "wss" ? .https : .http
        self.host = host.lowercased()
        self.port = webSocketURL.port ?? (rawScheme == "wss" ? 443 : 80)
        self.allowsSelfSigned = allowsSelfSigned
    }

    var baseURLString: String {
        let hostPart = host.contains(":") ? "[\(host)]" : host
        return "\(scheme.rawValue)://\(hostPart):\(port)"
    }

    func isWebrootURL(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == scheme.rawValue,
              url.host?.lowercased() == host,
              effectivePort(of: url) == port else {
            return false
        }
        return url.path.hasPrefix("/webroot/")
    }

    private func effectivePort(of url: URL) -> Int? {
        if let port = url.port { return port }
        switch scheme {
        case .https: return 443
        case .http: return 80
        }
    }
}

enum WireToken {
    /// Protocol-required `sha1_hex(secret + "LivaChat")`. SHA1 is a protocol
    /// compatibility choice, not a security primitive.
    static func make(secret: String) -> String {
        let digest = Insecure.SHA1.hash(data: Data((secret + "LivaChat").utf8))
        return digest.map { PosixFormat.string("%02x", $0) }.joined()
    }
}
