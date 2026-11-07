import XCTest
@testable import LivaChatApp

@MainActor
private final class StubPermission: MicPermissionProviding {
    func current() -> MicPermissionState { .granted }
    func request(_ completion: @escaping @MainActor (MicPermissionState) -> Void) {
        completion(.granted)
    }
}

@MainActor
final class ConversationViewModelTests: XCTestCase {
    private func makeViewModel() -> ConversationViewModel {
        ConversationViewModel(
            permissionProvider: StubPermission(),
            bannerAutoDismissInterval: 0.05,
            now: { Date(timeIntervalSince1970: 1_000) }
        )
    }

    func testAppendMessageDeduplicatesAndRejectsBlank() {
        let viewModel = makeViewModel()
        let message = ChatMessage(
            id: "user-1",
            role: .user,
            text: "hello",
            isFinal: true,
            timestamp: Date()
        )
        viewModel.appendMessage(message)
        viewModel.appendMessage(message)
        viewModel.appendMessage(
            ChatMessage(id: "user-2", role: .user, text: "  ", isFinal: true, timestamp: Date())
        )
        XCTAssertEqual(viewModel.messages.count, 1)
    }

    func testClearAudioErrorBannerLeavesUnrelatedBanners() {
        let viewModel = makeViewModel()

        viewModel.showBanner(.fatal(.audioUnavailable, action: .retry))
        viewModel.clearAudioErrorBanner()
        XCTAssertNil(viewModel.banner)

        viewModel.showBanner(
            .fatal(
                .captureFailed(
                    AppErrorMessage("conv_capture_failed", logDetail: "boom")
                ),
                action: .retry
            )
        )
        viewModel.clearAudioErrorBanner()
        XCTAssertNil(viewModel.banner)

        viewModel.showBanner(
            .recoverable(.server(titleKey: "conv_audio_source_failed", detail: "x"))
        )
        viewModel.clearAudioErrorBanner()
        XCTAssertNil(viewModel.banner)

        viewModel.showBanner(.fatal(.authFailed, action: .openSettings))
        viewModel.clearAudioErrorBanner()
        XCTAssertEqual(viewModel.banner?.message, .authFailed)

        viewModel.showBanner(.fatal(.connectionClosed, action: .retry))
        viewModel.clearAudioErrorBanner()
        XCTAssertEqual(viewModel.banner?.message, .connectionClosed)
    }

    func testClearConversationClearsMessagesTransientAndBanner() {
        let viewModel = makeViewModel()
        viewModel.appendUserMessage(id: "1", text: "hello")
        viewModel.setTranscription("partial")
        viewModel.setStreamingAI(messageID: "m1", text: "streaming")
        viewModel.showBanner(.fatal(.connectionClosed, action: .retry))

        viewModel.clearConversation()

        XCTAssertTrue(viewModel.messages.isEmpty)
        XCTAssertEqual(viewModel.currentTranscription, "")
        XCTAssertNil(viewModel.streamingAIText)
        XCTAssertNil(viewModel.banner)
    }

    func testTranscriptionFinalFlagTracksInterimAndFinal() {
        let viewModel = makeViewModel()

        viewModel.setTranscription("partial")
        XCTAssertEqual(viewModel.currentTranscription, "partial")
        XCTAssertFalse(viewModel.isTranscriptionFinal)

        viewModel.finishUserTranscription(id: "user-1", text: "final")
        XCTAssertEqual(viewModel.currentTranscription, "")
        XCTAssertTrue(viewModel.isTranscriptionFinal)
        XCTAssertEqual(viewModel.messages.last?.text, "final")

        viewModel.clearTransient()
        XCTAssertFalse(viewModel.isTranscriptionFinal)
    }

    func testUpdateMicLevelClampsAndRespectsMute() {
        let viewModel = makeViewModel()
        viewModel.updateMicLevel(5)
        XCTAssertEqual(viewModel.micMeter.level, MicLevelModel.levels)
        viewModel.updateMicLevel(-1)
        XCTAssertEqual(viewModel.micMeter.level, 0)

        viewModel.setMicMuted(true)
        viewModel.updateMicLevel(3)
        XCTAssertEqual(viewModel.micMeter.level, 0)

        viewModel.setMicMuted(false)
        viewModel.updateMicLevel(3)
        XCTAssertEqual(viewModel.micMeter.level, 3)
    }

    func testToggleMicUpdatesLocalStateWithoutRuntime() {
        let viewModel = makeViewModel()
        XCTAssertFalse(viewModel.isMicMuted)

        // The runtime attaches asynchronously after the conversation appears.
        // The control must still respond while it is absent.
        viewModel.toggleMic()
        XCTAssertTrue(viewModel.isMicMuted)
        viewModel.toggleMic()
        XCTAssertFalse(viewModel.isMicMuted)
    }

    func testResetMicLevelClearsModel() {
        let viewModel = makeViewModel()
        viewModel.updateMicLevel(3)
        viewModel.resetMicLevel()
        XCTAssertEqual(viewModel.micMeter.level, 0)
    }

    func testConnectionLossClearsTransientButKeepsMessages() {
        let viewModel = makeViewModel()
        viewModel.appendUserMessage(id: "1", text: "hello")
        viewModel.setTranscription("partial")
        viewModel.setStreamingAI(messageID: "m1", text: "streaming")

        viewModel.applyConnectionState(.reconnecting)

        XCTAssertEqual(viewModel.messages.count, 1)
        XCTAssertEqual(viewModel.currentTranscription, "")
        XCTAssertNil(viewModel.streamingAIText)
    }
}
