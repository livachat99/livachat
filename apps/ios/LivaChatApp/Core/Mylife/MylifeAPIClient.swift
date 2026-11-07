import Foundation

enum MylifeMediaFormat: String {
    case m4a
}

final class MylifeAPIClient: @unchecked Sendable {
    private struct ErrorEnvelope: Decodable {
        struct Payload: Decodable {
            let code: String
            let message: String?
        }
        let error: Payload
    }

    private let baseURL: URL
    private let token: String
    private let jsonSession: URLSession
    private let removeSession: URLSession
    private let mediaSession: URLSession
    private let delegates: [ProbeSessionDelegate]
    private let jsonDecoder: JSONDecoder

    convenience init?(serverBase: ServerBase, token: String) {
        guard !token.isEmpty, let baseURL = URL(string: serverBase.baseURLString) else { return nil }
        let jsonDelegate = ProbeSessionDelegate(
            allowSelfSigned: serverBase.allowsSelfSigned,
            expectedHost: serverBase.host,
            expectedPort: serverBase.port
        )
        let removeDelegate = ProbeSessionDelegate(
            allowSelfSigned: serverBase.allowsSelfSigned,
            expectedHost: serverBase.host,
            expectedPort: serverBase.port
        )
        let mediaDelegate = ProbeSessionDelegate(
            allowSelfSigned: serverBase.allowsSelfSigned,
            expectedHost: serverBase.host,
            expectedPort: serverBase.port
        )
        let jsonSession = URLSession(
            configuration: Self.makeConfiguration(timeout: 10),
            delegate: jsonDelegate,
            delegateQueue: nil
        )
        let removeSession = URLSession(
            configuration: Self.makeConfiguration(timeout: 30),
            delegate: removeDelegate,
            delegateQueue: nil
        )
        let mediaSession = URLSession(
            configuration: Self.makeConfiguration(timeout: 60),
            delegate: mediaDelegate,
            delegateQueue: nil
        )
        self.init(
            baseURL: baseURL,
            token: token,
            jsonSession: jsonSession,
            removeSession: removeSession,
            mediaSession: mediaSession,
            delegates: [jsonDelegate, removeDelegate, mediaDelegate]
        )
    }

    init(
        baseURL: URL,
        token: String,
        jsonSession: URLSession,
        removeSession: URLSession? = nil,
        mediaSession: URLSession? = nil,
        delegates: [ProbeSessionDelegate] = []
    ) {
        self.baseURL = baseURL
        self.token = token
        self.jsonSession = jsonSession
        self.removeSession = removeSession ?? jsonSession
        self.mediaSession = mediaSession ?? jsonSession
        self.delegates = delegates
        self.jsonDecoder = JSONDecoder()
    }

    deinit {
        jsonSession.finishTasksAndInvalidate()
        if removeSession !== jsonSession {
            removeSession.finishTasksAndInvalidate()
        }
        if mediaSession !== jsonSession && mediaSession !== removeSession {
            mediaSession.finishTasksAndInvalidate()
        }
    }

