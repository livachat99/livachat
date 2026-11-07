import Foundation
import XCTest
@testable import LivaChatApp

private final class CancelStubURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private func makeHTTPResponse(
    _ request: URLRequest,
    status: Int,
    body: String
) -> (HTTPURLResponse, Data) {
    let response = HTTPURLResponse(
        url: request.url!,
        statusCode: status,
        httpVersion: nil,
        headerFields: nil
    )!
    return (response, Data(body.utf8))
}

private func requestBodyData(_ request: URLRequest) -> Data? {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { return nil }
    stream.open()
    defer { stream.close() }
    var data = Data()
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 1_024)
    defer { buffer.deallocate() }
    while stream.hasBytesAvailable {
        let count = stream.read(buffer, maxLength: 1_024)
        if count <= 0 { break }
        data.append(buffer, count: count)
    }
    return data
}

final class AgentJobCancelClientTests: XCTestCase {
    private func makeClient(token: String = "token-1") -> AgentJobCancelClient {
        let configuration = AgentJobCancelClient.makeConfiguration()
        configuration.protocolClasses = [CancelStubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        return AgentJobCancelClient(
            baseURL: URL(string: "https://chat.example.com:8443")!,
            token: token,
            deviceID: "device-1",
            session: session
        )
    }

    func testRequestContractAndSuccess() async throws {
        CancelStubURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/api/mylife/agent-jobs/cancel")
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            let query = Dictionary(
                uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") }
            )
            XCTAssertEqual(query["token"], "token-1")
            XCTAssertEqual(query["device_id"], "device-1")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Content-Type"),
                "application/json; charset=utf-8"
            )
            let body = try XCTUnwrap(requestBodyData(request))
            let json = try XCTUnwrap(
                JSONSerialization.jsonObject(with: body) as? [String: String]
            )
            XCTAssertEqual(json["job_id"], "job-1")
            return makeHTTPResponse(
                request,
                status: 200,
                body: #"{"schema_version":1,"job_id":"job-1","status":"cancelled"}"#
            )
        }

        try await makeClient().cancel(jobID: "job-1")
    }

    func testEncodesTokenInQuery() async throws {
        CancelStubURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            let token = components.queryItems?.first(where: { $0.name == "token" })?.value
            XCTAssertEqual(token, "a b&c")
            return makeHTTPResponse(
                request,
                status: 200,
                body: #"{"schema_version":1,"job_id":"job-1","status":"completed"}"#
            )
        }

        try await makeClient(token: "a b&c").cancel(jobID: "job-1")
    }

    func testErrorEnvelopeIsExposed() async {
        CancelStubURLProtocol.handler = { request in
            makeHTTPResponse(
                request,
                status: 409,
                body: #"{"error":{"code":"conflict","message":"job is not active"}}"#
            )
        }

        do {
            try await makeClient().cancel(jobID: "job-1")
            XCTFail("expected error")
        } catch let error as AgentJobCancelError {
            XCTAssertEqual(error.statusCode, 409)
            XCTAssertEqual(error.code, "conflict")
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testNonEnvelopeErrorFallsBackToInternal() async {
        CancelStubURLProtocol.handler = { request in
            makeHTTPResponse(request, status: 404, body: "not found")
        }

        do {
            try await makeClient().cancel(jobID: "job-1")
            XCTFail("expected error")
        } catch let error as AgentJobCancelError {
            XCTAssertEqual(error.statusCode, 404)
            XCTAssertEqual(error.code, "internal")
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testMalformedSuccessBodyFails() async {
        CancelStubURLProtocol.handler = { request in
            makeHTTPResponse(request, status: 200, body: "{}")
        }

        do {
            try await makeClient().cancel(jobID: "job-1")
            XCTFail("expected error")
        } catch let error as AgentJobCancelError {
            XCTAssertEqual(error.statusCode, 200)
            XCTAssertEqual(error.code, "internal")
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testCancelIncompleteIsNotSilent() async {
        CancelStubURLProtocol.handler = { request in
            makeHTTPResponse(
                request,
                status: 503,
                body: #"{"error":{"code":"cancel_incomplete","message":"job is still active"}}"#
            )
        }

        do {
            try await makeClient().cancel(jobID: "job-1")
            XCTFail("expected error")
        } catch let error as AgentJobCancelError {
            XCTAssertEqual(error.statusCode, 503)
            XCTAssertEqual(error.code, "cancel_incomplete")
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testConfigurationMatchesAndroidTimeoutAndIsolation() {
        let configuration = AgentJobCancelClient.makeConfiguration()
        XCTAssertEqual(configuration.timeoutIntervalForRequest, 60)
        XCTAssertEqual(configuration.timeoutIntervalForResource, 60)
        XCTAssertFalse(configuration.waitsForConnectivity)
        XCTAssertNil(configuration.httpCookieStorage)
        XCTAssertNil(configuration.urlCache)
    }

    func testSelfSignedTrustIsScopedToExpectedHostAndPort() {
        let delegate = ProbeSessionDelegate(
            allowSelfSigned: true,
            expectedHost: "chat.example.com",
            expectedPort: 8443
        )
        let expected = URLProtectionSpace(
            host: "chat.example.com",
            port: 8443,
            protocol: "https",
            realm: nil,
            authenticationMethod: NSURLAuthenticationMethodServerTrust
        )
        let wrongHost = URLProtectionSpace(
            host: "proxy.example.com",
            port: 8443,
            protocol: "https",
            realm: nil,
            authenticationMethod: NSURLAuthenticationMethodServerTrust
        )
        let wrongPort = URLProtectionSpace(
            host: "chat.example.com",
            port: 9443,
            protocol: "https",
            realm: nil,
            authenticationMethod: NSURLAuthenticationMethodServerTrust
        )

        XCTAssertTrue(delegate.isExpected(expected))
        XCTAssertFalse(delegate.isExpected(wrongHost))
        XCTAssertFalse(delegate.isExpected(wrongPort))
    }
}
