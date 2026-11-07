import Combine
import Foundation
import LivaChatSDK

@MainActor
final class SettingsViewModel: ObservableObject {
    @Published var address: String {
        didSet {
            // An auth key without a server address is meaningless and must
            // never remain visible (notably after reinstalling: Keychain
            // survives, UserDefaults does not).
            if address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
               !secret.isEmpty {
                secret = ""
            }
            resetTransientState()
        }
    }
    @Published var secret: String {
        didSet { resetTransientState() }
    }
    @Published var showSecret = false
    @Published private(set) var isProbing = false
    @Published private(set) var probeResult: ProbeResult?
    @Published private(set) var validationMessageKey: String?
    @Published private(set) var saveErrorKey: String?
    @Published var showApplyConfirmation = false
    @Published var tlsAllowSelfSigned: Bool {
        didSet { resetTransientState() }
    }
    @Published var hapticEnabled: Bool {
        didSet {
            store.hapticEnabled = hapticEnabled
            onHapticChanged(hapticEnabled)
        }
    }
    @Published var cameraDefaultFront: Bool {
        didSet { store.cameraDefaultFront = cameraDefaultFront }
    }
    @Published var cameraAutoOnJoin: Bool {
        didSet { store.cameraAutoOnJoin = cameraAutoOnJoin }
    }
    @Published var avdetectorEnabled: Bool {
        didSet {
            store.avdetectorEnabled = avdetectorEnabled
            onAvdetectorChanged(avdetectorEnabled)
        }
    }
    @Published private(set) var savedAddress: String
    @Published private(set) var savedSecret: String
    @Published private(set) var savedTLSAllowSelfSigned: Bool

    private let store: SettingsStore
    private let credentials: CredentialStoring
    private let probe: ServerProbing
    private let probeFactory: ((TLSPolicy) -> ServerProbing)?
    private let onApply: () -> Void
    private let onHapticChanged: (Bool) -> Void
    private let onAvdetectorChanged: (Bool) -> Void
    private var probeTask: Task<Void, Never>?

    init(
        store: SettingsStore,
        credentials: CredentialStoring,
        probe: ServerProbing,
        probeFactory: ((TLSPolicy) -> ServerProbing)? = nil,
        onApply: @escaping () -> Void,
        onHapticChanged: @escaping (Bool) -> Void = { _ in },
        onAvdetectorChanged: @escaping (Bool) -> Void = { _ in }
    ) {
        self.store = store
        self.credentials = credentials
        self.probe = probe
        self.probeFactory = probeFactory
        self.onApply = onApply
        self.onHapticChanged = onHapticChanged
        self.onAvdetectorChanged = onAvdetectorChanged

        let savedAddress = store.savedAddress
        let hasSavedAddress = !savedAddress
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .isEmpty
        let savedSecret: String
        if hasSavedAddress {
            savedSecret = (try? credentials.read(.serverSecret)) ?? ""
        } else {
            // Keychain survives app reinstall while the address (UserDefaults)
            // does not. Drop credentials that cannot belong to an empty
            // address instead of showing a stale key on first launch.
            savedSecret = ""
            try? credentials.delete(.serverSecret)
            try? credentials.delete(.wireToken)
        }
        self.savedAddress = savedAddress
        self.savedSecret = savedSecret
        self.savedTLSAllowSelfSigned = store.tlsAllowSelfSigned
        self.address = savedAddress
        self.secret = savedSecret
        self.tlsAllowSelfSigned = store.tlsAllowSelfSigned
        self.hapticEnabled = store.hapticEnabled
        self.cameraDefaultFront = store.cameraDefaultFront
        self.cameraAutoOnJoin = store.cameraAutoOnJoin
        self.avdetectorEnabled = store.avdetectorEnabled
    }

    var isDirty: Bool {
        address.trimmingCharacters(in: .whitespacesAndNewlines) != savedAddress
            || secret.trimmingCharacters(in: .whitespacesAndNewlines) != savedSecret
            || tlsAllowSelfSigned != savedTLSAllowSelfSigned
    }

    var canSave: Bool {
        isDirty && (try? SettingsStore.validate(address: address, secret: secret)) != nil
    }

    var canProbe: Bool {
        (try? SettingsStore.validate(address: address, secret: secret)) != nil
    }

    var versionText: String {
        AppInfo.displayVersion
    }

    func checkService() {
        guard let (parsed, trimmedSecret) = validated() else { return }

        probeTask?.cancel()
        isProbing = true
        probeResult = nil
        let wireToken = WireToken.make(secret: trimmedSecret)
        let deviceID = (try? credentials.deviceID()) ?? UUID().uuidString
        let policy: TLSPolicy = tlsAllowSelfSigned ? .allowSelfSigned : .system
        let prober = probeFactory?(policy) ?? probe

        probeTask = Task { @MainActor [weak self] in
            let result = await prober.probe(
                address: parsed,
                wireToken: wireToken,
                deviceID: deviceID
            )
            guard !Task.isCancelled, let self else { return }
            self.isProbing = false
            self.probeResult = result
        }
    }

    func requestApply() {
        guard validated() != nil, isDirty else { return }
        showApplyConfirmation = true
    }

    func cancelApply() {
        showApplyConfirmation = false
    }

    @discardableResult
    func apply() -> Bool {
        guard let (parsed, trimmedSecret) = validated() else { return false }

        do {
            let saved = try store.saveServerConfig(
                address: parsed.normalizedInput,
                secret: trimmedSecret,
                credentials: credentials
            )
            store.tlsAllowSelfSigned = tlsAllowSelfSigned
            savedTLSAllowSelfSigned = tlsAllowSelfSigned
            savedAddress = saved.address
            savedSecret = saved.secret
            address = saved.address
            secret = saved.secret
            saveErrorKey = nil
            showApplyConfirmation = false
            onApply()
            return true
        } catch {
            AppLog.error("Settings", "apply failed: \(error.localizedDescription)")
            saveErrorKey = "settings_save_failed"
            showApplyConfirmation = false
            return false
        }
    }

    private func validated() -> (ServerAddress, String)? {
        do {
            let result = try SettingsStore.validate(address: address, secret: secret)
            validationMessageKey = nil
            return result
        } catch let error as SettingsValidationError {
            validationMessageKey = error.messageKey
            return nil
        } catch {
            validationMessageKey = "settings_error_address"
            return nil
        }
    }

    private func resetTransientState() {
        probeTask?.cancel()
        probeTask = nil
        isProbing = false
        probeResult = nil
        validationMessageKey = nil
        saveErrorKey = nil
    }
}
