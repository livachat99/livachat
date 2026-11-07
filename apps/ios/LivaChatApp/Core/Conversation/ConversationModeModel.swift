import Combine
import Foundation
import LivaChatSDK

enum ConversationLanguageTarget: Equatable, Sendable {
    case dialogue
    case lecture
}

enum TranscribeAudioSource: String, Equatable, Sendable {
    case original
    case voice
}

enum ActiveConversationMode: Equatable, Sendable {
    case dialogue
    case lecture
    case transcribe
    case omni
}

struct ModeChangeRequest: Sendable {
    let mode: SessionMode
    let myLanguage: String?
    let localLanguage: String?
    let usecase: SessionUsecase?

    static let chat = ModeChangeRequest(
        mode: .chat,
        myLanguage: nil,
        localLanguage: nil,
        usecase: nil
    )
}

@MainActor
final class ConversationModeModel: ObservableObject {
    @Published private(set) var translateEnabled = false
    @Published private(set) var lectureEnabled = false
    @Published private(set) var transcribeEnabled = false
    @Published private(set) var omniEnabled = false
    @Published private(set) var isSessionReady = false
    @Published private(set) var transcribeSource: TranscribeAudioSource = .voice
    @Published var isLanguagePickerPresented = false
    @Published var languagePickerTarget: ConversationLanguageTarget?
    @Published var isTranscribeSourcePickerPresented = false
    @Published var isOmniIntroPresented = false

    var sendRequest: (@MainActor (ModeChangeRequest) -> Void)?
    var onEffectiveSourceChange: (@MainActor (TranscribeAudioSource) -> Void)?

    private let deviceLanguage: () -> String
    private var pendingRequest: ModeChangeRequest?
    private var lastRequest: ModeChangeRequest?

    init(deviceLanguage: @escaping () -> String = { Locale.current.languageCode ?? "en" }) {
        self.deviceLanguage = deviceLanguage
    }

    // MARK: - Derived state

    var dialogueActive: Bool { translateEnabled && !lectureEnabled }
    var lectureActive: Bool { translateEnabled && lectureEnabled }
    var anyModeActive: Bool { translateEnabled || transcribeEnabled || omniEnabled }

    var activeMode: ActiveConversationMode? {
        if lectureActive { return .lecture }
        if dialogueActive { return .dialogue }
        if transcribeEnabled { return .transcribe }
        if omniEnabled { return .omni }
        return nil
    }

    var effectiveAudioSource: TranscribeAudioSource {
        transcribeEnabled ? transcribeSource : .voice
    }

    var dialogueSwitchOn: Bool {
        dialogueActive || (isLanguagePickerPresented && languagePickerTarget == .dialogue)
    }

    var lectureSwitchOn: Bool {
        lectureActive || (isLanguagePickerPresented && languagePickerTarget == .lecture)
    }

    var transcribeSwitchOn: Bool {
        transcribeEnabled || isTranscribeSourcePickerPresented
    }

    var omniSwitchOn: Bool {
        omniEnabled || isOmniIntroPresented
    }

    var dialogueSwitchEnabled: Bool { switchEnabled(isActive: dialogueActive) }
    var lectureSwitchEnabled: Bool { switchEnabled(isActive: lectureActive) }
    var transcribeSwitchEnabled: Bool { switchEnabled(isActive: transcribeEnabled) }
    var omniSwitchEnabled: Bool { switchEnabled(isActive: omniEnabled) }

    // MARK: - User actions

    func toggleDialogue() {
        if dialogueActive {
            exitToChat()
        } else {
            presentLanguagePicker(.dialogue)
        }
    }

    func toggleLecture() {
        if lectureActive {
            exitToChat()
        } else {
            presentLanguagePicker(.lecture)
        }
    }

    func toggleTranscribe() {
        if transcribeEnabled {
            exitToChat()
        } else {
            presentTranscribeSourcePicker()
        }
    }

    func toggleOmni() {
        if omniEnabled {
            exitToChat()
        } else {
            presentOmniIntro()
        }
    }

    func presentLanguagePicker(_ target: ConversationLanguageTarget) {
        guard isSessionReady, !anyModeActive else { return }
        languagePickerTarget = target
        isLanguagePickerPresented = true
    }

    func dismissLanguagePicker() {
        isLanguagePickerPresented = false
        languagePickerTarget = nil
    }

    func confirmLanguage(_ language: String) {
        guard isSessionReady, let target = languagePickerTarget else { return }
        dismissLanguagePicker()
        requestTranslate(target: target, language: language)
    }

    func presentTranscribeSourcePicker() {
        guard isSessionReady, !anyModeActive else { return }
        isTranscribeSourcePickerPresented = true
    }

    func dismissTranscribeSourcePicker() {
        isTranscribeSourcePickerPresented = false
    }

    func confirmTranscribeSource(_ source: TranscribeAudioSource) {
        guard isSessionReady else { return }
        dismissTranscribeSourcePicker()
        requestTranscribe(source: source)
    }

    func presentOmniIntro() {
        guard isSessionReady, !anyModeActive else { return }
        isOmniIntroPresented = true
    }

    func dismissOmniIntro() {
        isOmniIntroPresented = false
    }

    func confirmOmni() {
        guard isSessionReady else { return }
        dismissOmniIntro()
        requestOmni()
    }

