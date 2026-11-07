import Combine
import Foundation

enum TimelineSearchState: Equatable {
    case idle
    case loading
    case empty
    case error(String)
    case data(hits: [SearchHitDTO], total: Int, nextCursor: String?, loadingMore: Bool)
}

struct TimelineEventListNav: Equatable {
    let tsMs: Int64
    let pending: Bool
}

struct TimelineGalleryNav: Equatable {
    let from: String
    let to: String
    let label: String
    let focusFrameId: String?
    let pending: Bool
}

struct TimelineHotSlotNav: Equatable {
    let startMs: Int64
    let endMs: Int64
    let pending: Bool
}

struct TimelineAudioNav: Equatable {
    let date: String
    let label: String
    let count: Int
    let pending: Bool
}

@MainActor
final class TimelineHomeViewModel: ObservableObject {
    @Published private(set) var granularity: Granularity = .day
    @Published private(set) var scopeKey: String?
    @Published private(set) var overviewWindow: OverviewWindow?
    @Published private(set) var overviewLoading = true
    @Published private(set) var overviewError: String?
    @Published private(set) var periods: [PeriodDigest] = []
    @Published private(set) var loadingMore = false
    @Published private(set) var loadMoreError: String?
    @Published var query = ""
    @Published private(set) var search: TimelineSearchState = .idle
    @Published private(set) var isRefreshing = false
    @Published private(set) var scrollResetToken = 0

    private let store: MylifeServicing
    private var locale: Locale { AppLocale.current }
    private var cancellables = Set<AnyCancellable>()
    private var searchTask: Task<Void, Never>?
    private var browseTask: Task<Void, Never>?
    private var loadMoreTask: Task<Void, Never>?
    private var searchGeneration = 0
    private var previousTimezone: String?

    init(store: MylifeServicing) {
        self.store = store
        store.overviewPublisher
            .sink { [weak self] window in
                self?.applyWindow(window)
            }
            .store(in: &cancellables)
        loadFirstPage()
    }

    var hasMore: Bool {
        overviewWindow?.nextCursor != nil
    }

    var scopeLabel: String? {
        scopeKey.map { MylifeDateLabels.scopeLabel($0, locale: locale) }
    }

    var isSearchMode: Bool {
        !query.isEmpty
    }

    // MARK: - Browse

    func onGranularityChange(_ granularity: Granularity) {
        self.granularity = granularity
        scopeKey = nil
        scrollResetToken += 1
        recompute()
        ensureWindow(for: granularity)
    }

    func onDrillDown(_ digest: PeriodDigest) {
        switch digest.granularity {
        case .month:
            granularity = .day
            scopeKey = digest.key
        case .year:
            granularity = .month
            scopeKey = digest.key
        case .day, .week:
            return
        }
        scrollResetToken += 1
        recompute()
    }

    func onClearScope() {
        scopeKey = nil
        recompute()
    }

