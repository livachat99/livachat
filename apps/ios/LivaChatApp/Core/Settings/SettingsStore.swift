import Combine
import Foundation
import LivaChatSDK

struct SavedServerConfig: Equatable {
    let address: String
    let secret: String
    let wireToken: String
    let deviceID: String
    let serverAddress: ServerAddress

    var webSocketURL: URL { serverAddress.webSocketURL }
}

enum SettingsValidationError: LocalizedError, Equatable {
    case invalidAddress
    case emptySecret
    case missingSecret

    var errorDescription: String? {
        switch self {
        case .invalidAddress:
            return "Enter a valid server address (ip:port or domain:port)."
        case .emptySecret:
            return "Enter the auth key."
        case .missingSecret:
            return "Saved server credentials are unavailable."
        }
    }

    var messageKey: String {
        switch self {
        case .invalidAddress:
            return "settings_error_address"
        case .emptySecret, .missingSecret:
            return "settings_error_secret"
        }
    }
}

@MainActor
final class SettingsStore: ObservableObject {
    private enum Keys {
        static let serverAddress = "settings.server_address"
        static let hapticEnabled = "settings.haptic_enabled"
        static let onboardingCompleted = "settings.onboarding_completed"
        static let tlsAllowSelfSigned = "settings.tls_allow_self_signed"
        static let cameraDefaultFront = "settings.camera_default_front"
        static let cameraAutoOnJoin = "settings.camera_auto_on_join"
        static let avatarVisible = "settings.avatar_visible"
        static let avdetectorEnabled = "settings.avdetector_enabled"
    }

    private let defaults: UserDefaults

    @Published private(set) var savedAddress: String
    @Published var hapticEnabled: Bool {
        didSet { defaults.set(hapticEnabled, forKey: Keys.hapticEnabled) }
    }
    @Published private(set) var onboardingCompleted: Bool
    @Published var tlsAllowSelfSigned: Bool {
        didSet { defaults.set(tlsAllowSelfSigned, forKey: Keys.tlsAllowSelfSigned) }
    }
    @Published var cameraDefaultFront: Bool {
        didSet { defaults.set(cameraDefaultFront, forKey: Keys.cameraDefaultFront) }
    }
    @Published var cameraAutoOnJoin: Bool {
        didSet { defaults.set(cameraAutoOnJoin, forKey: Keys.cameraAutoOnJoin) }
    }
    @Published var avatarVisible: Bool {
        didSet { defaults.set(avatarVisible, forKey: Keys.avatarVisible) }
    }
    /// 允许 AI 主动搭话（AVDetector 会话级开关）；默认开启。
    @Published var avdetectorEnabled: Bool {
        didSet { defaults.set(avdetectorEnabled, forKey: Keys.avdetectorEnabled) }
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.savedAddress = defaults.string(forKey: Keys.serverAddress) ?? ""
        self.hapticEnabled = defaults.object(forKey: Keys.hapticEnabled) as? Bool ?? true
        self.onboardingCompleted = defaults.bool(forKey: Keys.onboardingCompleted)
        self.tlsAllowSelfSigned = defaults.object(forKey: Keys.tlsAllowSelfSigned) as? Bool ?? true
        self.cameraDefaultFront = defaults.object(forKey: Keys.cameraDefaultFront) as? Bool ?? false
        self.cameraAutoOnJoin = defaults.object(forKey: Keys.cameraAutoOnJoin) as? Bool ?? false
        self.avatarVisible = defaults.object(forKey: Keys.avatarVisible) as? Bool ?? true
        self.avdetectorEnabled = defaults.object(forKey: Keys.avdetectorEnabled) as? Bool ?? true
    }

    var tlsPolicy: TLSPolicy {
        tlsAllowSelfSigned ? .allowSelfSigned : .system
    }

    static func validate(address: String, secret: String) throws -> (ServerAddress, String) {
        guard let parsed = ServerAddress(input: address) else {
            throw SettingsValidationError.invalidAddress
        }
        let trimmedSecret = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedSecret.isEmpty else {
            throw SettingsValidationError.emptySecret
        }
        return (parsed, trimmedSecret)
    }

    @discardableResult
    func saveServerConfig(
        address: String,
        secret: String,
        credentials: CredentialStoring
    ) throws -> SavedServerConfig {
        let (parsed, trimmedSecret) = try Self.validate(address: address, secret: secret)
        let wireToken = WireToken.make(secret: trimmedSecret)

        try credentials.write(trimmedSecret, for: .serverSecret)
        try credentials.write(wireToken, for: .wireToken)
        defaults.set(parsed.normalizedInput, forKey: Keys.serverAddress)
        savedAddress = parsed.normalizedInput

        return SavedServerConfig(
            address: parsed.normalizedInput,
            secret: trimmedSecret,
            wireToken: wireToken,
            deviceID: try credentials.deviceID(),
            serverAddress: parsed
        )
    }

    func loadSavedConfig(credentials: CredentialStoring) throws -> SavedServerConfig? {
        let address = savedAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !address.isEmpty, let parsed = ServerAddress(input: address) else {
            return nil
        }
        guard let secret = try credentials.read(.serverSecret), !secret.isEmpty else {
            return nil
        }

        // Derive instead of trusting a cached token: a partial Keychain write
        // (secret updated, wire token stale) must not produce an auth failure.
        let wireToken = WireToken.make(secret: secret)
        if let cached = try? credentials.read(.wireToken) {
            if cached != wireToken {
                try? credentials.write(wireToken, for: .wireToken)
            }
        } else {
            try? credentials.write(wireToken, for: .wireToken)
        }

        return SavedServerConfig(
            address: address,
            secret: secret,
            wireToken: wireToken,
            deviceID: try credentials.deviceID(),
            serverAddress: parsed
        )
    }

    func markOnboardingCompleted() {
        guard !onboardingCompleted else { return }
        onboardingCompleted = true
        defaults.set(true, forKey: Keys.onboardingCompleted)
    }
}
