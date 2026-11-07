import Combine
import Foundation

@MainActor
final class HistoryListViewModel: ObservableObject {
    @Published private(set) var sessions: [HistorySession] = []
    @Published private(set) var isLoading = true
    @Published var searchQuery = ""
    @Published var showDeleteConfirm: String?
    @Published var showClearAllConfirm = false
    @Published private(set) var isClearing = false

    private let service: HistoryService
    private var cancellables = Set<AnyCancellable>()
    private var searchTask: Task<Void, Never>?
    private var queryGeneration = 0

    init(service: HistoryService) {
        self.service = service
        service.$revision
            .dropFirst()
            .sink { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.reloadForRevision()
                }
            }
            .store(in: &cancellables)
    }

    var canClear: Bool {
        !isLoading && (!sessions.isEmpty || !searchQuery.isEmpty)
    }

    func initialLoad() async {
        await reload()
    }

    func searchQueryChanged() {
        queryGeneration += 1
        let generation = queryGeneration
        searchTask?.cancel()
        searchTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled,
                  let self,
                  self.queryGeneration == generation else { return }
            await self.reload(queryGeneration: generation)
        }
    }

    func deleteSession(id: String) {
        showDeleteConfirm = nil
        Task { @MainActor [weak self] in
            guard let self else { return }
            await self.service.deleteSession(id: id)
            self.queryGeneration += 1
            await self.reload(queryGeneration: self.queryGeneration)
        }
    }

    func clearAll() {
        guard !isClearing else { return }
        isClearing = true
        Task { @MainActor [weak self] in
            guard let self else { return }
            await self.service.clearAll()
            self.searchQuery = ""
            self.queryGeneration += 1
            self.showClearAllConfirm = false
            self.isClearing = false
            await self.reload(queryGeneration: self.queryGeneration)
        }
    }

    private func reloadForRevision() {
        queryGeneration += 1
        let generation = queryGeneration
        searchTask?.cancel()
        Task { @MainActor [weak self] in
            await self?.reload(queryGeneration: generation)
        }
    }

    private func reload(queryGeneration: Int? = nil) async {
        let generation = queryGeneration ?? self.queryGeneration
        let query = searchQuery
        let sessions = await service.loadSessions(search: query)
        guard generation == self.queryGeneration else { return }
        self.sessions = sessions
        isLoading = false
    }
}
