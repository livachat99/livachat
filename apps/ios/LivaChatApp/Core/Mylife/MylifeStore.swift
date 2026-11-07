import Combine
import Foundation

@MainActor
protocol MylifeServicing: AnyObject {
    var overviewWindow: OverviewWindow? { get }
    var overviewPublisher: AnyPublisher<OverviewWindow?, Never> { get }

    func refreshOverview(force: Bool, limit: Int, cursor: String?) async throws
    func loadMoreOverview(limit: Int) async throws -> Bool
    func hasPendingAnalysis() -> Bool

    func search(query: String, cursor: String?) async throws -> SearchResponseDTO
    func hotSlotEvents(startMs: Int64, endMs: Int64, cursor: String?) async throws -> HotSlotPage
    func frames(from: String, to: String, cursor: String?) async throws -> FramePage
    func audioSegments(date: String, cursor: String?) async throws -> AudioPage
    func removeEvents(eventRefs: [String]) async throws -> RemoveResponseDTO
    func downloadMedia(url: URL) async throws -> Data

    func markRemovalRefreshPending()
    func flushRemovalRefreshIfNeeded()
    func startPolling()
    func stopPolling()
}

@MainActor
final class MylifeStore: ObservableObject, MylifeServicing {
    @Published private(set) var overviewWindow: OverviewWindow?

    var overviewPublisher: AnyPublisher<OverviewWindow?, Never> {
        $overviewWindow.eraseToAnyPublisher()
    }

    private let api: MylifeAPIClient
    private var pollTask: Task<Void, Never>?
    private var removalRefreshPending = false

    init?(serverBase: ServerBase, token: String) {
        guard let api = MylifeAPIClient(serverBase: serverBase, token: token) else { return nil }
        self.api = api
    }

    init(api: MylifeAPIClient) {
        self.api = api
    }

    // MARK: - Overview window

    func refreshOverview(force: Bool, limit: Int, cursor: String? = nil) async throws {
        try await refreshOverview(force: force, limit: limit, cursor: cursor, allowTimezoneRetry: true)
    }

    private func refreshOverview(
        force: Bool,
        limit: Int,
        cursor: String?,
        allowTimezoneRetry: Bool
    ) async throws {
        if !force && cursor == nil && overviewWindow != nil { return }
        let response = try await api.overview(
            timezone: TimeZone.current.identifier,
            limit: limit,
            cursor: cursor
        )
        if cursor != nil, allowTimezoneRetry,
           let previous = overviewWindow, previous.timezone != response.timezone {
            // A window-locating request must not hide newer days after a tz change.
            return try await refreshOverview(
                force: true,
                limit: limit,
                cursor: nil,
                allowTimezoneRetry: false
            )
        }
        applyOverviewPage(response)
    }

    func loadMoreOverview(limit: Int) async throws -> Bool {
        guard let cursor = overviewWindow?.nextCursor else { return false }
        try await refreshOverview(force: true, limit: limit, cursor: cursor)
        return true
    }

    func hasPendingAnalysis() -> Bool {
        guard let days = overviewWindow?.days else { return false }
        return days.prefix(MylifeFormat.maxPollDays).contains { $0.hasPending }
    }

    private func applyOverviewPage(_ response: OverviewResponseDTO) {
        let page = mapOverview(response)
        guard let previous = overviewWindow, previous.timezone == page.timezone else {
            overviewWindow = page
            return
        }

        var byDate: [String: MylifeDay] = [:]
        for day in page.days { byDate[day.date] = day }
        for day in previous.days where byDate[day.date] == nil { byDate[day.date] = day }
        overviewWindow = OverviewWindow(
            timezone: page.timezone,
            days: byDate.values.sorted { $0.date > $1.date },
            nextCursor: deeperCursor(previous.nextCursor, page.nextCursor),
            total: page.total
        )
    }

    private func deeperCursor(_ lhs: String?, _ rhs: String?) -> String? {
        guard let lhs, let rhs else { return nil }
        return min(lhs, rhs)
    }

