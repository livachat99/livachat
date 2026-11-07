import Combine
import Foundation
import ImageIO

enum HotSlotState: Equatable {
    case loading
    case empty
    case error(String)
    case data(events: [HotSlotEvent], loadingMore: Bool, exhausted: Bool)
}

@MainActor
final class HotSlotViewModel: ObservableObject {
    @Published private(set) var state: HotSlotState = .loading
    @Published private(set) var pending: Bool
    @Published private(set) var viewerFrame: HotSlotEvent?
    @Published private(set) var viewerAgentJobKey: String?
    @Published private(set) var saving = false
    @Published private(set) var imageSaveTip: String?

    let startMs: Int64
    let endMs: Int64
    let titleTsMs: Int64
    let anchorTs: Int64?

    private let store: MylifeServicing
    private let saver: PhotoLibrarySaving
    private var cancellables = Set<AnyCancellable>()
    private var loadTask: Task<Void, Never>?
    private var loadMoreTask: Task<Void, Never>?
    private var chaseTask: Task<Void, Never>?
    private var saveTask: Task<Void, Never>?
    private var tipTask: Task<Void, Never>?
    private var seenRefs = Set<String>()
    private var nextCursor: String?
    private var exhausted = false
    private var loaded = false
    private var chasing = false
    private var chasePages = 0

    init(
        store: MylifeServicing,
        startMs: Int64,
        endMs: Int64,
        titleTsMs: Int64,
        pending: Bool,
        anchorTs: Int64? = nil,
        saver: PhotoLibrarySaving = PhotoLibrarySaver()
    ) {
        self.store = store
        self.startMs = startMs
        self.endMs = endMs
        self.titleTsMs = titleTsMs
        self.pending = pending
        self.anchorTs = anchorTs
        self.saver = saver

        store.overviewPublisher
            .sink { [weak self] overview in
                guard let self else { return }
                if self.loaded { self.reloadFirstPage() }
                if overview != nil, self.pending, !store.hasPendingAnalysis() {
                    self.pending = false
                }
            }
            .store(in: &cancellables)

        reloadFirstPage()
    }

    var isAnchorMode: Bool { anchorTs != nil }

    func retry() {
        clearOverlays()
        reloadFirstPage()
    }