    func onLoadMore(manual: Bool = false) {
        guard scopeKey == nil,
              hasMore,
              !loadingMore,
              manual || loadMoreError == nil else { return }
        loadingMore = true
        loadMoreError = nil
        let limit = MylifeFormat.overviewPageSize(for: granularity)
        loadMoreTask?.cancel()
        loadMoreTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                _ = try await self.store.loadMoreOverview(limit: limit)
            } catch {
                if !isMylifeCancellation(error), !Task.isCancelled {
                    self.loadMoreError = self.userMessage(error, fallbackKey: "mylife_load_failed")
                }
            }
            self.loadingMore = false
        }
    }

    func refresh() {
        if trimmedQuery.count >= 2 {
            onSearchImmediate()
            return
        }
        isRefreshing = true
        browseTask?.cancel()
        browseTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                if let scopeKey = self.scopeKey,
                   let range = self.scopeDateRange(scopeKey) {
                    let limit = MylifeDateLabels.daysBetweenInclusive(from: range.from, to: range.to) ?? 30
                    let cursor = MylifeDateLabels.dayAfter(range.to)
                    try await self.store.refreshOverview(force: true, limit: limit, cursor: cursor)
                } else {
                    try await self.store.refreshOverview(
                        force: true,
                        limit: MylifeFormat.overviewPageSize(for: self.granularity),
                        cursor: nil
                    )
                }
                self.overviewError = nil
            } catch {
                if !isMylifeCancellation(error), !Task.isCancelled, self.overviewWindow == nil {
                    self.overviewError = self.userMessage(error, fallbackKey: "mylife_load_failed")
                }
            }
            self.isRefreshing = false
        }
    }

    private func loadFirstPage() {
        overviewLoading = true
        browseTask?.cancel()
        browseTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                try await self.store.refreshOverview(
                    force: true,
                    limit: MylifeFormat.overviewPageSize(for: self.granularity),
                    cursor: nil
                )
            } catch {
                if !isMylifeCancellation(error), !Task.isCancelled, self.overviewWindow == nil {
                    self.overviewError = self.userMessage(error, fallbackKey: "mylife_load_failed")
                }
            }
            self.overviewLoading = false
        }
    }

    private func ensureWindow(for granularity: Granularity) {
        guard let window = overviewWindow, window.nextCursor != nil else { return }
        let need = MylifeFormat.overviewPageSize(for: granularity)
        guard window.days.count < need else { return }
        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                try await self.store.refreshOverview(force: true, limit: need, cursor: nil)
            } catch {
                // Keep existing content; the next page/refresh can retry.
            }
        }
    }

    private func applyWindow(_ window: OverviewWindow?) {
        guard let window else {
            overviewWindow = nil
            overviewLoading = false
            recompute()
            return
        }
        let tzChanged = previousTimezone != nil && previousTimezone != window.timezone
        previousTimezone = window.timezone
        overviewWindow = window
        overviewLoading = false
        overviewError = nil
        loadMoreError = nil
        if tzChanged { scopeKey = nil }
        recompute()
    }

    private func recompute() {
        guard let window = overviewWindow else {
            periods = []
            return
        }
        let digests = MylifeAggregator.aggregate(
            days: window.days,
            granularity: granularity,
            scopeKey: scopeKey,
            locale: locale
        )
        guard let oldest = window.days.map(\.date).min(), window.nextCursor != nil else {
            periods = digests
            return
        }
        periods = digests.filter { digest in
            guard let start = digest.dateRange()?.from else { return true }
            return oldest <= start
        }
    }

    private func scopeDateRange(_ scopeKey: String) -> (from: String, to: String)? {
        if scopeKey.count == 7 {
            guard let components = MylifeDateLabels.components(fromDateKey: scopeKey + "-01"),
                  let year = components.year, let month = components.month else { return nil }
            return (
                PosixFormat.string("%04d-%02d-01", year, month),
                PosixFormat.string("%04d-%02d-%02d", year, month, MylifeDateLabels.daysInMonth(year: year, month: month))
            )
        }
        if scopeKey.count == 4, let year = Int(scopeKey) {
            let yearKey = PosixFormat.string("%04d", year)
            return ("\(yearKey)-01-01", "\(yearKey)-12-31")
        }
        return nil
    }

    // MARK: - Search

    func onQueryChange(_ value: String) {
        query = value
        scheduleSearch(debounced: true)
    }

    func onSearchImmediate() {
        scheduleSearch(debounced: false)
    }

    func onSearchRetry() {
        scheduleSearch(debounced: false)
    }

    func clearQuery() {
        searchTask?.cancel()
        searchGeneration += 1
        query = ""
        search = .idle
    }

    private var trimmedQuery: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func scheduleSearch(debounced: Bool) {
        searchTask?.cancel()
        let q = trimmedQuery
        searchGeneration += 1
        let generation = searchGeneration

        guard !q.isEmpty else {
            search = .idle
            return
        }
        guard q.count >= 2 else {
            search = .idle
            return
        }

        search = .loading
        searchTask = Task { @MainActor [weak self] in
            if debounced {
                try? await Task.sleep(nanoseconds: 350_000_000)
            }
            guard let self, !Task.isCancelled, self.searchGeneration == generation else { return }
            await self.performSearch(q, cursor: nil, generation: generation)
        }
    }

    private func performSearch(_ q: String, cursor: String?, generation: Int) async {
        let previousSearch = search
        do {
            let response = try await store.search(query: q, cursor: cursor)
            guard !Task.isCancelled, searchGeneration == generation else { return }
            if cursor != nil, case let .data(existing, _, _, _) = search {
                let existingIDs = Set(existing.map(\.id))
                let appended = existing + response.hits.filter { !existingIDs.contains($0.id) }
                search = .data(
                    hits: appended,
                    total: response.total,
                    nextCursor: response.nextCursor,
                    loadingMore: false
                )
            } else if response.hits.isEmpty {
                search = .empty
            } else {
                search = .data(
                    hits: response.hits,
                    total: response.total,
                    nextCursor: response.nextCursor,
                    loadingMore: false
                )
            }
        } catch {
            // New query/close cancels the request; its state is owned by the newer flow.
            guard !isMylifeCancellation(error), !Task.isCancelled,
                  searchGeneration == generation else { return }
            if cursor != nil, case let .data(hits, total, nextCursor, _) = previousSearch {
                // Pagination failure keeps loaded results (Android parity).
                search = .data(hits: hits, total: total, nextCursor: nextCursor, loadingMore: false)
            } else {
                search = .error(userMessage(error, fallbackKey: "mylife_search_failed"))
            }
        }
    }

    func onSearchLoadMore() {
        guard case let .data(hits, total, nextCursor, loadingMore) = search,
              let nextCursor,
              !loadingMore,
              trimmedQuery.count >= 2 else { return }
        search = .data(hits: hits, total: total, nextCursor: nextCursor, loadingMore: true)
        let q = trimmedQuery
        let generation = searchGeneration
        Task { @MainActor [weak self] in
            guard let self else { return }
            await self.performSearch(q, cursor: nextCursor, generation: generation)
        }
    }

    func onSearchHitClick(_ hit: SearchHitDTO) -> TimelineEventListNav? {
        guard let tsMs = hit.tsMs else { return nil }
        let pending = pendingForDay(tsMs)
        return TimelineEventListNav(tsMs: tsMs, pending: pending)
    }

    func onTopicSearch(_ topic: MylifeDayTopic) {
        let trimmed = topic.searchWords?.trimmingCharacters(in: .whitespacesAndNewlines)
        query = trimmed.flatMap { $0.isEmpty ? nil : $0 } ?? topic.topic
        scheduleSearch(debounced: true)
    }

    private func pendingForDay(_ tsMs: Int64) -> Bool {
        let date = MylifeDateLabels.localDateKey(tsMs: tsMs)
        return overviewWindow?.days.first(where: { $0.date == date })?.hasPending ?? false
    }

    // MARK: - Navigation payloads

    func hotSlotNavigation(_ slot: MylifeHotSlot, pending: Bool) -> TimelineHotSlotNav {
        TimelineHotSlotNav(startMs: slot.startMs, endMs: slot.endMs, pending: pending)
    }

    func imageNavigation(_ image: PeriodImage, digest: PeriodDigest) -> TimelineGalleryNav? {
        guard let range = digest.dateRange() else { return nil }
        return TimelineGalleryNav(
            from: range.from,
            to: range.to,
            label: digest.rangeLabel,
            focusFrameId: image.frameId,
            pending: digest.hasPending
        )
    }

    func audioNavigation(_ digest: PeriodDigest) -> TimelineAudioNav? {
        guard digest.audioCount > 0 else { return nil }
        return TimelineAudioNav(
            date: digest.key,
            label: digest.rangeLabel,
            count: digest.audioCount,
            pending: digest.hasPending
        )
    }

    private func userMessage(_ error: Error, fallbackKey: String) -> String {
        if let apiError = error as? MylifeAPIError, !apiError.message.isEmpty {
            return apiError.message
        }
        return L10n.t(fallbackKey)
    }
}