    static func makeConfiguration(timeout: TimeInterval) -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        configuration.timeoutIntervalForRequest = timeout
        configuration.timeoutIntervalForResource = timeout
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        return configuration
    }

    // MARK: - Endpoints

    func overview(timezone: String, limit: Int? = nil, cursor: String? = nil) async throws -> OverviewResponseDTO {
        var items = [URLQueryItem(name: "timezone", value: timezone)]
        if let limit { items.append(URLQueryItem(name: "limit", value: String(limit))) }
        if let cursor, !cursor.isEmpty { items.append(URLQueryItem(name: "cursor", value: cursor)) }
        return try await get(path: "/api/mylife/overview", queryItems: items)
    }

    func timeline(
        dateFrom: String?,
        dateTo: String?,
        types: String?,
        limit: Int,
        cursor: String?,
        timezone: String?
    ) async throws -> TimelineResponseDTO {
        var items: [URLQueryItem] = []
        if let dateFrom { items.append(URLQueryItem(name: "date_from", value: dateFrom)) }
        if let dateTo { items.append(URLQueryItem(name: "date_to", value: dateTo)) }
        if let types { items.append(URLQueryItem(name: "types", value: types)) }
        items.append(URLQueryItem(name: "limit", value: String(limit)))
        if let cursor, !cursor.isEmpty { items.append(URLQueryItem(name: "cursor", value: cursor)) }
        if let timezone, !timezone.isEmpty { items.append(URLQueryItem(name: "timezone", value: timezone)) }
        return try await get(path: "/api/mylife/timeline", queryItems: items)
    }

    func search(query: String, limit: Int = MylifeFormat.searchPageLimit, cursor: String? = nil) async throws -> SearchResponseDTO {
        var items = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "limit", value: String(limit))
        ]
        if let cursor, !cursor.isEmpty { items.append(URLQueryItem(name: "cursor", value: cursor)) }
        return try await get(path: "/api/mylife/search", queryItems: items)
    }

    func removeEvents(eventRefs: [String]) async throws -> RemoveResponseDTO {
        guard let url = makeURL(path: "/api/mylife/events/remove", queryItems: []) else {
            throw MylifeAPIError(statusCode: 0, code: "internal", message: "invalid remove url")
        }
        let body = try JSONEncoder().encode(["event_refs": eventRefs])
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = body
        return try await perform(request, session: removeSession)
    }

    func download(url: URL) async throws -> Data {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 60
        let (data, response) = try await mediaSession.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw MylifeAPIError(statusCode: 0, code: "internal", message: "non-http response")
        }
        guard (200...299).contains(http.statusCode) else {
            throw MylifeAPIError(statusCode: http.statusCode, code: "internal", message: "HTTP \(http.statusCode)")
        }
        guard !data.isEmpty else {
            throw MylifeAPIError(statusCode: http.statusCode, code: "internal", message: "empty response")
        }
        return data
    }

    func mediaURL(relativePath: String, format: MylifeMediaFormat? = nil) -> URL? {
        guard !relativePath.isEmpty,
              let resolved = URL(string: relativePath, relativeTo: baseURL)?.absoluteURL,
              var components = URLComponents(url: resolved, resolvingAgainstBaseURL: false) else {
            return nil
        }
        var items = components.queryItems ?? []
        if !items.contains(where: { $0.name == "token" }) {
            items.append(URLQueryItem(name: "token", value: token))
        }
        if let format, !items.contains(where: { $0.name == "format" }) {
            items.append(URLQueryItem(name: "format", value: format.rawValue))
        }
        components.queryItems = items
        return components.url
    }

    // MARK: - Transport

    private func get<T: Decodable>(path: String, queryItems: [URLQueryItem]) async throws -> T {
        guard let url = makeURL(path: path, queryItems: queryItems) else {
            throw MylifeAPIError(statusCode: 0, code: "internal", message: "invalid url: \(path)")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await perform(request, session: jsonSession)
    }

    private func perform<T: Decodable>(_ request: URLRequest, session: URLSession) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw MylifeAPIError(statusCode: 0, code: "internal", message: "")
        }
        guard (200...299).contains(http.statusCode) else {
            throw parseError(status: http.statusCode, data: data)
        }
        guard !data.isEmpty else {
            throw MylifeAPIError(statusCode: http.statusCode, code: "internal", message: "")
        }
        do {
            return try jsonDecoder.decode(T.self, from: data)
        } catch {
            AppLog.warn("MylifeAPI", "decode failed: \(error.localizedDescription)")
            throw MylifeAPIError(statusCode: http.statusCode, code: "internal", message: "")
        }
    }

    private func makeURL(path: String, queryItems: [URLQueryItem]) -> URL? {
        guard var components = URLComponents(
            url: URL(string: path, relativeTo: baseURL)?.absoluteURL ?? baseURL,
            resolvingAgainstBaseURL: false
        ) else { return nil }
        var items = components.queryItems ?? []
        if !items.contains(where: { $0.name == "token" }) {
            items.append(URLQueryItem(name: "token", value: token))
        }
        items.append(contentsOf: queryItems)
        components.queryItems = items
        return components.url
    }

    private func parseError(status: Int, data: Data) -> MylifeAPIError {
        if let envelope = try? jsonDecoder.decode(ErrorEnvelope.self, from: data) {
            return MylifeAPIError(
                statusCode: status,
                code: envelope.error.code,
                message: envelope.error.message ?? "HTTP \(status)"
            )
        }
        return MylifeAPIError(statusCode: status, code: "internal", message: "HTTP \(status)")
    }
}
