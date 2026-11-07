import Combine
import Foundation

enum AudioGalleryState: Equatable {
    case loading
    case empty
    case error(String)
    case data(segments: [AudioSegment], total: Int, nextCursor: String?, loadingMore: Bool)
}

@MainActor
final class AudioGalleryViewModel: ObservableObject {
    @Published private(set) var state: AudioGalleryState = .loading
    @Published private(set) var headerCount = 0
    @Published private(set) var pending: Bool
    @Published private(set) var playerState = MylifeAudioPlayer.State()
    @Published private(set) var scrubMs: Int64?
    @Published private(set) var scrubRef: String?

    let date: String
    private let store: MylifeServicing
    private let player: MylifeAudioPlayer
    private var cancellables = Set<AnyCancellable>()
    private var loadTask: Task<Void, Never>?
    private var loadMoreTask: Task<Void, Never>?
    private var loaded = false
    private var seenRefs = Set<String>()

    init(
        store: MylifeServicing,
        player: MylifeAudioPlayer,
        realtimeSpeaking: AnyPublisher<Bool, Never>,
        date: String,
        count: Int,
        pending: Bool
    ) {
        self.store = store
        self.player = player
        self.date = date
        self.headerCount = count
        self.pending = pending

        player.$state
            .sink { [weak self] state in
                self?.playerState = state
            }
            .store(in: &cancellables)

        realtimeSpeaking
            .removeDuplicates()
            .sink { [weak self] speaking in
                guard let self, speaking, self.player.state.isPlaying else { return }
                self.player.pause()
            }
            .store(in: &cancellables)

        store.overviewPublisher
            .sink { [weak self] overview in
                guard let self else { return }
                if self.loaded, self.player.state.key == nil {
                    self.reloadFirstPage()
                }
                if overview != nil, self.pending, !store.hasPendingAnalysis() {
                    self.pending = false
                }
            }
            .store(in: &cancellables)

        reloadFirstPage()
    }

    var segments: [AudioSegment] {
        if case let .data(segments, _, _, _) = state { return segments }
        return []
    }

    func retry() {
        reloadFirstPage()
    }

    func loadMore() {
        guard case let .data(segments, total, nextCursor, loadingMore) = state,
              let nextCursor,
              !loadingMore else { return }
        state = .data(segments: segments, total: total, nextCursor: nextCursor, loadingMore: true)
        loadMoreTask?.cancel()
        loadMoreTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let page = try await self.store.audioSegments(date: self.date, cursor: nextCursor)
                guard !Task.isCancelled else { return }
                self.applyPage(page, replace: false)
            } catch {
                guard case let .data(existing, total, nextCursor, _) = self.state else { return }
                self.state = .data(segments: existing, total: total, nextCursor: nextCursor, loadingMore: false)
            }
        }
    }

    func onToggle(_ segment: AudioSegment) {
        player.toggle(key: segment.eventRef, url: segment.mediaUrl)
    }

    func onScrub(_ segment: AudioSegment, ms: Int64) {
        scrubMs = ms
        scrubRef = segment.eventRef
    }

    func onScrubEnd(_ segment: AudioSegment, ms: Int64) {
        if player.state.key != segment.eventRef {
            player.toggle(key: segment.eventRef, url: segment.mediaUrl, startAtMs: ms)
        } else {
            player.seek(to: ms)
        }
        scrubMs = nil
        scrubRef = nil
    }

    func pauseForBackground() {
        player.pause()
    }

    func stop() {
        loadTask?.cancel()
        loadMoreTask?.cancel()
        player.stop()
    }

    private func reloadFirstPage() {
        loadTask?.cancel()
        loadMoreTask?.cancel()
        seenRefs.removeAll()
        if case .data = state {
            // Polling refresh keeps the current list visible.
        } else {
            state = .loading
        }
        loadTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let page = try await self.store.audioSegments(date: self.date, cursor: nil)
                guard !Task.isCancelled else { return }
                self.applyPage(page, replace: true)
            } catch {
                guard !isMylifeCancellation(error), !Task.isCancelled else { return }
                if case .data = self.state { return }
                self.state = .error(self.userMessage(error))
            }
        }
    }

    private func applyPage(_ page: AudioPage, replace: Bool) {
        let fresh: [AudioSegment]
        if replace {
            seenRefs.removeAll()
            seenRefs.formUnion(page.segments.map(\.eventRef))
            fresh = page.segments
        } else {
            fresh = page.segments.filter { seenRefs.insert($0.eventRef).inserted }
        }
        let existing: [AudioSegment]
        if replace {
            existing = []
        } else if case let .data(segments, _, _, _) = state {
            existing = segments
        } else {
            existing = []
        }
        let merged = existing + fresh
        loaded = true
        headerCount = page.total
        state = merged.isEmpty
            ? .empty
            : .data(segments: merged, total: page.total, nextCursor: page.nextCursor, loadingMore: false)
    }

    private func userMessage(_ error: Error) -> String {
        if let apiError = error as? MylifeAPIError, !apiError.message.isEmpty {
            return apiError.message
        }
        return L10n.t("mylife_load_failed")
    }
}
