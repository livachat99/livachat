import Foundation
import ImageIO

enum FrameGalleryState: Equatable {
    case loading
    case empty
    case error(String)
    case data(frames: [FrameItem], total: Int, nextCursor: String?, loadingMore: Bool)
}

@MainActor
final class FrameGalleryViewModel: ObservableObject {
    @Published private(set) var state: FrameGalleryState = .loading
    @Published private(set) var viewerIndex: Int?
    @Published private(set) var imageSaveTip: String?
    @Published private(set) var savingFrameId: String?
    @Published private(set) var editMode = false
    @Published private(set) var selectedFrameIds = Set<String>()
    @Published private(set) var deleteConfirm = false
    @Published private(set) var deleting = false
    @Published private(set) var deleteTip: String?

    private let store: MylifeServicing
    private let saver: PhotoLibrarySaving
    private(set) var from: String = ""
    private(set) var to: String = ""
    private var loadTask: Task<Void, Never>?
    private var loadMoreTask: Task<Void, Never>?
    private var saveTask: Task<Void, Never>?
    private var imageTipTask: Task<Void, Never>?
    private var deleteTipTask: Task<Void, Never>?

    private static let maxSelection = 50
    private static let reasonNotFound = "not_found"

    init(store: MylifeServicing, saver: PhotoLibrarySaving = PhotoLibrarySaver()) {
        self.store = store
        self.saver = saver
    }

    var frames: [FrameItem] {
        if case let .data(frames, _, _, _) = state { return frames }
        return []
    }

    var total: Int {
        if case let .data(_, total, _, _) = state { return total }
        return 0
    }

