import Foundation

final class AgentJobCancelClient: AgentJobCanceling, @unchecked Sendable {
    private struct RequestBody: Encodable {
        let jobID: String

        enum CodingKeys: String, CodingKey {
            case jobID = "job_id"
        }
    }

    private struct ResponseBody: Decodable {
        let schemaVersion: Int?
        let jobID: String
        let status: String

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case jobID = "job_id"
            case status
        }
    }

    private struct ErrorEnvelope: Decodable {
        struct Payload: Decodable {
            let code: String
            let message: String?
        }

        let error: Payload
    }

    private let baseURL: URL
    private let token: String
    private let deviceID: String
    private let session: URLSession

    convenience init?(serverBase: ServerBase, token: String, deviceID: String) {
        guard !token.isEmpty,
              !deviceID.isEmpty,
              let url = URL(string: serverBase.baseURLString) else {
            return nil
        }
        let configuration = Self.makeConfiguration()
        let delegate = ProbeSessionDelegate(
            allowSelfSigned: serverBase.allowsSelfSigned,
            expectedHost: serverBase.host,
            expectedPort: serverBase.port
        )
        let session = URLSession(
            configuration: configuration,
            delegate: delegate,
            delegateQueue: nil
        )
        self.init(baseURL: url, token: token, deviceID: deviceID, session: session)
    }

    init(baseURL: URL, token: String, deviceID: String, session: URLSession) {
        self.baseURL = baseURL
        self.token = token
        self.deviceID = deviceID
        self.session = session
    }

    deinit {
        session.finishTasksAndInvalidate()
    }

    static func makeConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 60
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        return configuration
    }

    func cancel(jobID: String) async throws {
        var components = URLComponents(
            url: baseURL,
            resolvingAgainstBaseURL: false
        )
        components?.path = "/api/mylife/agent-jobs/cancel"
        components?.queryItems = [
            URLQueryItem(name: "token", value: token),
            URLQueryItem(name: "device_id", value: deviceID)
        ]
        guard let url = components?.url else {
            throw AgentJobCancelError(
                statusCode: 0,
                code: "internal",
                message: "invalid cancel url"
            )
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(RequestBody(jobID: jobID))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            let code = (error as? URLError)?.code.rawValue ?? -1
            AppLog.warn("AgentJobCancel", "request failed urlerror=\(code)")
            throw error
        }

        guard let http = response as? HTTPURLResponse else {
            throw AgentJobCancelError(
                statusCode: 0,
                code: "internal",
                message: "non-http response"
            )
        }

        if (200...299).contains(http.statusCode) {
            do {
                _ = try JSONDecoder().decode(ResponseBody.self, from: data)
                AppLog.info("AgentJobCancel", "cancel status=\(http.statusCode)")
                return
            } catch {
                AppLog.warn("AgentJobCancel", "invalid success body status=\(http.statusCode)")
                throw AgentJobCancelError(
                    statusCode: http.statusCode,
                    code: "internal",
                    message: "invalid response body"
                )
            }
        }

        let envelope = try? JSONDecoder().decode(ErrorEnvelope.self, from: data)
        let code = envelope?.error.code ?? "internal"
        let message = envelope?.error.message ?? "HTTP \(http.statusCode)"
        AppLog.info(
            "AgentJobCancel",
            "cancel failed status=\(http.statusCode) code=\(code)"
        )
        throw AgentJobCancelError(
            statusCode: http.statusCode,
            code: code,
            message: message
        )
    }
}
