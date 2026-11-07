import Foundation

@MainActor
final class HistoryDetailViewModel: ObservableObject {
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var isLoading = true
    @Published var imageViewerMessageID: String?
    @Published var agentJobResultMessageID: String?

    let sessionID: String
    private let service: HistoryService
    private var loaded = false

    init(service: HistoryService, sessionID: String) {
        self.service = service
        self.sessionID = sessionID
    }

    var imageMessages: [ChatMessage] {
        messages.filter { $0.role == .assistant && $0.image != nil }
    }

    var agentJobResultMessage: ChatMessage? {
        guard let id = agentJobResultMessageID else { return nil }
        return messages.first { $0.id == id && $0.isAgentJobResult }
    }

    func loadIfNeeded() async {
        guard !loaded else { return }
        loaded = true
        let detail = await service.loadDetail(sessionID: sessionID)
        messages = detail.messages
        isLoading = false
    }

    func openImageViewer(messageID: String) {
        guard imageMessages.contains(where: { $0.id == messageID }) else { return }
        imageViewerMessageID = messageID
    }

    func closeImageViewer() {
        imageViewerMessageID = nil
    }

    func openAgentJobResult(messageID: String) {
        guard messages.first(where: { $0.id == messageID })?.isAgentJobResult == true else { return }
        agentJobResultMessageID = messageID
    }

    func closeAgentJobResult() {
        agentJobResultMessageID = nil
    }

    func deleteSession() async {
        await service.deleteSession(id: sessionID)
    }
}