    func load(from: String, to: String, force: Bool = false) {
        guard !from.isEmpty, !to.isEmpty else { return }
        if !force, from == self.from, to == self.to, case .data = state { return }
        self.from = from
        self.to = to
        loadTask?.cancel()
        loadMoreTask?.cancel()
        state = .loading
        loadTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let page = try await self.store.frames(from: from, to: to, cursor: nil)
                guard !Task.isCancelled else { return }
                self.state = page.frames.isEmpty
                    ? .empty
                    : .data(frames: page.frames, total: page.total, nextCursor: page.nextCursor, loadingMore: false)
            } catch {
                guard !isMylifeCancellation(error), !Task.isCancelled else { return }
                self.state = .error(self.userMessage(error))
            }
        }
    }

    func loadMore() {
        guard case let .data(frames, total, nextCursor, loadingMore) = state,
              let nextCursor,
              !loadingMore else { return }
        state = .data(frames: frames, total: total, nextCursor: nextCursor, loadingMore: true)
        loadMoreTask?.cancel()
        loadMoreTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let page = try await self.store.frames(from: self.from, to: self.to, cursor: nextCursor)
                guard !Task.isCancelled else { return }
                guard case let .data(existing, _, _, _) = self.state else { return }
                var seen = Set(existing.map(\.frameId))
                let appended = page.frames.filter { seen.insert($0.frameId).inserted }
                self.state = .data(
                    frames: existing + appended,
                    total: page.total,
                    nextCursor: page.nextCursor,
                    loadingMore: false
                )
            } catch is CancellationError {
                // ignore
            } catch {
                guard case let .data(existing, total, nextCursor, _) = self.state else { return }
                self.state = .data(
                    frames: existing,
                    total: total,
                    nextCursor: nextCursor,
                    loadingMore: false
                )
            }
        }
    }

    func onFrameClick(_ index: Int) {
        guard !deleting, frames.indices.contains(index) else { return }
        imageTipTask?.cancel()
        imageTipTask = nil
        viewerIndex = index
        imageSaveTip = nil
    }

    func dismissViewer() {
        saveTask?.cancel()
        saveTask = nil
        imageTipTask?.cancel()
        imageTipTask = nil
        viewerIndex = nil
        savingFrameId = nil
        imageSaveTip = nil
    }

    func saveFrame(id: String) {
        guard savingFrameId == nil,
              let frame = frames.first(where: { $0.frameId == id }) else { return }
        savingFrameId = frame.frameId
        saveTask?.cancel()
        saveTask = Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.savingFrameId = nil }
            do {
                let data = try await self.store.downloadMedia(url: frame.mediaUrl)
                guard let source = CGImageSourceCreateWithData(data as CFData, nil),
                      CGImageSourceCreateImageAtIndex(source, 0, nil) != nil else {
                    self.showImageTip(L10n.t("common_save_failed"))
                    return
                }
                let success = await withCheckedContinuation { continuation in
                    self.saver.save(jpeg: data) { result in
                        continuation.resume(returning: result)
                    }
                }
                guard !Task.isCancelled else { return }
                self.showImageTip(L10n.t(success ? "common_saved_to_gallery" : "common_save_failed"))
            } catch {
                guard !isMylifeCancellation(error), !Task.isCancelled else { return }
                self.showImageTip(L10n.t("common_save_failed"))
            }
        }
    }

    // MARK: - Edit / delete

    func enterEditMode() {
        guard !deleting else { return }
        editMode = true
        selectedFrameIds.removeAll()
    }

    func exitEditMode() {
        guard !deleting else { return }
        editMode = false
        selectedFrameIds.removeAll()
        deleteConfirm = false
    }

    func toggleSelect(_ frame: FrameItem) {
        guard editMode, !deleting, let ref = frame.eventRef, !ref.isEmpty else { return }
        if selectedFrameIds.contains(frame.frameId) {
            selectedFrameIds.remove(frame.frameId)
        } else if selectedFrameIds.count >= Self.maxSelection {
            showDeleteTip(L10n.t("mylife_max_selection"))
        } else {
            selectedFrameIds.insert(frame.frameId)
        }
    }

    func requestDelete() {
        guard editMode, !deleting, !selectedFrameIds.isEmpty else { return }
        deleteConfirm = true
    }

    func dismissDeleteConfirm() {
        deleteConfirm = false
    }

    func deleteSelected() {
        deleteConfirm = false
        guard editMode, !deleting, !selectedFrameIds.isEmpty else { return }
        let selected = frames.filter { selectedFrameIds.contains($0.frameId) }
        let refs = selected.compactMap { $0.eventRef.flatMap { $0.isEmpty ? nil : $0 } }
        guard !refs.isEmpty else { return }
        deleting = true
        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let response = try await self.store.removeEvents(eventRefs: refs)
                let gone = Set(response.removed)
                    .union(response.skipped.filter { $0.reason == Self.reasonNotFound }.map(\.eventRef))
                let failed = response.skipped.filter { $0.reason != Self.reasonNotFound }.count
                let tip: String
                if failed == 0 {
                    tip = L10n.plural("mylife_photos_deleted", gone.count)
                } else {
                    tip = L10n.format("mylife_photos_deleted_partial", gone.count, failed)
                }
                self.showDeleteTip(tip)
                self.editMode = false
                self.selectedFrameIds.removeAll()
                self.store.markRemovalRefreshPending()
                self.load(from: self.from, to: self.to, force: true)
            } catch is CancellationError {
                // ignore
            } catch {
                self.showDeleteTip(
                    self.isTimeout(error)
                        ? L10n.t("mylife_delete_timeout")
                        : L10n.t("mylife_delete_failed")
                )
            }
            self.deleting = false
        }
    }

    func flushOnExit() {
        store.flushRemovalRefreshIfNeeded()
    }

    private func isTimeout(_ error: Error) -> Bool {
        if let urlError = error as? URLError, urlError.code == .timedOut { return true }
        if let apiError = error as? MylifeAPIError, apiError.statusCode == 408 { return true }
        return false
    }

    private func showImageTip(_ text: String) {
        imageTipTask?.cancel()
        imageSaveTip = text
        imageTipTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled else { return }
            self?.imageSaveTip = nil
        }
    }

    private func showDeleteTip(_ text: String) {
        deleteTipTask?.cancel()
        deleteTip = text
        deleteTipTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled else { return }
            self?.deleteTip = nil
        }
    }

    private func userMessage(_ error: Error) -> String {
        if let apiError = error as? MylifeAPIError, !apiError.message.isEmpty {
            return apiError.message
        }
        return L10n.t("mylife_load_failed")
    }
}
