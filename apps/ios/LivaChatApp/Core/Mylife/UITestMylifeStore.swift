#if DEBUG
import Combine
import Foundation

/// In-process MyLife fixture for UI tests. Never touches the network.
@MainActor
final class UITestMylifeStore: ObservableObject, MylifeServicing {
    private let subject: CurrentValueSubject<OverviewWindow?, Never>
    private let fixture: String
    private let downloadHandler: ((URL) async throws -> Data)?

    var overviewWindow: OverviewWindow? { subject.value }
    var overviewPublisher: AnyPublisher<OverviewWindow?, Never> {
        subject.eraseToAnyPublisher()
    }

    init(fixture: String = "home", downloadHandler: ((URL) async throws -> Data)? = nil) {
        self.fixture = fixture
        self.downloadHandler = downloadHandler
        self.subject = CurrentValueSubject(Self.makeWindow(fixture: fixture))
    }

    func refreshOverview(force: Bool, limit: Int, cursor: String?) async throws {
        if cursor == nil {
            subject.send(Self.makeWindow(fixture: fixture))
        }
    }

    func loadMoreOverview(limit: Int) async throws -> Bool { false }
    func hasPendingAnalysis() -> Bool {
        subject.value?.days.contains { $0.hasPending } ?? false
    }

    func search(query: String, cursor: String?) async throws -> SearchResponseDTO {
        guard cursor == nil else {
            return SearchResponseDTO(
                schemaVersion: 1,
                query: query,
                total: 2,
                nextCursor: nil,
                hits: []
            )
        }
        return Self.makeSearch(query: query)
    }

    func hotSlotEvents(startMs: Int64, endMs: Int64, cursor: String?) async throws -> HotSlotPage {
        let base = startMs + 60_000
        let events = [
            HotSlotEvent(tsMs: base, type: EventType.chat, role: "user", text: "Hello from this slot", frameId: nil, mediaUrl: nil, caption: nil, eventRef: "uitest-chat"),
            HotSlotEvent(
                tsMs: base + 60_000,
                type: EventType.agentJob,
                role: nil,
                text: "Agent result **ready** — a longer summary so the bubble spans the same maximum width as conversation and history messages.",
                frameId: nil,
                mediaUrl: nil,
                caption: nil,
                eventRef: "uitest-agent"
            ),
            HotSlotEvent(tsMs: base + 120_000, type: EventType.transcribe, role: "user", text: "Transcript text", frameId: nil, mediaUrl: nil, caption: nil, eventRef: "uitest-transcribe")
        ].filter { $0.tsMs >= startMs && $0.tsMs <= endMs }
        return HotSlotPage(events: events, nextCursor: nil, reachedWindowEnd: true)
    }

    func frames(from: String, to: String, cursor: String?) async throws -> FramePage {
        let frames = (1...8).map { index in
            FrameItem(
                tsMs: Self.timestamp(hour: 9, minute: 30) + Int64(index) * 1_000,
                mediaUrl: URL(string: "https://example.invalid/frame\(index).jpg")!,
                frameId: index == 1 ? "uitest-frame" : "uitest-frame-\(index)",
                windowId: "w",
                caption: "Frame \(index) caption",
                eventRef: "uitest-image-\(index)"
            )
        }
        return FramePage(frames: frames, total: frames.count, nextCursor: nil)
    }

    func audioSegments(date: String, cursor: String?) async throws -> AudioPage {
        let base = Self.timestamp(hour: 9, minute: 20)
        let segments = [
            AudioSegment(
                eventRef: "uitest-audio-1",
                tsMs: base,
                mediaUrl: URL(string: "https://example.invalid/a1.ogg?format=m4a")!,
                transcript: "This is a transcript long enough to render as the body.",
                summary: "Short summary",
                durationMs: 1_000,
                energy: nil
            ),
            AudioSegment(
                eventRef: "uitest-audio-2",
                tsMs: base + 5 * 60_000,
                mediaUrl: URL(string: "https://example.invalid/a2.ogg?format=m4a")!,
                transcript: "短",
                summary: "A fallback summary for the second segment.",
                durationMs: 1_000,
                energy: nil
            )
        ]
        return AudioPage(segments: segments, total: segments.count, nextCursor: nil)
    }

    func removeEvents(eventRefs: [String]) async throws -> RemoveResponseDTO {
        throw MylifeAPIError(statusCode: 503, code: "uitest", message: "not available")
    }