    private func mapOverview(_ response: OverviewResponseDTO) -> OverviewWindow {
        let days = response.days.map { day in
            let images = day.images.compactMap { image -> MylifeDayImage? in
                guard let url = api.mediaURL(relativePath: image.mediaUrl) else { return nil }
                return MylifeDayImage(
                    url: url,
                    tsMs: image.tsMs,
                    eventRef: image.eventRef,
                    frameId: image.frameId,
                    windowId: image.windowId
                )
            }
            let topics = day.topics.map { topic in
                MylifeDayTopic(
                    topic: topic.topic,
                    searchWords: nonBlank(topic.searchWords)
                )
            }
            let hotSlots = day.hotSlots.map {
                MylifeHotSlot(startMs: $0.startMs, endMs: $0.endMs)
            }
            return MylifeDay(
                date: day.date,
                counts: day.counts,
                summary: nonBlank(day.summary),
                hotSlots: hotSlots,
                images: images,
                hasPending: day.hasPending,
                topics: topics
            )
        }.sorted { $0.date > $1.date }

        return OverviewWindow(
            timezone: response.timezone,
            days: days,
            nextCursor: response.nextCursor,
            total: response.total
        )
    }

    // MARK: - Search

    func search(query: String, cursor: String? = nil) async throws -> SearchResponseDTO {
        try await api.search(query: query, cursor: cursor)
    }

    // MARK: - Hot slot

    func hotSlotEvents(startMs: Int64, endMs: Int64, cursor: String? = nil) async throws -> HotSlotPage {
        let zone = TimeZone.current
        let from = MylifeDateLabels.localDateKey(tsMs: startMs, timeZone: zone)
        let to = MylifeDateLabels.localDateKey(tsMs: endMs, timeZone: zone)
        let response = try await api.timeline(
            dateFrom: from,
            dateTo: to,
            types: MylifeFormat.hotSlotTypes,
            limit: MylifeFormat.hotSlotPageLimit,
            cursor: cursor,
            timezone: zone.identifier
        )
        let raw = flatten(response)
        let events = raw.compactMap { event -> HotSlotEvent? in
            guard event.tsMs >= startMs, event.tsMs <= endMs else { return nil }
            return mapHotSlotEvent(event)
        }
        return HotSlotPage(
            events: MylifeAggregator.stableSort(events, by: \.tsMs),
            nextCursor: response.nextCursor,
            reachedWindowEnd: raw.contains { $0.tsMs > endMs }
        )
    }

    private func mapHotSlotEvent(_ event: TimelineEventDTO) -> HotSlotEvent? {
        if event.type == EventType.video {
            guard let frameId = nonBlank(event.frameId),
                  let relative = nonBlank(event.mediaUrl),
                  let url = api.mediaURL(relativePath: relative) else { return nil }
            return HotSlotEvent(
                tsMs: event.tsMs,
                type: event.type,
                role: event.role,
                text: nil,
                frameId: frameId,
                mediaUrl: url,
                caption: nonBlank(event.caption),
                eventRef: event.eventRef
            )
        }

        let body: String?
        if event.type == EventType.agentJob {
            body = [event.text, event.summary, event.agentName]
                .compactMap { nonBlank($0) }
                .first
        } else {
            body = nonBlank(event.text)
        }
        guard let body else { return nil }
        return HotSlotEvent(
            tsMs: event.tsMs,
            type: event.type,
            role: event.role,
            text: body,
            frameId: nil,
            mediaUrl: nil,
            caption: nil,
            eventRef: event.eventRef
        )
    }

    // MARK: - Gallery

    func frames(from: String, to: String, cursor: String? = nil) async throws -> FramePage {
        let response = try await api.timeline(
            dateFrom: from,
            dateTo: to,
            types: EventType.video,
            limit: MylifeFormat.galleryPageLimit,
            cursor: cursor,
            timezone: TimeZone.current.identifier
        )
        let frames = flatten(response).compactMap { event -> FrameItem? in
            guard let relative = nonBlank(event.mediaUrl),
                  let frameId = nonBlank(event.frameId),
                  let url = api.mediaURL(relativePath: relative) else { return nil }
            return FrameItem(
                tsMs: event.tsMs,
                mediaUrl: url,
                frameId: frameId,
                windowId: event.windowId,
                caption: nonBlank(event.caption),
                eventRef: nonBlank(event.eventRef)
            )
        }
        return FramePage(
            frames: MylifeAggregator.stableSort(frames, by: \.tsMs),
            total: response.total,
            nextCursor: response.nextCursor
        )
    }

