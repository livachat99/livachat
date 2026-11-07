import Foundation
import XCTest
@testable import LivaChatApp

private final class MylifeStoreStubURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var overviewCallCount = 0

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

private func storeResponse(_ request: URLRequest, _ body: String) -> (HTTPURLResponse, Data) {
    let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
    return (response, Data(body.utf8))
}

private func storeQuery(_ request: URLRequest) -> [String: String] {
    let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
    return Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") })
}

private func dayJSON(
    _ date: String,
    pending: Bool = false,
    summary: String? = "A",
    video: Int = 1,
    audio: Int = 0
) -> String {
    let summaryJSON = summary.map { "\"\($0)\"" } ?? "null"
    return """
    {"date":"\(date)","counts":{"video":\(video),"audio":\(audio)},"summary":\(summaryJSON),
     "hot_slots":[{"start_ms":1,"end_ms":2,"count":3}],
     "images":[{"frame_id":"f-\(date)","ts_ms":1,"event_ref":"r-\(date)","media_url":"/api/mylife/media/d/s/video/f.jpg","window_id":"w"}],
     "has_pending":\(pending),
     "topics":[{"topic":"topic-\(date)","search_words":"word"}]}
    """
}

private func overviewJSON(days: [String], timezone: String = "Asia/Shanghai", next: String? = nil, total: Int = 1) -> String {
    let nextJSON = next.map { "\"\($0)\"" } ?? "null"
    return """
    {"schema_version":1,"timezone":"\(timezone)","days":[\(days.joined(separator: ","))],
     "next_cursor":\(nextJSON),"total":\(total)}
    """
}

@MainActor
final class MylifeStoreTests: XCTestCase {
    private func makeStore() -> MylifeStore {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MylifeStoreStubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let api = MylifeAPIClient(
            baseURL: URL(string: "https://chat.example.com:8443")!,
            token: "token",
            jsonSession: session
        )
        return MylifeStore(api: api)
    }

    override func setUp() {
        super.setUp()
        MylifeStoreStubURLProtocol.overviewCallCount = 0
    }

    func testOverviewMergeAndDeeperCursor() async throws {
        MylifeStoreStubURLProtocol.handler = { request in
            let q = storeQuery(request)
            if q["cursor"] == nil {
                return storeResponse(request, overviewJSON(
                    days: [dayJSON("2026-08-21", pending: true), dayJSON("2026-08-20")],
                    next: "2026-08-19",
                    total: 3
                ))
            }
            return storeResponse(request, overviewJSON(
                days: [dayJSON("2026-08-19")],
                next: nil,
                total: 3
            ))
        }

        let store = makeStore()
        try await store.refreshOverview(force: true, limit: 30)
        XCTAssertEqual(store.overviewWindow?.days.map(\.date), ["2026-08-21", "2026-08-20"])
        XCTAssertEqual(store.overviewWindow?.nextCursor, "2026-08-19")
        XCTAssertTrue(store.hasPendingAnalysis())
        XCTAssertEqual(store.overviewWindow?.days.first?.images.first?.url.host, "chat.example.com")

        let loaded = try await store.loadMoreOverview(limit: 30)
        XCTAssertTrue(loaded)
        XCTAssertEqual(store.overviewWindow?.days.map(\.date), ["2026-08-21", "2026-08-20", "2026-08-19"])
        XCTAssertNil(store.overviewWindow?.nextCursor)
    }

    func testOverviewTimezoneChangeReplacesWindow() async throws {
        MylifeStoreStubURLProtocol.handler = { request in
            MylifeStoreStubURLProtocol.overviewCallCount += 1
            if MylifeStoreStubURLProtocol.overviewCallCount == 1 {
                return storeResponse(request, overviewJSON(days: [dayJSON("2026-08-21")], timezone: "Asia/Shanghai"))
            }
            return storeResponse(request, overviewJSON(days: [dayJSON("2026-08-20")], timezone: "Europe/Paris"))
        }

        let store = makeStore()
        try await store.refreshOverview(force: true, limit: 30)
        try await store.refreshOverview(force: true, limit: 30)
        XCTAssertEqual(store.overviewWindow?.timezone, "Europe/Paris")
        XCTAssertEqual(store.overviewWindow?.days.map(\.date), ["2026-08-20"])
    }

    func testHotSlotEventsFiltersWindowAndMapsAgentFallback() async throws {
        MylifeStoreStubURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/mylife/timeline")
            let body = """
            {"schema_version":1,"timezone":"Asia/Shanghai","days":[{"date":"2026-08-21","events":[
              {"ts_ms":100,"type":"video","frame_id":"f1","media_url":"/api/mylife/media/d/s/video/f1.jpg","event_ref":"v1","caption":"cap"},
              {"ts_ms":120,"type":"chat","role":"user","text":"hi","event_ref":"c1"},
              {"ts_ms":150,"type":"agent_job","role":"assistant","summary":"agent summary","event_ref":"a1"},
              {"ts_ms":200,"type":"chat","role":"assistant","text":"later","event_ref":"c2"}
            ]}],"next_cursor":null,"total":4}
            """
            return storeResponse(request, body)
        }

