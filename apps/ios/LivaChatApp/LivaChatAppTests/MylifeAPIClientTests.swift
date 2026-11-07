import Foundation
import XCTest
@testable import LivaChatApp

private final class MylifeStubURLProtocol: URLProtocol {
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

private func makeMylifeResponse(_ request: URLRequest, status: Int, body: String) -> (HTTPURLResponse, Data) {
    let response = HTTPURLResponse(
        url: request.url!,
        statusCode: status,
        httpVersion: nil,
        headerFields: nil
    )!
    return (response, Data(body.utf8))
}

private func mylifeRequestBody(_ request: URLRequest) -> Data? {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { return nil }
    stream.open()
    defer { stream.close() }
    var data = Data()
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 1024)
    defer { buffer.deallocate() }
    while stream.hasBytesAvailable {
        let count = stream.read(buffer, maxLength: 1024)
        if count <= 0 { break }
        data.append(buffer, count: count)
    }
    return data
}

final class MylifeAPIClientTests: XCTestCase {
    private func makeClient() -> MylifeAPIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MylifeStubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        return MylifeAPIClient(
            baseURL: URL(string: "https://chat.example.com:8443")!,
            token: "token 1&2",
            jsonSession: session
        )
    }

    private func query(_ request: URLRequest) -> [String: String] {
        let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
        return Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") })
    }

    func testOverviewRequestContract() async throws {
        MylifeStubURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/api/mylife/overview")
            let q = self.query(request)
            XCTAssertEqual(q["token"], "token 1&2")
            XCTAssertEqual(q["timezone"], "Asia/Shanghai")
            XCTAssertEqual(q["limit"], "30")
            XCTAssertEqual(q["cursor"], "2026-08-01")
            XCTAssertNil(q["device_id"])
            return makeMylifeResponse(request, status: 200, body: #"""
            {"schema_version":1,"timezone":"Asia/Shanghai","days":[],"next_cursor":null,"total":0}
            """#)
        }

        let response = try await makeClient().overview(
            timezone: "Asia/Shanghai",
            limit: 30,
            cursor: "2026-08-01"
        )
        XCTAssertEqual(response.timezone, "Asia/Shanghai")
        XCTAssertTrue(response.days.isEmpty)
    }

    func testTimelineAndSearchQueries() async throws {
        MylifeStubURLProtocol.handler = { request in
            let q = self.query(request)
            XCTAssertEqual(q["token"], "token 1&2")
            if request.url?.path == "/api/mylife/timeline" {
                XCTAssertEqual(q["types"], "video")
                XCTAssertEqual(q["limit"], "40")
                XCTAssertEqual(q["timezone"], "Europe/Paris")
                XCTAssertNil(q["device_id"])
                return makeMylifeResponse(request, status: 200, body: #"""
                {"schema_version":1,"timezone":"Europe/Paris","days":[],"next_cursor":null,"total":0}
                """#)
            }
            if request.url?.path == "/api/mylife/search" {
                XCTAssertEqual(q["q"], "hello")
                XCTAssertEqual(q["limit"], "50")
                XCTAssertNil(q["timezone"])
                return makeMylifeResponse(request, status: 200, body: #"""
                {"schema_version":1,"query":"hello","total":0,"next_cursor":null,"hits":[]}
                """#)
            }
            XCTFail("unexpected path \(request.url?.path ?? "")")
            return makeMylifeResponse(request, status: 500, body: "{}")
        }

        _ = try await makeClient().timeline(
            dateFrom: "2026-08-01",
            dateTo: "2026-08-31",
            types: "video",
            limit: 40,
            cursor: nil,
            timezone: "Europe/Paris"
        )
        _ = try await makeClient().search(query: "hello")
    }

    func testErrorEnvelope() async {
        MylifeStubURLProtocol.handler = { request in
            makeMylifeResponse(
                request,
                status: 401,
                body: #"{"error":{"code":"unauthorized","message":"authentication failed"}}"#
            )
        }

        do {
            _ = try await makeClient().overview(timezone: "Asia/Shanghai")
            XCTFail("expected error")
        } catch let error as MylifeAPIError {
            XCTAssertEqual(error.statusCode, 401)
            XCTAssertEqual(error.code, "unauthorized")
            XCTAssertEqual(error.message, "authentication failed")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testDecodeFailureDoesNotExposeTechnicalMessage() async {
        MylifeStubURLProtocol.handler = { request in
            makeMylifeResponse(request, status: 200, body: "not json")
        }

        do {
            _ = try await makeClient().overview(timezone: "Asia/Shanghai")
            XCTFail("expected error")
        } catch let error as MylifeAPIError {
            XCTAssertEqual(error.code, "internal")
            XCTAssertTrue(error.message.isEmpty)
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testRemoveRequestContract() async throws {
        MylifeStubURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/api/mylife/events/remove")
            XCTAssertEqual(self.query(request)["token"], "token 1&2")
            XCTAssertNil(self.query(request)["device_id"])
            let body = try XCTUnwrap(mylifeRequestBody(request))
            let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: [String]])
            XCTAssertEqual(json["event_refs"], ["r1", "r2"])
            return makeMylifeResponse(
                request,
                status: 200,
                body: #"{"removed":["r1"],"skipped":[{"event_ref":"r2","reason":"not_found"}]}"#
            )
        }

        let response = try await makeClient().removeEvents(eventRefs: ["r1", "r2"])
        XCTAssertEqual(response.removed, ["r1"])
        XCTAssertEqual(response.skipped.first?.reason, "not_found")
    }

    func testMediaURLAddsTokenAndOptionalFormat() {
        let client = makeClient()
        let image = client.mediaURL(relativePath: "/api/mylife/media/d/s/video/f.jpg")
        XCTAssertEqual(image?.host, "chat.example.com")
        XCTAssertEqual(image?.port, 8443)
        let imageQuery = URLComponents(url: image!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        XCTAssertEqual(imageQuery.first(where: { $0.name == "token" })?.value, "token 1&2")
        XCTAssertNil(imageQuery.first(where: { $0.name == "format" }))

        let audio = client.mediaURL(relativePath: "/api/mylife/media/d/s/audio/a.ogg", format: .m4a)
        let audioQuery = URLComponents(url: audio!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        XCTAssertEqual(audioQuery.first(where: { $0.name == "token" })?.value, "token 1&2")
        XCTAssertEqual(audioQuery.first(where: { $0.name == "format" })?.value, "m4a")
    }
}