    func downloadMedia(url: URL) async throws -> Data {
        if let downloadHandler {
            return try await downloadHandler(url)
        }
        return Self.silentWavData()
    }

    func markRemovalRefreshPending() {}
    func flushRemovalRefreshIfNeeded() {}
    func startPolling() {}
    func stopPolling() {}

    private static func makeWindow(fixture: String) -> OverviewWindow {
        if fixture == "empty" {
            return OverviewWindow(timezone: TimeZone.current.identifier, days: [], nextCursor: nil, total: 0)
        }
        let days = [
            day("2026-08-21", pending: true, summary: "Morning walk and a long conversation about travel plans.",
                slots: [slot(9, 12), slot(14, 0)], audio: 2,
                topics: [("travel", "travel plans"), ("morning", "morning walk")],
                video: 8,
                images: [MylifeDayImage(
                    url: URL(string: "https://example.invalid/frame.jpg")!,
                    tsMs: timestamp(hour: 9, minute: 30),
                    eventRef: "uitest-image",
                    frameId: "uitest-frame",
                    windowId: "w"
                )]),
            day("2026-08-20", summary: "Worked on the timeline design and reviewed the gallery.",
                slots: [slot(10, 30)], audio: 0,
                topics: [("design", "timeline design")]),
            day("2026-08-19", summary: "Quiet day at home.",
                slots: [], audio: 1,
                topics: [("home", "quiet day")])
        ]
        return OverviewWindow(
            timezone: TimeZone.current.identifier,
            days: days,
            nextCursor: nil,
            total: days.count
        )
    }

    private static func day(
        _ date: String,
        pending: Bool = false,
        summary: String?,
        slots: [MylifeHotSlot],
        audio: Int,
        topics: [(String, String?)],
        video: Int = 0,
        images: [MylifeDayImage] = []
    ) -> MylifeDay {
        MylifeDay(
            date: date,
            counts: [EventType.video: video, EventType.audio: audio],
            summary: summary,
            hotSlots: slots,
            images: images,
            hasPending: pending,
            topics: topics.map { MylifeDayTopic(topic: $0.0, searchWords: $0.1) }
        )
    }

    private static func slot(_ hour: Int, _ minute: Int) -> MylifeHotSlot {
        let start = timestamp(hour: hour, minute: minute)
        return MylifeHotSlot(startMs: start, endMs: start + 30 * 60_000)
    }

    private static func timestamp(hour: Int, minute: Int) -> Int64 {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        let date = calendar.date(from: DateComponents(
            year: 2026, month: 8, day: 21, hour: hour, minute: minute
        )) ?? Date()
        return Int64(date.timeIntervalSince1970 * 1000)
    }

    private static func silentWavData() -> Data {
        let sampleRate = 16_000
        let frames = sampleRate * 3
        let dataSize = frames * 2
        var data = Data()
        func appendLE32(_ value: UInt32) {
            var v = value.littleEndian
            withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
        }
        func appendLE16(_ value: UInt16) {
            var v = value.littleEndian
            withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
        }
        data.append(contentsOf: Array("RIFF".utf8))
        appendLE32(UInt32(36 + dataSize))
        data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8))
        appendLE32(16)
        appendLE16(1)
        appendLE16(1)
        appendLE32(UInt32(sampleRate))
        appendLE32(UInt32(sampleRate * 2))
        appendLE16(2)
        appendLE16(16)
        data.append(contentsOf: Array("data".utf8))
        appendLE32(UInt32(dataSize))
        data.append(Data(count: dataSize))
        return data
    }

    private static func makeSearch(query: String) -> SearchResponseDTO {
        let ts = timestamp(hour: 10, minute: 24)
        let hits = [
            SearchHitDTO(
                date: "2026-08-21",
                tsMs: ts,
                kind: "chat",
                snippet: "\(query) — travel plans for the autumn trip"
            ),
            SearchHitDTO(
                date: "2026-08-21",
                tsMs: ts + 60_000,
                kind: "transcribe",
                snippet: "A transcript mentioning \(query)"
            ),
            SearchHitDTO(
                date: "2026-08-20",
                tsMs: ts - 86_400_000,
                kind: "video",
                snippet: "A captured frame near \(query)"
            )
        ]
        return SearchResponseDTO(
            schemaVersion: 1,
            query: query,
            total: hits.count,
            nextCursor: nil,
            hits: hits
        )
    }
}


#endif
