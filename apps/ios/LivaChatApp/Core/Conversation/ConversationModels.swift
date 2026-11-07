import Foundation
import LivaChatSDK

enum ChatRole: Equatable {
    case user
    case assistant
}

enum ChatMessageMode: String, Equatable, Sendable {
    case translate
    case transcribe
}

struct ChatMessage: Identifiable, Equatable {
    let id: String
    let role: ChatRole
    var text: String
    var isFinal: Bool
    let timestamp: Date
    var image: AiImage? = nil
    var source: AITextSource? = nil
    var mode: ChatMessageMode? = nil

    var isAgentJobResult: Bool {
        id.hasPrefix("ai-msg_agent_job_")
    }
}

struct StreamingAIText: Equatable {
    let messageID: String
    var text: String
}

enum ConversationBannerMessage: Equatable {
    case sessionEnded
    case connectionFailed
    case authFailed
    case connectionClosed
    case micUnavailable
    case audioUnavailable
    case captureFailed(AppErrorMessage)
    case cameraStartFailed(AppErrorMessage)
    case server(titleKey: String, detail: String)
}

enum BannerAction: Equatable {
    case retry
    /// Opens the in-app server configuration screen.
    case openSettings
    /// Opens the iOS Settings app page for LivaChat (permissions).
    case openSystemSettings
}

struct ConversationBanner: Equatable {
    enum Kind: Equatable {
        case warning
        case error
    }

    let kind: Kind
    let message: ConversationBannerMessage
    let action: BannerAction?
    let autoDismissAfter: TimeInterval?

    static let recoverableDuration: TimeInterval = 5

    static func recoverable(_ message: ConversationBannerMessage, action: BannerAction? = nil) -> ConversationBanner {
        ConversationBanner(
            kind: .warning,
            message: message,
            action: action,
            autoDismissAfter: recoverableDuration
        )
    }

    static func fatal(_ message: ConversationBannerMessage, action: BannerAction? = nil) -> ConversationBanner {
        ConversationBanner(
            kind: .error,
            message: message,
            action: action,
            autoDismissAfter: nil
        )
    }
}

enum MicPermissionState: Equatable {
    case unknown
    case undetermined
    case granted
    case denied
}
