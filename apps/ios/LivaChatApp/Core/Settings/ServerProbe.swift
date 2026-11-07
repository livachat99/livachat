import Foundation
import LivaChatSDK

enum ProbeResult: Equatable {
    case available
    case authFailed
    case unreachable
}

protocol ServerProbing: Sendable {
    func probe(address: ServerAddress, wireToken: String, deviceID: String) async -> ProbeResult
}

final class URLSessionServerProbe: ServerProbing, @unchecked Sendable {
    private let session: URLSession
    private let sessionDelegate: ProbeSessionDelegate?

    init(tlsPolicy: TLSPolicy = .system) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 5
        configuration.waitsForConnectivity = false

        let allowSelfSigned: Bool
        switch tlsPolicy {
        case .system:
            allowSelfSigned = false
        case .allowSelfSigned:
            allowSelfSigned = true
        }
        let delegate = ProbeSessionDelegate(allowSelfSigned: allowSelfSigned)
        sessionDelegate = delegate
        session = URLSession(
            configuration: configuration,
            delegate: delegate,
            delegateQueue: nil
        )
    }

    init(session: URLSession) {
        self.session = session
        self.sessionDelegate = nil
    }

    deinit {
        session.invalidateAndCancel()
    }

    func probe(address: ServerAddress, wireToken: String, deviceID: String) async -> ProbeResult {
        var components = URLComponents(url: address.healthURL, resolvingAgainstBaseURL: false)
        components?.queryItems = [
            URLQueryItem(name: "token", value: wireToken),
            URLQueryItem(name: "device_id", value: deviceID)
        ]
        guard let url = components?.url else { return .unreachable }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 5

        do {
            let (_, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                AppLog.warn("ServerProbe", "probe \(address.host):\(address.port) non-http response")
                return .unreachable
            }
            AppLog.info("ServerProbe", "probe \(address.host):\(address.port) status=\(http.statusCode)")
            switch http.statusCode {
            case 200:
                return .available
            case 401:
                return .authFailed
            default:
                return .unreachable
            }
        } catch {
            AppLog.warn(
                "ServerProbe",
                "probe \(address.host):\(address.port) failed: \(error.localizedDescription)"
            )
            return .unreachable
        }
    }
}

final class ProbeSessionDelegate: NSObject, URLSessionDelegate, URLSessionTaskDelegate, @unchecked Sendable {
    private let allowSelfSigned: Bool
    private let expectedHost: String?
    private let expectedPort: Int?

    init(allowSelfSigned: Bool, expectedHost: String? = nil, expectedPort: Int? = nil) {
        self.allowSelfSigned = allowSelfSigned
        self.expectedHost = expectedHost?.lowercased()
        self.expectedPort = expectedPort
    }

    func isExpected(_ space: URLProtectionSpace) -> Bool {
        if let expectedHost,
           space.host.lowercased() != expectedHost {
            return false
        }
        if let expectedPort,
           space.port != expectedPort {
            return false
        }
        return true
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        handle(challenge, completionHandler: completionHandler)
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        handle(challenge, completionHandler: completionHandler)
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        // Never follow redirects: a proxy/login page must not turn an unavailable
        // server into AVAILABLE, and the cancel token must not follow a redirect.
        completionHandler(nil)
    }

    private func handle(
        _ challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard allowSelfSigned,
              challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              isExpected(challenge.protectionSpace),
              let trust = challenge.protectionSpace.serverTrust else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}
