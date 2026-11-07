import Combine
import Foundation
import LivaChatSDK

struct HistorySession: Identifiable, Equatable, Sendable {
    let id: String
    let startTime: Date
    let endTime: Date?
    let messageCount: Int
    let summary: String?
    let firstUserMessage: String?
}

struct HistoryMessageRecord: Identifiable, Equatable, Sendable {
    let id: String
    let sessionID: String
    let role: String
    let text: String
    let isFinal: Bool
    let seq: Int
    let timestamp: Date
    var imagePath: String?
    let imageMime: String?
    let imageWidth: Int?
    let imageHeight: Int?
    let imageOrientation: Int?
    let imageCameraFacing: String?
    let source: String?
    let mode: String?

    static let roleUser = "USER"
    static let roleAI = "AI"
}

struct HistorySeed {
    let sessionID: String
    let messages: [ChatMessage]
}

struct HistoryDetail {
    let session: HistorySession?
    let messages: [ChatMessage]
}

@MainActor
protocol ConversationHistoryRecording: AnyObject {
    var isSuppressingMessages: Bool { get }
    var sessionSwitched: AnyPublisher<HistorySeed, Never> { get }

    func recordUserMessage(
        id: String,
        text: String,
        timestamp: Date,
        mode: ChatMessageMode?
    )
    func recordAssistantMessage(
        id: String,
        text: String,
        timestamp: Date,
        image: AiImage?,
        source: AITextSource?,
        mode: ChatMessageMode?
    )
    func finishSession()
}

enum HistorySummary {
    static func make(from messages: [HistoryMessageRecord]) -> (firstUserMessage: String?, summary: String?) {
        let userMessages = messages.filter { $0.role == HistoryMessageRecord.roleUser }
        let firstUserMessage = userMessages.first?.text
        guard !userMessages.isEmpty else {
            return (firstUserMessage, nil)
        }
        let preview = userMessages.prefix(3).map(\.text).joined(separator: " ")
        let summary = preview.count > 100 ? String(preview.prefix(100)) + "..." : preview
        return (firstUserMessage, summary)
    }
}