    func removeEvents(eventRefs: [String]) async throws -> RemoveResponseDTO {
        try await api.removeEvents(eventRefs: eventRefs)
    }

    // MARK: - Audio gallery

    func audioSegments(date: String, cursor: String? = nil) async throws -> AudioPage {
        let response = try await api.timeline(
            dateFrom: date,
            dateTo: date,
            types: EventType.audio,
            limit: MylifeFormat.audioPageLimit,
            cursor: cursor,
            timezone: TimeZone.current.identifier
        )
        let segments = flatten(response).compactMap { event -> AudioSegment? in
            guard let relative = nonBlank(event.mediaUrl),
                  let eventRef = nonBlank(event.eventRef),
                  let url = api.mediaURL(relativePath: relative, format: .m4a) else { return nil }
            return AudioSegment(
                eventRef: eventRef,
                tsMs: event.tsMs,
                mediaUrl: url,
                transcript: nonBlank(event.transcript),
                summary: nonBlank(event.audioSummary),
                durationMs: safeDurationMs(event.durationS),
                energy: decodeEnergy(event)
            )
        }
        return AudioPage(
            segments: MylifeAggregator.stableSort(segments, by: \.tsMs),
            total: response.total,
            nextCursor: response.nextCursor
        )
    }

    private func decodeEnergy(_ event: TimelineEventDTO) -> EnergyEnvelope? {
        guard event.energyV == 1,
              let base64 = nonBlank(event.energy),
              let frameMs = event.frameMs,
              let dbMin = event.dbMin,
              let dbMax = event.dbMax,
              dbMin.isFinite, dbMax.isFinite,
              dbMax > dbMin,
              let data = Data(base64Encoded: base64),
              !data.isEmpty else {
            return nil
        }
        let minFloat = Float(dbMin)
        let maxFloat = Float(dbMax)
        guard minFloat.isFinite, maxFloat.isFinite, maxFloat > minFloat else {
            return nil
        }
        return EnergyEnvelope(
            frameMs: frameMs,
            dbMin: minFloat,
            dbMax: maxFloat,
            values: [UInt8](data)
        )
    }

    private func safeDurationMs(_ seconds: Double?) -> Int64 {
        let raw = ((seconds ?? 0) * 1000).rounded()
        guard raw.isFinite else { return 0 }
        return Int64(max(0, min(raw, 9_000_000_000_000_000)))
    }

    // MARK: - Media / removal refresh

    func downloadMedia(url: URL) async throws -> Data {
        try await api.download(url: url)
    }

    func markRemovalRefreshPending() {
        removalRefreshPending = true
    }

    func flushRemovalRefreshIfNeeded() {
        guard removalRefreshPending else { return }
        removalRefreshPending = false
        let limit = pollWindowDays()
        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                try await self.refreshOverview(force: true, limit: limit)
            } catch {
                if Task.isCancelled || isMylifeCancellation(error) { return }
                AppLog.warn("MylifeStore", "overview refresh after removal failed: \(error.localizedDescription)")
            }
        }
    }

    // MARK: - Polling

    func startPolling() {
        guard pollTask == nil else { return }
        pollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if self.hasPendingAnalysis() {
                    try? await Task.sleep(nanoseconds: 60_000_000_000)
                    guard !Task.isCancelled, self.hasPendingAnalysis() else { continue }
                    do {
                        try await self.refreshOverview(force: true, limit: self.pollWindowDays())
                    } catch {
                        if Task.isCancelled || isMylifeCancellation(error) { continue }
                        AppLog.warn("MylifeStore", "overview poll failed: \(error.localizedDescription)")
                    }
                } else {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                }
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    private func pollWindowDays() -> Int {
        let count = overviewWindow?.days.count ?? 0
        return min(max(count, 1), MylifeFormat.maxPollDays)
    }

    // MARK: - Helpers

    private func flatten(_ response: TimelineResponseDTO) -> [TimelineEventDTO] {
        response.days.flatMap(\.events)
    }

    private func nonBlank(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