        let store = makeStore()
        let page = try await store.hotSlotEvents(startMs: 90, endMs: 160)
        XCTAssertEqual(page.events.map(\.tsMs), [100, 120, 150])
        XCTAssertEqual(page.events[0].mediaUrl?.host, "chat.example.com")
        XCTAssertEqual(page.events[1].text, "hi")
        XCTAssertEqual(page.events[2].text, "agent summary")
        XCTAssertTrue(page.reachedWindowEnd)
    }

    func testAudioSegmentsAddsM4AAndDecodesEnergy() async throws {
        MylifeStoreStubURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/mylife/timeline")
            XCTAssertEqual(storeQuery(request)["types"], "audio")
            let body = """
            {"schema_version":1,"timezone":"Asia/Shanghai","days":[{"date":"2026-08-21","events":[
              {"ts_ms":100,"type":"audio","media_url":"/api/mylife/media/d/s/audio/a.ogg","event_ref":"a1",
               "transcript":"hello","audio_summary":"summary","duration_s":1.5,
               "energy_v":1,"frame_ms":20,"db_min":-70.0,"db_max":0.0,"energy":"AP8="}
            ]}],"next_cursor":null,"total":1}
            """
            return storeResponse(request, body)
        }

        let store = makeStore()
        let page = try await store.audioSegments(date: "2026-08-21")
        let segment = try XCTUnwrap(page.segments.first)
        XCTAssertEqual(segment.eventRef, "a1")
        XCTAssertEqual(segment.transcript, "hello")
        XCTAssertEqual(segment.summary, "summary")
        XCTAssertEqual(segment.durationMs, 1500)
        XCTAssertEqual(segment.energy?.values, [0, 255])
        let components = URLComponents(url: segment.mediaUrl, resolvingAgainstBaseURL: false)!
        let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        XCTAssertEqual(query["format"], "m4a")
        XCTAssertEqual(query["token"], "token")
    }

    func testAudioSegmentsClampOverflowingDurationAndEnergyBounds() async throws {
        MylifeStoreStubURLProtocol.handler = { request in
            let body = """
            {"schema_version":1,"timezone":"Asia/Shanghai","days":[{"date":"2026-08-21","events":[
              {"ts_ms":100,"type":"audio","media_url":"/api/mylife/media/d/s/audio/a.ogg","event_ref":"a1",
               "duration_s":1e308,
               "energy_v":1,"frame_ms":20,"db_min":-1e308,"db_max":1e308,"energy":"AP8="}
            ]}],"next_cursor":null,"total":1}
            """
            return storeResponse(request, body)
        }

        let store = makeStore()
        let page = try await store.audioSegments(date: "2026-08-21")
        XCTAssertEqual(page.segments.first?.durationMs, 0)
        XCTAssertNil(page.segments.first?.energy)
    }

    func testFramesSkipMissingMediaOrFrameId() async throws {
        MylifeStoreStubURLProtocol.handler = { request in
            let body = """
            {"schema_version":1,"timezone":"Asia/Shanghai","days":[{"date":"2026-08-21","events":[
              {"ts_ms":100,"type":"video","frame_id":"f1","media_url":"/api/mylife/media/d/s/video/f1.jpg","event_ref":"r1","caption":"c"},
              {"ts_ms":110,"type":"video","media_url":"/api/mylife/media/d/s/video/f2.jpg","event_ref":"r2"},
              {"ts_ms":120,"type":"video","frame_id":"f3","event_ref":"r3"}
            ]}],"next_cursor":null,"total":3}
            """
            return storeResponse(request, body)
        }

        let store = makeStore()
        let page = try await store.frames(from: "2026-08-21", to: "2026-08-21")
        XCTAssertEqual(page.frames.map(\.frameId), ["f1"])
        XCTAssertEqual(page.total, 3)
    }

    func testSearchReturnsHits() async throws {
        MylifeStoreStubURLProtocol.handler = { request in
            let body = """
            {"schema_version":1,"query":"hi","total":1,"next_cursor":null,
             "hits":[{"date":"2026-08-21","ts_ms":120,"kind":"chat","snippet":"hi there"}]}
            """
            return storeResponse(request, body)
        }
        let store = makeStore()
        let response = try await store.search(query: "hi", cursor: nil)
        XCTAssertEqual(response.hits.first?.kind, "chat")
        XCTAssertEqual(response.hits.first?.tsMs, 120)
    }
}
