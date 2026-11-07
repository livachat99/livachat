import LivaChatSDK
import XCTest
@testable import LivaChatApp

@MainActor
private final class ModeRecorder {
    var requests: [ModeChangeRequest] = []
    var sources: [TranscribeAudioSource] = []
}

@MainActor
final class ConversationModeModelTests: XCTestCase {
    private func makeModel() -> (ConversationModeModel, ModeRecorder) {
        let model = ConversationModeModel(deviceLanguage: { "zh-Hans" })
        let recorder = ModeRecorder()
        model.sendRequest = { request in
            recorder.requests.append(request)
        }
        model.onEffectiveSourceChange = { source in
            recorder.sources.append(source)
        }
        return (model, recorder)
    }

    private func makeReady(_ model: ConversationModeModel, _ recorder: ModeRecorder) {
        model.sessionBecameReady()
        recorder.requests.removeAll()
        recorder.sources.removeAll()
    }

    func testDialogueAutoIsSentRaw() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .dialogue, language: "auto")

        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertEqual(recorder.requests[0].mode.rawValue, SessionMode.translate.rawValue)
        XCTAssertEqual(recorder.requests[0].myLanguage, "zh")
        XCTAssertEqual(recorder.requests[0].localLanguage, "auto")
        XCTAssertEqual(recorder.requests[0].usecase?.rawValue, SessionUsecase.dialogue.rawValue)
        XCTAssertTrue(model.dialogueActive)
        XCTAssertFalse(model.lectureEnabled)
        XCTAssertEqual(recorder.sources.last, .voice)
    }

    func testLectureAutoNormalizedToEnglish() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .lecture, language: "auto")

        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertEqual(recorder.requests[0].localLanguage, "en")
        XCTAssertEqual(recorder.requests[0].usecase?.rawValue, SessionUsecase.lecture.rawValue)
        XCTAssertTrue(model.translateEnabled)
        XCTAssertTrue(model.lectureEnabled)
        XCTAssertEqual(model.activeMode, .lecture)
    }

    func testTranscribeSetsSourceAndLocalPayload() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranscribe(source: .original)

        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertEqual(recorder.requests[0].mode.rawValue, SessionMode.transcribe.rawValue)
        XCTAssertNil(recorder.requests[0].myLanguage)
        XCTAssertNil(recorder.requests[0].localLanguage)
        XCTAssertNil(recorder.requests[0].usecase)
        XCTAssertTrue(model.transcribeEnabled)
        XCTAssertEqual(model.transcribeSource, .original)
        XCTAssertEqual(model.activeMode, .transcribe)
        XCTAssertEqual(recorder.sources.last, .original)
    }

    func testOmniUsesVoiceSource() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestOmni()

        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertEqual(recorder.requests[0].mode.rawValue, SessionMode.omni.rawValue)
        XCTAssertTrue(model.omniEnabled)
        XCTAssertEqual(recorder.sources.last, .voice)
    }

    func testRequestsBlockedUntilReady() {
        let (model, recorder) = makeModel()
        model.requestTranslate(target: .dialogue, language: "en")
        model.requestTranscribe(source: .voice)
        model.requestOmni()

        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertFalse(model.anyModeActive)
    }

    func testSwitchEnablement() {
        let (model, recorder) = makeModel()
        XCTAssertFalse(model.dialogueSwitchEnabled)
        XCTAssertFalse(model.transcribeSwitchEnabled)

        makeReady(model, recorder)
        XCTAssertTrue(model.dialogueSwitchEnabled)
        XCTAssertTrue(model.lectureSwitchEnabled)
        XCTAssertTrue(model.transcribeSwitchEnabled)
        XCTAssertTrue(model.omniSwitchEnabled)

        model.requestTranslate(target: .lecture, language: "en")
        XCTAssertTrue(model.lectureSwitchEnabled)
        XCTAssertFalse(model.dialogueSwitchEnabled)
        XCTAssertFalse(model.transcribeSwitchEnabled)
        XCTAssertFalse(model.omniSwitchEnabled)
        XCTAssertTrue(model.lectureSwitchOn)
        XCTAssertFalse(model.dialogueSwitchOn)
    }

    func testPickerOnStateAndConfirmation() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.presentLanguagePicker(.dialogue)
        XCTAssertTrue(model.isLanguagePickerPresented)
        XCTAssertTrue(model.dialogueSwitchOn)
        XCTAssertFalse(model.dialogueActive)

        model.confirmLanguage("ja")
        XCTAssertFalse(model.isLanguagePickerPresented)
        XCTAssertTrue(model.dialogueActive)
        XCTAssertEqual(recorder.requests.last?.localLanguage, "ja")
    }

    func testLocalExitSendsChatAndClearsRestore() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .dialogue, language: "en")
        recorder.requests.removeAll()

        model.exitToChat()
        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertEqual(recorder.requests[0].mode.rawValue, SessionMode.chat.rawValue)
        XCTAssertFalse(model.anyModeActive)
        XCTAssertEqual(recorder.sources.last, .voice)

        recorder.requests.removeAll()
        model.sessionBecameNotReady()
        model.sessionBecameReady()
        XCTAssertTrue(recorder.requests.isEmpty)
    }

    func testLocalExitWithoutReadyDoesNotSendAndClearsRestore() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .dialogue, language: "en")
        model.sessionBecameNotReady()
        recorder.requests.removeAll()

        model.exitToChat()
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertFalse(model.anyModeActive)

        model.sessionBecameReady()
        XCTAssertTrue(recorder.requests.isEmpty)
    }

    func testEchoAppliesFlagsAndSource() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.applyEcho(mode: .transcribe, usecase: nil)

        XCTAssertTrue(model.transcribeEnabled)
        XCTAssertFalse(model.translateEnabled)
        XCTAssertEqual(recorder.sources.last, .voice)

        model.applyEcho(mode: .translate, usecase: .lecture)
        XCTAssertTrue(model.translateEnabled)
        XCTAssertTrue(model.lectureEnabled)
        XCTAssertEqual(recorder.sources.last, .voice)
    }

    func testEchoIgnoredBeforeReady() {
        let (model, _) = makeModel()
        model.applyEcho(mode: .translate, usecase: .lecture)
        XCTAssertFalse(model.anyModeActive)
    }

    func testRejection1009RollsBackAndClearsRestore() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .dialogue, language: "en")
        XCTAssertTrue(model.handleRejection(code: 1009))
        XCTAssertFalse(model.anyModeActive)
        XCTAssertEqual(recorder.sources.last, .voice)

        recorder.requests.removeAll()
        model.sessionBecameNotReady()
        model.sessionBecameReady()
        XCTAssertTrue(recorder.requests.isEmpty)
    }

    func testOtherErrorsDoNotRollback() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .dialogue, language: "en")
        XCTAssertFalse(model.handleRejection(code: 1001))
        XCTAssertFalse(model.handleRejection(code: 1008))
        XCTAssertTrue(model.dialogueActive)
    }

    func testNotReadyDropsPendingButKeepsRestoreRequest() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .dialogue, language: "en")
        model.sessionBecameNotReady()

        XCTAssertFalse(model.handleRejection(code: 1009))
        XCTAssertTrue(model.translateEnabled)

        recorder.requests.removeAll()
        model.sessionBecameReady()
        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertEqual(recorder.requests[0].mode.rawValue, SessionMode.translate.rawValue)
    }

    func testSessionEndedClearsUIKeepsRestore() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .lecture, language: "en")
        recorder.requests.removeAll()

        model.sessionEnded()
        XCTAssertFalse(model.anyModeActive)
        XCTAssertFalse(model.isSessionReady)
        XCTAssertEqual(recorder.sources.last, .voice)

        model.sessionBecameReady()
        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertEqual(recorder.requests[0].usecase?.rawValue, SessionUsecase.lecture.rawValue)
        XCTAssertTrue(model.lectureActive)
    }

    func testSourceFailureFallsBackToVoice() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranscribe(source: .original)
        XCTAssertEqual(model.transcribeSource, .original)

        model.noteTranscribeSourceFailure()
        XCTAssertEqual(model.transcribeSource, .voice)
        XCTAssertEqual(recorder.sources.last, .voice)
        XCTAssertTrue(model.transcribeEnabled)
    }

    func testSourceSequenceOnExit() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranscribe(source: .original)
        model.applyEcho(mode: .transcribe, usecase: nil)
        model.exitToChat()

        XCTAssertEqual(recorder.sources, [.original, .original, .voice])
    }

    func testConfirmWhileNotReadyKeepsDialog() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.presentOmniIntro()
        model.sessionBecameNotReady()

        model.confirmOmni()
        XCTAssertTrue(model.isOmniIntroPresented)
        XCTAssertTrue(recorder.requests.isEmpty)
    }

    func testLanguagePickerIncludesThaiAndVietnamese() {
        XCTAssertEqual(
            LanguagePickerDialog.languageCodes,
            [
                "auto", "zh", "en", "ja", "ko", "fr", "it", "de", "es", "pt", "ru",
                "hi", "id", "th", "vi", "ar",
            ]
        )
        XCTAssertTrue(LanguagePickerDialog.languageCodes.contains("hi"))
        XCTAssertTrue(LanguagePickerDialog.languageCodes.contains("id"))
        XCTAssertTrue(LanguagePickerDialog.languageCodes.contains("th"))
        XCTAssertTrue(LanguagePickerDialog.languageCodes.contains("vi"))
    }

    func testResetClearsEverything() {
        let (model, recorder) = makeModel()
        makeReady(model, recorder)
        model.requestTranslate(target: .dialogue, language: "en")
        model.reset()

        XCTAssertFalse(model.isSessionReady)
        XCTAssertFalse(model.anyModeActive)
        XCTAssertFalse(model.isLanguagePickerPresented)
        XCTAssertEqual(model.transcribeSource, .voice)
        XCTAssertEqual(recorder.sources.last, .voice)
    }
}
