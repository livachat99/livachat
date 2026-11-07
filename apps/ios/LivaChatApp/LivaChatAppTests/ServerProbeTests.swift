import LivaChatSDK
import XCTest
@testable import LivaChatApp

final class StubURLProtocol: URLProtocol {
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

final class ServerProbeTests: XCTestCase {
    private func makeProbe() -> URLSessionServerProbe {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        configuration.timeoutIntervalForRequest = 1
        return URLSessionServerProbe(session: URLSession(configuration: configuration))
    }

    private func address() throws -> ServerAddress {
        try XCTUnwrap(ServerAddress(input: "chat.example.com:8443"))
    }

    func testMapsStatusCodesAndEncodesQuery() async throws {
        StubURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            let token = components.queryItems?.first(where: { $0.name == "token" })?.value
            let device = components.queryItems?.first(where: { $0.name == "device_id" })?.value
            XCTAssertEqual(device, "device-1")
            let status: Int
            switch token {
            case "good": status = 200
            case "bad": status = 401
            default: status = 500
            }
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: status,
                httpVersion: nil,
                headerFields: nil
            )!
            return (response, Data())
        }

        let probe = makeProbe()
        let address = try address()
        let available = await probe.probe(address: address, wireToken: "good", deviceID: "device-1")
        XCTAssertEqual(available, .available)
        let authFailed = await probe.probe(address: address, wireToken: "bad", deviceID: "device-1")
        XCTAssertEqual(authFailed, .authFailed)
        let unavailable = await probe.probe(address: address, wireToken: "other", deviceID: "device-1")
        XCTAssertEqual(unavailable, .unreachable)
    }

    func testNetworkErrorIsUnreachable() async throws {
        StubURLProtocol.handler = { _ in throw URLError(.timedOut) }
        let result = await makeProbe().probe(
            address: try address(),
            wireToken: "token",
            deviceID: "device-1"
        )
        XCTAssertEqual(result, .unreachable)
    }

    func testTLSErrorIsUnreachable() async throws {
        StubURLProtocol.handler = { _ in throw URLError(.serverCertificateUntrusted) }
        let result = await makeProbe().probe(
            address: try address(),
            wireToken: "token",
            deviceID: "device-1"
        )
        XCTAssertEqual(result, .unreachable)
    }

    func testRedirectIsRejected() {
        let delegate = ProbeSessionDelegate(allowSelfSigned: false)
        let session = URLSession.shared
        let task = session.dataTask(with: URL(string: "https://chat.example.com/healthz_token")!)
        let response = HTTPURLResponse(
            url: URL(string: "https://chat.example.com/healthz_token")!,
            statusCode: 302,
            httpVersion: nil,
            headerFields: ["Location": "https://proxy.example.com/login"]
        )!
        let expectation = expectation(description: "redirect rejected")

        delegate.urlSession(
            session,
            task: task,
            willPerformHTTPRedirection: response,
            newRequest: URLRequest(url: URL(string: "https://proxy.example.com/login")!)
        ) { request in
            XCTAssertNil(request)
            expectation.fulfill()
        }

        wait(for: [expectation], timeout: 1)
        task.cancel()
    }
}