    func loadMore() {
        guard case let .data(_, loadingMore, exhausted) = state,
              !chasing,
              !loadingMore,
              !exhausted,
              nextCursor != nil else { return }
        setLoadingMore(true)
        loadMoreTask?.cancel()
        loadMoreTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                try await self.fetchPages(firstPage: false)
            } catch is CancellationError {
                // ignore
            } catch {
                // Keep loaded pages; the next scroll gesture may retry.
            }
            self.setLoadingMore(false)
        }
    }

    func ensureLoadedUntil(_ anchorTs: Int64) {
        guard anchorTs > 0,
              case let .data(_, loadingMore, exhausted) = state,
              !chasing,
              !loadingMore,
              !exhausted,
              !coversAnchor(anchorTs),
              nextCursor != nil,
              chasePages < Self.maxChasePages else { return }

        chasing = true
        setLoadingMore(true)
        chaseTask?.cancel()
        chaseTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                while self.chasePages < Self.maxChasePages {
                    self.chasePages += 1
                    try await self.fetchPages(firstPage: false)
                    if self.exhausted || self.coversAnchor(anchorTs) { break }
                }
            } catch is CancellationError {
                // ignore
            } catch {
                // Silent degrade; the screen falls back to first row end >= anchor.
            }
            self.chasing = false
            self.setLoadingMore(false)
        }
    }

    // MARK: - Overlays

    func onFrameClick(_ frame: HotSlotEvent) {
        tipTask?.cancel()
        tipTask = nil
        viewerAgentJobKey = nil
        viewerFrame = frame
        imageSaveTip = nil
    }

    func onAgentJobClick(rowKey: String) {
        saveTask?.cancel()
        saveTask = nil
        tipTask?.cancel()
        tipTask = nil
        viewerFrame = nil
        viewerAgentJobKey = rowKey
        saving = false
        imageSaveTip = nil
    }

    func dismissAgentJob() {
        viewerAgentJobKey = nil
    }

    func dismissViewer() {
        saveTask?.cancel()
        saveTask = nil
        tipTask?.cancel()
        tipTask = nil
        viewerFrame = nil
        saving = false
        imageSaveTip = nil
    }

    func saveViewerFrame() {
        guard !saving, let url = viewerFrame?.mediaUrl else { return }
        saving = true
        saveTask?.cancel()
        saveTask = Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.saving = false }
            do {
                let data = try await self.store.downloadMedia(url: url)
                guard let source = CGImageSourceCreateWithData(data as CFData, nil),
                      CGImageSourceCreateImageAtIndex(source, 0, nil) != nil else {
                    self.showTip(L10n.t("common_save_failed"))
                    return
                }
                let success = await withCheckedContinuation { continuation in
                    self.saver.save(jpeg: data) { result in
                        continuation.resume(returning: result)
                    }
                }
                guard !Task.isCancelled else { return }
                self.showTip(L10n.t(success ? "common_saved_to_gallery" : "common_save_failed"))
            } catch {
                guard !isMylifeCancellation(error), !Task.isCancelled else { return }
                self.showTip(L10n.t("common_save_failed"))
            }
        }
    }

    // MARK: - Loading

    private func reloadFirstPage() {
        loadTask?.cancel()
        loadMoreTask?.cancel()
        chaseTask?.cancel()
        chasing = false
        chasePages = 0
        nextCursor = nil
        exhausted = false
        seenRefs.removeAll()
        if case .data = state {
            // Polling refresh must not flash the list.
        } else {
            state = .loading
        }
        loadTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                try await self.fetchPages(firstPage: true)
            } catch {
                guard !isMylifeCancellation(error), !Task.isCancelled else { return }
                if case .data = self.state { return }
                self.state = .error(self.userMessage(error))
            }
        }
    }

    private func fetchPages(firstPage: Bool) async throws {
        var replace = firstPage
        while true {
            let page = try await store.hotSlotEvents(
                startMs: startMs,
                endMs: endMs,
                cursor: nextCursor
            )
            try Task.checkCancellation()
            let next = accept(page: page, replace: replace)
            replace = false
            loaded = true
            if next == nil || hasEvents() { return }
            if firstPage { state = .loading }
        }
    }

    @discardableResult
    private func accept(page: HotSlotPage, replace: Bool) -> String? {
        let fresh = page.events.filter { event in
            guard let ref = event.eventRef else { return true }
            return seenRefs.insert(ref).inserted
        }
        nextCursor = page.nextCursor
        exhausted = page.reachedWindowEnd || page.nextCursor == nil

        let existing: [HotSlotEvent]
        if replace {
            existing = []
        } else if case let .data(events, _, _) = state {
            existing = events
        } else {
            existing = []
        }
        let merged = existing + fresh
        state = merged.isEmpty
            ? .empty
            : .data(events: merged, loadingMore: false, exhausted: exhausted)
        return exhausted ? nil : nextCursor
    }

    private func hasEvents() -> Bool {
        if case let .data(events, _, _) = state { return !events.isEmpty }
        return false
    }

    private func coversAnchor(_ anchorTs: Int64) -> Bool {
        if case let .data(events, _, _) = state {
            return events.contains { $0.tsMs >= anchorTs }
        }
        return false
    }

    private func setLoadingMore(_ value: Bool) {
        guard case let .data(events, _, exhausted) = state else { return }
        state = .data(events: events, loadingMore: value, exhausted: exhausted)
    }

    private func clearOverlays() {
        saveTask?.cancel()
        saveTask = nil
        tipTask?.cancel()
        tipTask = nil
        viewerFrame = nil
        viewerAgentJobKey = nil
        saving = false
        imageSaveTip = nil
    }

    private func showTip(_ text: String) {
        tipTask?.cancel()
        imageSaveTip = text
        tipTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled else { return }
            self?.imageSaveTip = nil
        }
    }

    private func userMessage(_ error: Error) -> String {
        if let apiError = error as? MylifeAPIError, !apiError.message.isEmpty {
            return apiError.message
        }
        return L10n.t("mylife_load_failed")
    }

    private static let maxChasePages = 5
}