    func requestTranslate(target: ConversationLanguageTarget, language: String) {
        guard isSessionReady, !anyModeActive else { return }
        let usecase: SessionUsecase = target == .lecture ? .lecture : .dialogue
        let localLanguage: String
        if target == .lecture, language == "auto" {
            localLanguage = "en"
        } else {
            localLanguage = language
        }
        let request = ModeChangeRequest(
            mode: .translate,
            myLanguage: Self.normalizedLanguage(deviceLanguage()),
            localLanguage: localLanguage,
            usecase: usecase
        )
        applyFlags(mode: .translate, usecase: usecase)
        pendingRequest = request
        lastRequest = request
        applyAudioSource()
        AppLog.info(
            "ConversationMode",
            "request translate usecase=\(usecase.rawValue) local=\(localLanguage)"
        )
        sendRequest?(request)
    }

    func requestTranscribe(source: TranscribeAudioSource) {
        guard isSessionReady, !anyModeActive else { return }
        transcribeSource = source
        let request = ModeChangeRequest(
            mode: .transcribe,
            myLanguage: nil,
            localLanguage: nil,
            usecase: nil
        )
        applyFlags(mode: .transcribe, usecase: nil)
        pendingRequest = request
        lastRequest = request
        applyAudioSource()
        AppLog.info("ConversationMode", "request transcribe source=\(source.rawValue)")
        sendRequest?(request)
    }

    func requestOmni() {
        guard isSessionReady, !anyModeActive else { return }
        let request = ModeChangeRequest(
            mode: .omni,
            myLanguage: nil,
            localLanguage: nil,
            usecase: nil
        )
        applyFlags(mode: .omni, usecase: nil)
        pendingRequest = request
        lastRequest = request
        applyAudioSource()
        AppLog.info("ConversationMode", "request omni")
        sendRequest?(request)
    }

    func exitToChat() {
        clearFlags()
        pendingRequest = nil
        lastRequest = nil
        closeOverlays()
        applyAudioSource()
        guard isSessionReady else {
            AppLog.info("ConversationMode", "exit to chat locally (not ready)")
            return
        }
        AppLog.info("ConversationMode", "request chat")
        sendRequest?(.chat)
    }

    // MARK: - Session lifecycle

    func sessionBecameReady() {
        guard !isSessionReady else { return }
        isSessionReady = true
        pendingRequest = nil
        clearFlags()

        guard let lastRequest else {
            applyAudioSource()
            return
        }

        applyFlags(mode: lastRequest.mode, usecase: lastRequest.usecase)
        pendingRequest = lastRequest
        applyAudioSource()
        AppLog.info("ConversationMode", "restore mode=\(lastRequest.mode.rawValue)")
        sendRequest?(lastRequest)
    }

    func sessionBecameNotReady() {
        isSessionReady = false
        pendingRequest = nil
    }

    func sessionEnded() {
        isSessionReady = false
        pendingRequest = nil
        clearFlags()
        closeOverlays()
        applyAudioSource()
    }

    func reset() {
        isSessionReady = false
        pendingRequest = nil
        lastRequest = nil
        clearFlags()
        closeOverlays()
        transcribeSource = .voice
        applyAudioSource()
    }

    func dismissOverlays() {
        closeOverlays()
    }

    // MARK: - Server events

    func applyEcho(mode: SessionMode, usecase: SessionUsecase?) {
        guard isSessionReady else { return }
        pendingRequest = nil
        applyFlags(mode: mode, usecase: usecase)
        applyAudioSource()
        AppLog.info(
            "ConversationMode",
            "echo mode=\(mode.rawValue) usecase=\(usecase?.rawValue ?? "nil")"
        )
    }

    /// Returns true when the error was the session.change rejection (1009).
    @discardableResult
    func handleRejection(code: UInt16) -> Bool {
        guard code == 1009, let request = pendingRequest else { return false }
        pendingRequest = nil
        switch request.mode {
        case .translate:
            translateEnabled = false
            lectureEnabled = false
        case .transcribe:
            transcribeEnabled = false
        case .omni:
            omniEnabled = false
        case .chat:
            break
        }
        lastRequest = nil
        applyAudioSource()
        AppLog.info("ConversationMode", "rejected mode=\(request.mode.rawValue)")
        return true
    }

    func noteTranscribeSourceFailure() {
        transcribeSource = .voice
        guard transcribeEnabled else { return }
        applyAudioSource()
    }

    // MARK: - Private

    private func switchEnabled(isActive: Bool) -> Bool {
        if isActive { return true }
        return isSessionReady && !anyModeActive
    }

    private func clearFlags() {
        translateEnabled = false
        lectureEnabled = false
        transcribeEnabled = false
        omniEnabled = false
    }

    private func closeOverlays() {
        isLanguagePickerPresented = false
        languagePickerTarget = nil
        isTranscribeSourcePickerPresented = false
        isOmniIntroPresented = false
    }

    private func applyFlags(mode: SessionMode, usecase: SessionUsecase?) {
        switch mode {
        case .chat:
            clearFlags()
        case .translate:
            translateEnabled = true
            lectureEnabled = usecase == .lecture
            transcribeEnabled = false
            omniEnabled = false
        case .transcribe:
            translateEnabled = false
            lectureEnabled = false
            transcribeEnabled = true
            omniEnabled = false
        case .omni:
            translateEnabled = false
            lectureEnabled = false
            transcribeEnabled = false
            omniEnabled = true
        }
    }

    private func applyAudioSource() {
        onEffectiveSourceChange?(effectiveAudioSource)
    }

    private static func normalizedLanguage(_ raw: String) -> String {
        let code = raw.split(separator: "-").first.map(String.init) ?? raw
        let normalized = code.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return normalized.isEmpty ? "en" : normalized
    }
}
