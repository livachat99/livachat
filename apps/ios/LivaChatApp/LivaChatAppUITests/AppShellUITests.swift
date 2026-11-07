import XCTest

@MainActor
final class AppShellUITests: XCTestCase {
    override func setUp() {
        super.setUp()
        XCUIDevice.shared.orientation = .portrait
    }

    private func launch(_ arguments: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = arguments + ["-uiTestSkipSplash"]
        app.launch()
        return app
    }

    private func appleLocale(for code: String) -> String {
        switch code {
        case "de": return "de_DE"
        case "ru": return "ru_RU"
        case "hi": return "hi_IN"
        case "ar": return "ar_SA"
        default: return code
        }
    }

    private func attachScreenshot(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testMicButtonAppliesEveryTap() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening"
        ])
        let mic = app.buttons["conversation_mic"]
        XCTAssertTrue(mic.waitForExistence(timeout: 5))
        XCTAssertEqual(mic.label, "Mic on")

        // Tap the icon, caption, and the empty sides of the 64pt target: the
        // whole button area must be tappable, not only the drawn glyph.
        let offsets = [
            CGVector(dx: 0.5, dy: 0.2),
            CGVector(dx: 0.5, dy: 0.85),
            CGVector(dx: 0.08, dy: 0.5),
            CGVector(dx: 0.92, dy: 0.5)
        ]
        // UI tests run without a runtime; the control must still flip local
        // state on every tap instead of dropping taps while media is absent.
        for tap in 1...8 {
            let expected = mic.label == "Mic on" ? "Mic off" : "Mic on"
            mic.coordinate(withNormalizedOffset: offsets[(tap - 1) % offsets.count]).tap()
            let applied = XCTNSPredicateExpectation(
                predicate: NSPredicate(format: "label == %@", expected),
                object: mic
            )
            XCTAssertEqual(
                XCTWaiter.wait(for: [applied], timeout: 2),
                .completed,
                "tap \(tap) was not applied"
            )
        }
        attachScreenshot(app, name: "Mic toggles")
    }

    func testToolbarCaptionsShowNounOnly() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-AppleLanguages", "(zh-Hans)",
            "-AppleLocale", "zh_CN"
        ])

        XCTAssertTrue(app.staticTexts["麦克风"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["扬声器"].exists)
        XCTAssertTrue(app.staticTexts["摄像头"].exists)
        XCTAssertEqual(app.buttons["conversation_mic"].label, "麦克风 已开启")
        XCTAssertEqual(app.buttons["conversation_speaker"].label, "扬声器 已开启")
        attachScreenshot(app, name: "Toolbar captions zh")
    }

    func testErrorBannerHidesTechnicalDetail() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestPermission", "granted",
            "-uiTestConnection", "listening",
            "-uiTestBanner", "captureFailed",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        XCTAssertTrue(app.staticTexts["خطأ في الميكروفون"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["injected technical detail"].exists)
        attachScreenshot(app, name: "Error banner ar")
    }

    func testPermissionScreen() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-uiTestCameraPermission", "undetermined"
        ])

        XCTAssertTrue(app.buttons["permission_start"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Microphone permission"].exists)
        XCTAssertTrue(app.buttons["permission_mic_card"].exists || app.staticTexts["Microphone permission"].exists)
        XCTAssertTrue(app.staticTexts["Camera permission (optional)"].exists)
        XCTAssertTrue(app.buttons["permission_camera"].exists)
        attachScreenshot(app, name: "Permission")
    }

    func testSettingsScreen() {
        let app = launch(["-uiTestScreen", "settings"])

        let server = app.textFields["settings_server"]
        XCTAssertTrue(server.waitForExistence(timeout: 5))
        XCTAssertTrue(app.secureTextFields["settings_secret"].exists)
        XCTAssertTrue(app.buttons["settings_probe"].exists)
        XCTAssertTrue(app.switches["settings_tls_allow_self_signed"].exists)
        XCTAssertTrue(app.switches["settings_camera_default_front"].exists)
        XCTAssertTrue(app.switches["settings_camera_auto_on_join"].exists)
        XCTAssertTrue(app.switches["settings_haptic"].exists)

        server.tap()
        server.typeText("127.0.0.1:18080")
        XCTAssertTrue(app.buttons["settings_save"].waitForExistence(timeout: 2))
        attachScreenshot(app, name: "Settings")
    }

    func testConversationStates() {
        let states: [(argument: String, label: String)] = [
            ("idle", "Say something anytime"),
            ("listening", "I'm listening..."),
            ("thinking", "Let me think..."),
            ("speaking", "Speaking"),
            ("disconnected", "Disconnected")
        ]

        let app = XCUIApplication()
        for state in states {
            if app.state != .notRunning {
                app.terminate()
            }
            app.launchArguments = [
                "-uiTestScreen", "conversation",
                "-uiTestConnection", state.argument,
                "-uiTestMessages", "sample",
                "-uiTestSkipSplash"
            ]
            app.launch()

            let status = app.descendants(matching: .any)
                .matching(identifier: "conversation_status")
                .firstMatch
            XCTAssertTrue(status.waitForExistence(timeout: 5), "missing status for \(state.argument)")
            XCTAssertEqual(status.label, state.label)
            XCTAssertTrue(app.buttons["conversation_mic"].exists)
            XCTAssertTrue(app.buttons["conversation_speaker"].exists)

            if state.argument == "thinking" {
                XCTAssertTrue(
                    app.descendants(matching: .any)
                        .matching(identifier: "conversation_streaming_ai")
                        .firstMatch
                        .exists
                )
            }
            if state.argument == "listening" {
                let bubble = app.descendants(matching: .any)
                    .matching(identifier: "conversation_transcription")
                    .firstMatch
                XCTAssertTrue(bubble.exists)
                XCTAssertLessThan(
                    bubble.frame.width,
                    app.windows.firstMatch.frame.width * 0.8,
                    "transcription bubble should hug its content"
                )
            }

            attachScreenshot(app, name: "Conversation \(state.argument)")
        }
    }

    func testConversationErrorBanner() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "disconnected",
            "-uiTestBanner", "error"
        ])

        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "conversation_banner")
                .firstMatch
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "conversation_status")
                .firstMatch
                .exists
        )
        attachScreenshot(app, name: "Conversation error banner")
    }

    func testCameraModeLayout() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestMessages", "sample",
            "-uiTestCamera", "on",
            "-uiTestCameraFront", "1",
            "-uiTestCameraPermission", "granted"
        ])

        XCTAssertTrue(app.buttons["conversation_switch_camera"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["conversation_camera"].exists)
        XCTAssertTrue(app.buttons["conversation_mic"].exists)
        XCTAssertTrue(app.buttons["conversation_speaker"].exists)

        let bubble = app.descendants(matching: .any)
            .matching(identifier: "conversation_camera_transcription")
            .firstMatch
        XCTAssertTrue(bubble.waitForExistence(timeout: 5))
        XCTAssertLessThan(
            bubble.frame.width,
            app.windows.firstMatch.frame.width * 0.8,
            "camera transcription bubble should hug its content"
        )
        attachScreenshot(app, name: "Camera mode")
    }

    func testCameraTogglePreservesMessageScrollPosition() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestLongMessages",
            "-uiTestCameraPermission", "granted"
        ])

        let camera = app.buttons["conversation_camera"]
        XCTAssertTrue(camera.waitForExistence(timeout: 10))

        let scroll = app.scrollViews.firstMatch
        XCTAssertTrue(scroll.waitForExistence(timeout: 10))
        scroll.swipeUp()
        scroll.swipeUp()
        // Swipes carry momentum; let the list settle before recording the frames.
        Thread.sleep(forTimeInterval: 1.0)

        let beforeScrollFrame = scroll.frame
        let beforeFrames = app.descendants(matching: .any)
            .matching(identifier: "message_ai")
            .allElementsBoundByIndex
            .map { $0.frame.midY }
        XCTAssertFalse(beforeFrames.isEmpty)
        // Ensure the swipes actually moved the list away from the top;
        // otherwise a reset-to-top regression could pass this test.
        XCTAssertTrue(
            beforeFrames.contains { $0 < 0 },
            "Expected offscreen messages above the viewport after scrolling"
        )
        attachScreenshot(app, name: "Before camera")

        camera.tap()
        XCTAssertTrue(app.buttons["conversation_switch_camera"].waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Camera on")

        app.buttons["conversation_camera"].tap()
        let switchGone = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"),
            object: app.buttons["conversation_switch_camera"]
        )
        XCTAssertEqual(XCTWaiter.wait(for: [switchGone], timeout: 5), .completed)
        Thread.sleep(forTimeInterval: 0.3)

        attachScreenshot(app, name: "After camera")
        XCTAssertEqual(scroll.frame.midY, beforeScrollFrame.midY, accuracy: 2.0)
        XCTAssertEqual(scroll.frame.height, beforeScrollFrame.height, accuracy: 2.0)
        let afterFrames = app.descendants(matching: .any)
            .matching(identifier: "message_ai")
            .allElementsBoundByIndex
            .map { $0.frame.midY }
        XCTAssertEqual(afterFrames.count, beforeFrames.count)
        for (before, after) in zip(beforeFrames, afterFrames) {
            XCTAssertEqual(after, before, accuracy: 2.0)
        }
    }

    func testGalleryPreviewLayout() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestGalleryPreview",
            "-uiTestCameraPermission", "granted"
        ])

        XCTAssertTrue(app.buttons["gallery_preview_send"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["gallery_preview_back"].exists)
        XCTAssertTrue(app.buttons["gallery_preview_cancel"].exists)
        attachScreenshot(app, name: "Gallery preview")

        app.buttons["gallery_preview_cancel"].tap()
        XCTAssertFalse(app.buttons["gallery_preview_send"].waitForExistence(timeout: 2))
    }

    func testAIImageBubble() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestMessages", "sample",
            "-uiTestAIImage"
        ])

        let image = app.descendants(matching: .any)
            .matching(identifier: "chat_ai_image")
            .firstMatch
        XCTAssertTrue(image.waitForExistence(timeout: 5))
        attachScreenshot(app, name: "AI image bubble")
    }

    func testAIImageViewer() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestMessages", "sample",
            "-uiTestAIImage"
        ])

        let image = app.descendants(matching: .any)
            .matching(identifier: "chat_ai_image")
            .firstMatch
        XCTAssertTrue(image.waitForExistence(timeout: 5))
        image.tap()

        XCTAssertTrue(app.buttons["image_viewer_save"].waitForExistence(timeout: 5))
        XCTAssertFalse(
            app.descendants(matching: .any)
                .matching(identifier: "image_viewer_page")
                .firstMatch
                .exists
        )

        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertFalse(app.buttons["image_viewer_save"].waitForExistence(timeout: 2))
    }

    func testAIImageViewerPaging() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestMessages", "sample",
            "-uiTestAIImagePair"
        ])

        let image = app.descendants(matching: .any)
            .matching(identifier: "chat_ai_image")
            .firstMatch
        XCTAssertTrue(image.waitForExistence(timeout: 5))
        image.tap()

        let page = app.staticTexts["image_viewer_page"]
        XCTAssertTrue(page.waitForExistence(timeout: 5))
        XCTAssertEqual(page.label, "1 / 2")

        app.swipeLeft()
        let nextPage = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label == %@", "2 / 2"),
            object: page
        )
        XCTAssertEqual(XCTWaiter.wait(for: [nextPage], timeout: 3), .completed)
    }

    func testMarkdownMessageRendering() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestMarkdown"
        ])

        XCTAssertTrue(app.staticTexts["Trip Summary"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Bold, italic, and struck text."].exists)
        XCTAssertTrue(app.staticTexts["First item"].exists)
        XCTAssertTrue(app.staticTexts["City"].exists)
        XCTAssertTrue(
            app.staticTexts
                .matching(NSPredicate(format: "label CONTAINS %@", "**not bold**"))
                .firstMatch
                .exists
        )
        attachScreenshot(app, name: "Markdown message")
    }

    func testWebrootLinkOpensInAppWebView() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestMarkdown"
        ])

        XCTAssertTrue(app.staticTexts["Trip Summary"].waitForExistence(timeout: 5))
        let link = app.staticTexts["Open page"]
        XCTAssertTrue(link.waitForExistence(timeout: 5))
        if !link.isHittable {
            app.swipeUp()
        }
        link.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        XCTAssertTrue(app.buttons["web_page_close"].waitForExistence(timeout: 10))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "web_page_error")
                .firstMatch
                .waitForExistence(timeout: 15)
        )
        XCTAssertTrue(app.buttons["web_page_retry"].exists)
        attachScreenshot(app, name: "Webroot web view")

        app.buttons["web_page_retry"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "web_page_error")
                .firstMatch
                .waitForExistence(timeout: 15)
        )

        app.buttons["web_page_close"].tap()
        let dismissed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"),
            object: app.buttons["web_page_close"]
        )
        XCTAssertEqual(XCTWaiter.wait(for: [dismissed], timeout: 3), .completed)
    }

    func testMarkdownLinkOpensInAppSafari() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestMarkdown"
        ])

        XCTAssertTrue(app.staticTexts["Trip Summary"].waitForExistence(timeout: 5))
        let link = app.staticTexts["Example link"]
        XCTAssertTrue(link.waitForExistence(timeout: 5))
        link.tap()

        guard let dismiss = safariDismissButton(app) else {
            return XCTFail("Safari dismiss button not found")
        }
        attachScreenshot(app, name: "Markdown link Safari")
        // Safari's chrome lives in a remote view; coordinate taps reach it
        // reliably where element taps can be ignored on the simulator.
        dismiss.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        sleep(1)
        XCTAssertTrue(waitForDisappearance(dismiss, timeout: 3))
    }

    func testConversationMessageBubbleWidthMatchesAndroid() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestMarkdown"
        ])
        assertMaxBubbleWidth(
            app.descendants(matching: .any).matching(identifier: "message_ai"),
            in: app
        )
    }

    func testWrappedAIMessagesShareMaxWidth() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestLongMessages"
        ])

        let messages = app.descendants(matching: .any)
            .matching(identifier: "message_ai")
        XCTAssertTrue(messages.firstMatch.waitForExistence(timeout: 5))
        let widths = messages.allElementsBoundByIndex.map(\.frame.width)
        XCTAssertFalse(widths.isEmpty)
        let expected = app.windows.firstMatch.frame.width * 0.75
        for width in widths {
            XCTAssertEqual(
                width,
                expected,
                accuracy: 2,
                "every wrapped AI message should use the same max width"
            )
        }
    }

    func testMessageBubbleAlignmentAndShortTextHugging() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestMessages", "sample"
        ])

        let user = app.descendants(matching: .any)
            .matching(identifier: "message_user")
            .firstMatch
        let ai = app.descendants(matching: .any)
            .matching(identifier: "message_ai")
            .firstMatch
        XCTAssertTrue(user.waitForExistence(timeout: 5))
        XCTAssertTrue(ai.waitForExistence(timeout: 5))

        let window = app.windows.firstMatch.frame
        // User messages hug the right edge of the message column.
        XCTAssertEqual(user.frame.maxX, window.maxX - 16, accuracy: 2)
        // AI messages hug the left edge and only take the width their text needs.
        XCTAssertEqual(ai.frame.minX, window.minX + 16, accuracy: 2)
        XCTAssertLessThan(
            ai.frame.width,
            window.width * 0.75 - 4,
            "a short AI message should hug its text instead of filling the max width"
        )
    }

    func testHistoryMessageBubbleWidthMatchesAndroid() {
        let app = launch(["-uiTestScreen", "history"])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        rows.matching(NSPredicate(format: "label CONTAINS %@", "Plan a trip to Kyoto"))
            .firstMatch
            .tap()
        XCTAssertTrue(app.staticTexts["Conversation details"].waitForExistence(timeout: 5))

        assertMaxBubbleWidth(
            app.descendants(matching: .any).matching(identifier: "message_ai"),
            in: app
        )
        let user = app.descendants(matching: .any)
            .matching(identifier: "message_user")
            .firstMatch
        XCTAssertTrue(user.waitForExistence(timeout: 5))
        XCTAssertEqual(
            user.frame.maxX,
            app.windows.firstMatch.frame.maxX - 16,
            accuracy: 2,
            "user messages should be flush with the right edge"
        )
    }

    func testHotSlotMessageBubbleWidthMatchesAndroid() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])
        let active = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH 'Active at'")
        ).firstMatch
        XCTAssertTrue(active.waitForExistence(timeout: 5))
        active.tap()
        XCTAssertTrue(app.staticTexts["timeline_hotslot_title"].waitForExistence(timeout: 5))

        assertMaxBubbleWidth(
            app.descendants(matching: .any).matching(identifier: "message_agent_job"),
            in: app
        )
        let user = app.descendants(matching: .any)
            .matching(identifier: "message_user")
            .firstMatch
        XCTAssertTrue(user.waitForExistence(timeout: 5))
        XCTAssertEqual(
            user.frame.maxX,
            app.windows.firstMatch.frame.maxX - 16,
            accuracy: 2,
            "user messages should be flush with the right edge"
        )
    }

    private func assertMaxBubbleWidth(
        _ query: XCUIElementQuery,
        in app: XCUIApplication,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(query.firstMatch.waitForExistence(timeout: 5), file: file, line: line)
        let widths = query.allElementsBoundByIndex.map(\.frame.width)
        let expected = app.windows.firstMatch.frame.width * 0.75
        XCTAssertEqual(
            widths.max() ?? 0,
            expected,
            accuracy: 2,
            "message bubble max width should be 75% of the window (Android widthIn)",
            file: file,
            line: line
        )
    }

    func testAgentJobOverlayAndCollapse() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestAgentJob"
        ])

        let headers = app.descendants(matching: .any)
            .matching(identifier: "message_agent_header")
        XCTAssertTrue(headers.firstMatch.waitForExistence(timeout: 5))
        XCTAssertGreaterThanOrEqual(headers.count, 3)

        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "message_agent_collapsed")
                .firstMatch
                .exists
        )

        let jobs = app.descendants(matching: .any)
            .matching(identifier: "message_agent_job")
        XCTAssertGreaterThanOrEqual(jobs.count, 2)
        jobs.element(boundBy: 0).tap()

        let overlay = app.descendants(matching: .any)
            .matching(identifier: "agent_job_result_overlay")
            .firstMatch
        XCTAssertTrue(overlay.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["agent_job_result_close"].exists)
        attachScreenshot(app, name: "Agent job overlay")

        app.buttons["agent_job_result_close"].tap()
        XCTAssertFalse(overlay.waitForExistence(timeout: 2))

        jobs.element(boundBy: 1).tap()
        XCTAssertTrue(overlay.waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.staticTexts
                .matching(NSPredicate(format: "label BEGINSWITH %@", "Paragraph 12:"))
                .firstMatch
                .waitForExistence(timeout: 5)
        )
        app.buttons["agent_job_result_close"].tap()
    }

    func testAgentJobOverlaySupportsLandscapeAndRestoresPortrait() {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }

        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestAgentJob"
        ])

        let jobs = app.descendants(matching: .any)
            .matching(identifier: "message_agent_job")
        XCTAssertTrue(jobs.firstMatch.waitForExistence(timeout: 5))
        jobs.element(boundBy: 0).tap()

        let overlay = app.descendants(matching: .any)
            .matching(identifier: "agent_job_result_overlay")
            .firstMatch
        XCTAssertTrue(overlay.waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Agent job overlay portrait")

        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(waitForWindow(app, landscape: true, timeout: 5))
        attachScreenshot(app, name: "Agent job overlay landscape")

        let close = app.buttons["agent_job_result_close"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        XCTAssertTrue(waitForDisappearance(overlay, timeout: 5))
        XCTAssertTrue(waitForWindow(app, landscape: false, timeout: 5))
    }

    func testAgentJobOverlayAdoptsLandscapeWhenOpened() {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }

        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestAgentJob"
        ])

        let jobs = app.descendants(matching: .any)
            .matching(identifier: "message_agent_job")
        XCTAssertTrue(jobs.firstMatch.waitForExistence(timeout: 5))

        // The app is portrait-locked while the overlay is closed.
        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(waitForWindow(app, landscape: false, timeout: 5))

        jobs.element(boundBy: 0).tap()
        let overlay = app.descendants(matching: .any)
            .matching(identifier: "agent_job_result_overlay")
            .firstMatch
        XCTAssertTrue(overlay.waitForExistence(timeout: 5))
        XCTAssertTrue(waitForWindow(app, landscape: true, timeout: 5))

        app.buttons["agent_job_result_close"].tap()
        XCTAssertTrue(waitForDisappearance(overlay, timeout: 5))
        XCTAssertTrue(waitForWindow(app, landscape: false, timeout: 5))
    }

    private func waitForWindow(
        _ app: XCUIApplication,
        landscape: Bool,
        timeout: TimeInterval
    ) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let frame = app.windows.firstMatch.frame
            if (frame.width > frame.height) == landscape {
                return true
            }
            Thread.sleep(forTimeInterval: 0.1)
        }
        return false
    }

    func testAgentJobLinkDoesNotOpenOverlay() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle",
            "-uiTestAgentJob"
        ])

        let link = app.staticTexts["Open report"]
        XCTAssertTrue(link.waitForExistence(timeout: 5))
        // The link sits inside a bubble that also handles card taps; XCUITest's
        // element tap can miss the inline link run, so tap its center directly.
        link.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        guard let dismiss = safariDismissButton(app) else {
            return XCTFail("Safari dismiss button not found")
        }
        attachScreenshot(app, name: "Agent job link Safari")

        dismiss.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        sleep(1)
        XCTAssertFalse(
            app.descendants(matching: .any)
                .matching(identifier: "agent_job_result_overlay")
                .firstMatch
                .exists
        )
    }

    func testHistoryOpensFromConversationMenu() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "idle"
        ])

        let settings = app.buttons["conversation_settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 5))
        settings.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(app.buttons["Chats"].waitForExistence(timeout: 5))
        app.buttons["Chats"].tap()
        XCTAssertTrue(app.staticTexts["Chat history"].waitForExistence(timeout: 5))
        app.buttons["history_back"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "conversation_status")
                .firstMatch
                .waitForExistence(timeout: 5)
        )
    }

    func testHistoryEmptyState() {
        let app = launch([
            "-uiTestScreen", "history",
            "-uiTestHistoryEmpty"
        ])

        XCTAssertTrue(app.staticTexts["Chat history"].waitForExistence(timeout: 10))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "history_empty")
                .firstMatch
                .waitForExistence(timeout: 5)
        )
        attachScreenshot(app, name: "History empty")
    }

    func testHistoryListSearchAndDetail() {
        let app = launch(["-uiTestScreen", "history"])

        XCTAssertTrue(app.staticTexts["Chat history"].waitForExistence(timeout: 10))
        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 5))
        XCTAssertGreaterThanOrEqual(rows.count, 2)
        XCTAssertTrue(
            rows.matching(NSPredicate(format: "label CONTAINS %@", "Plan a trip to Kyoto"))
                .firstMatch
                .exists
        )

        let search = app.textFields["history_search"]
        XCTAssertTrue(search.exists)
        search.tap()
        search.typeText("Kyoto")

        let kyotoRow = rows.matching(NSPredicate(format: "label CONTAINS %@", "Plan a trip to Kyoto"))
            .firstMatch
        XCTAssertTrue(kyotoRow.waitForExistence(timeout: 5))
        let filtered = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "count == 1"),
            object: app.buttons.matching(identifier: "history_row_select")
        )
        XCTAssertEqual(XCTWaiter.wait(for: [filtered], timeout: 5), .completed)
        kyotoRow.tap()

        XCTAssertTrue(app.staticTexts["Conversation details"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Kyoto guide"].waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "message_agent_header")
                .firstMatch
                .exists
        )
        attachScreenshot(app, name: "History detail")

        app.buttons["history_detail_back"].tap()
        XCTAssertTrue(app.staticTexts["Chat history"].waitForExistence(timeout: 5))
    }

    func testHistoryDeleteAndClear() {
        let app = launch(["-uiTestScreen", "history"])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        XCTAssertGreaterThanOrEqual(rows.count, 2)

        app.buttons.matching(identifier: "history_delete").firstMatch.tap()
        let deleteAlert = app.alerts.firstMatch
        XCTAssertTrue(deleteAlert.waitForExistence(timeout: 5))
        deleteAlert.buttons["Delete"].tap()

        let oneRow = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "count == 1"),
            object: app.buttons.matching(identifier: "history_row_select")
        )
        XCTAssertEqual(XCTWaiter.wait(for: [oneRow], timeout: 5), .completed)

        app.buttons["history_clear"].tap()
        let clearAlert = app.alerts.firstMatch
        XCTAssertTrue(clearAlert.waitForExistence(timeout: 5))
        clearAlert.buttons["Clear"].tap()

        let emptyRow = app.buttons.matching(identifier: "history_row_select")
            .matching(NSPredicate(format: "label CONTAINS %@", "Untitled conversation"))
            .firstMatch
        XCTAssertTrue(emptyRow.waitForExistence(timeout: 5))
        attachScreenshot(app, name: "History after clear")
    }

    func testHistorySwipeDeleteShowsConfirmation() {
        let app = launch(["-uiTestScreen", "history"])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        XCTAssertEqual(rows.count, 2)

        let row = rows.firstMatch
        XCTAssertTrue(row.exists)
        row.swipeLeft()

        let alert = app.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 5))
        alert.buttons["Cancel"].tap()
        XCTAssertEqual(rows.count, 2)
    }

    func testHistoryDetailImageViewer() {
        let app = launch(["-uiTestScreen", "history"])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        rows.matching(NSPredicate(format: "label CONTAINS %@", "Plan a trip to Kyoto"))
            .firstMatch
            .tap()
        XCTAssertTrue(app.staticTexts["Conversation details"].waitForExistence(timeout: 5))

        let image = app.descendants(matching: .any)
            .matching(identifier: "chat_ai_image")
            .firstMatch
        XCTAssertTrue(image.waitForExistence(timeout: 5))
        image.tap()
        XCTAssertTrue(app.buttons["image_viewer_save"].waitForExistence(timeout: 5))
        attachScreenshot(app, name: "History image viewer")
    }

    func testHistoryDetailAgentOverlay() {
        let app = launch(["-uiTestScreen", "history"])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        rows.matching(NSPredicate(format: "label CONTAINS %@", "Plan a trip to Kyoto"))
            .firstMatch
            .tap()
        XCTAssertTrue(app.staticTexts["Conversation details"].waitForExistence(timeout: 5))

        let job = app.descendants(matching: .any)
            .matching(identifier: "message_agent_job")
            .firstMatch
        XCTAssertTrue(job.waitForExistence(timeout: 5))
        if !job.isHittable {
            app.swipeUp()
        }
        job.tap()
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "agent_job_result_overlay")
                .firstMatch
                .waitForExistence(timeout: 5)
        )
        app.buttons["agent_job_result_close"].tap()
        let closed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"),
            object: app.descendants(matching: .any)
                .matching(identifier: "agent_job_result_overlay")
                .firstMatch
        )
        XCTAssertEqual(XCTWaiter.wait(for: [closed], timeout: 3), .completed)
    }

    func testHistoryBubbleLinkKeepsDetail() {
        let app = launch(["-uiTestScreen", "history"])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        rows.matching(NSPredicate(format: "label CONTAINS %@", "Plan a trip to Kyoto"))
            .firstMatch
            .tap()
        XCTAssertTrue(app.staticTexts["Conversation details"].waitForExistence(timeout: 5))

        let link = app.staticTexts["Open report"].firstMatch
        XCTAssertTrue(link.waitForExistence(timeout: 5))
        link.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        guard let dismiss = safariDismissButton(app) else {
            return XCTFail("Safari dismiss button not found")
        }
        dismiss.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(app.staticTexts["Conversation details"].waitForExistence(timeout: 5))
    }

    func testHistoryAgentOverlayLinkOpensSafariAndKeepsOverlay() {
        let app = launch(["-uiTestScreen", "history"])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        rows.matching(NSPredicate(format: "label CONTAINS %@", "Plan a trip to Kyoto"))
            .firstMatch
            .tap()
        XCTAssertTrue(app.staticTexts["Conversation details"].waitForExistence(timeout: 5))

        let job = app.descendants(matching: .any)
            .matching(identifier: "message_agent_job")
            .firstMatch
        XCTAssertTrue(job.waitForExistence(timeout: 5))
        if !job.isHittable {
            app.swipeUp()
        }
        job.tap()

        let overlay = app.descendants(matching: .any)
            .matching(identifier: "agent_job_result_overlay")
            .firstMatch
        XCTAssertTrue(overlay.waitForExistence(timeout: 5))

        let link = overlay.staticTexts["Open report"].firstMatch
        XCTAssertTrue(link.waitForExistence(timeout: 5))
        link.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // Safari must present above the History fullScreenCover; the overlay
        // stays mounted underneath and is visible again after dismissal.
        guard let dismiss = safariDismissButton(app) else {
            return XCTFail("Safari dismiss button not found")
        }
        dismiss.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(overlay.waitForExistence(timeout: 5))
    }

    func testHistoryAgentOverlayUsesFullWindowInLandscape() {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }

        let app = launch(["-uiTestScreen", "history"])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        rows.matching(NSPredicate(format: "label CONTAINS %@", "Plan a trip to Kyoto"))
            .firstMatch
            .tap()
        XCTAssertTrue(app.staticTexts["Conversation details"].waitForExistence(timeout: 5))

        let job = app.descendants(matching: .any)
            .matching(identifier: "message_agent_job")
            .firstMatch
        XCTAssertTrue(job.waitForExistence(timeout: 5))
        if !job.isHittable {
            app.swipeUp()
        }
        job.tap()

        let overlay = app.descendants(matching: .any)
            .matching(identifier: "agent_job_result_overlay")
            .firstMatch
        XCTAssertTrue(overlay.waitForExistence(timeout: 5))

        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(waitForWindow(app, landscape: true, timeout: 5))

        // The History flow keeps its 600pt content column, but the viewer is
        // mounted at the window level and must span the full window width.
        let frame = overlay.frame
        XCTAssertGreaterThan(frame.width, 600)
        attachScreenshot(app, name: "History agent overlay landscape")

        app.buttons["agent_job_result_close"].tap()
        XCTAssertTrue(waitForDisappearance(overlay, timeout: 5))
        XCTAssertTrue(waitForWindow(app, landscape: false, timeout: 5))
    }

    func testChineseLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(zh-Hans)",
            "-AppleLocale", "zh_CN"
        ])

        XCTAssertTrue(app.staticTexts["开始之前，需要你的许可"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission zh-Hans")
    }

    func testTraditionalChineseLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(zh-Hant)",
            "-AppleLocale", "zh_TW"
        ])

        XCTAssertTrue(app.staticTexts["開始之前，需要你的許可"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission zh-Hant")
    }

    func testJapaneseLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(ja)",
            "-AppleLocale", "ja_JP"
        ])

        XCTAssertTrue(app.staticTexts["始める前に、許可が必要です"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission ja")
    }

    func testKoreanLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(ko)",
            "-AppleLocale", "ko_KR"
        ])

        XCTAssertTrue(app.staticTexts["시작하기 전에 권한이 필요해요"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission ko")
    }

    func testRussianLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(ru)",
            "-AppleLocale", "ru_RU"
        ])

        XCTAssertTrue(app.staticTexts["Перед началом нужно разрешение"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission ru")
    }

    func testGermanLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(de)",
            "-AppleLocale", "de_DE"
        ])

        XCTAssertTrue(
            app.staticTexts["Bevor es losgeht, wird eine Berechtigung benötigt"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission de")
    }

    func testFrenchLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(fr)",
            "-AppleLocale", "fr_FR"
        ])

        XCTAssertTrue(
            app.staticTexts["Avant de commencer, une autorisation est nécessaire"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission fr")
    }

    func testItalianLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(it)",
            "-AppleLocale", "it_IT"
        ])

        XCTAssertTrue(
            app.staticTexts["Prima di iniziare serve un'autorizzazione"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission it")
    }

    func testSpanishLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(es)",
            "-AppleLocale", "es_ES"
        ])

        XCTAssertTrue(
            app.staticTexts["Antes de empezar, se necesita un permiso"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission es")
    }

    func testPortugueseLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(pt-BR)",
            "-AppleLocale", "pt_BR"
        ])

        XCTAssertTrue(
            app.staticTexts["Antes de começar, é necessária uma permissão"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission pt")
    }

    func testHindiLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(hi)",
            "-AppleLocale", "hi_IN"
        ])

        XCTAssertTrue(
            app.staticTexts["शुरू करने से पहले एक अनुमति चाहिए"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission hi")
    }

    func testIndonesianLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(id)",
            "-AppleLocale", "id_ID"
        ])

        XCTAssertTrue(
            app.staticTexts["Sebelum mulai, diperlukan izin"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission id")
    }

    func testArabicLocalization() {
        let app = launch([
            "-uiTestScreen", "permission",
            "-uiTestPermission", "undetermined",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        XCTAssertTrue(
            app.staticTexts["قبل أن نبدأ، نحتاج إلى إذنك"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["permission_start"].exists)
        attachScreenshot(app, name: "Permission ar")
    }

    // MARK: - Agent job status

    private func launchJobStatus(
        count: Int,
        cancelResult: String = "success",
        extra: [String] = []
    ) -> XCUIApplication {
        launch([
            "-uiTestScreen", "conversation",
            "-uiTestPermission", "granted",
            "-uiTestCameraPermission", "granted",
            "-uiTestConnection", "idle",
            "-uiTestJobStatusCount", "\(count)",
            "-uiTestJobCancelResult", cancelResult
        ] + extra)
    }

    private func jobRows(_ app: XCUIApplication) -> XCUIElementQuery {
        app.descendants(matching: .any).matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "agent_job_row_")
        )
    }

    private func panel(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any)["agent_job_panel"]
    }

    private func waitForRowCount(
        _ app: XCUIApplication,
        count: Int,
        timeout: TimeInterval
    ) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        repeat {
            if jobRows(app).count == count { return true }
            Thread.sleep(forTimeInterval: 0.2)
        } while Date() < deadline
        return jobRows(app).count == count
    }

    private func waitForDisappearance(_ element: XCUIElement, timeout: TimeInterval) -> Bool {
        let expectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"),
            object: element
        )
        return XCTWaiter.wait(for: [expectation], timeout: timeout) == .completed
    }

    /// iOS 26+ Safari uses a floating X (identifier "Close") instead of the
    /// older "Done" button.
    private func safariDismissButton(
        _ app: XCUIApplication,
        timeout: TimeInterval = 10
    ) -> XCUIElement? {
        let button = app.buttons
            .matching(NSPredicate(format: "identifier == 'Done' OR identifier == 'Close'"))
            .firstMatch
        return button.waitForExistence(timeout: timeout) ? button : nil
    }

    private func openJobPanel(_ app: XCUIApplication) -> XCUIElement {
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        badge.tap()
        let panel = panel(app)
        XCTAssertTrue(panel.waitForExistence(timeout: 5))
        return panel
    }

    private func tapOutsidePanel(_ app: XCUIApplication) {
        let panel = panel(app)
        app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
            .withOffset(CGVector(dx: panel.frame.midX, dy: panel.frame.minY - 20))
            .tap()
    }

    func testAgentJobPanelAndCancelSuccess() {
        let app = launchJobStatus(count: 2)
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.value as? String, "2")

        _ = openJobPanel(app)
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 3))
        XCTAssertFalse(app.buttons["conversation_mic"].isHittable)
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label CONTAINS %@", "Checking inventory levels"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        attachScreenshot(app, name: "Agent job panel")

        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].waitForExistence(timeout: 3))
        attachScreenshot(app, name: "Agent job cancel confirm")
        app.buttons["agent_job_cancel_no"].tap()
        XCTAssertTrue(
            waitForDisappearance(
                app.descendants(matching: .any)["agent_job_cancel_confirm"],
                timeout: 2
            )
        )

        action.tap()
        let yes = app.buttons["agent_job_cancel_yes"]
        XCTAssertTrue(yes.waitForExistence(timeout: 3))
        yes.tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["agent_job_cancel_spinner"]
                .waitForExistence(timeout: 3)
        )
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 1))
        XCTAssertTrue(waitForRowCount(app, count: 1, timeout: 15))

        tapOutsidePanel(app)
        XCTAssertTrue(waitForDisappearance(panel(app), timeout: 3))
    }

    func testAgentJobCancelWithoutSnapshotKeepsRow() {
        let app = launchJobStatus(count: 2, cancelResult: "success-no-snapshot")
        _ = openJobPanel(app)
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 3))

        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].waitForExistence(timeout: 3))
        app.buttons["agent_job_cancel_yes"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["agent_job_cancel_spinner"]
                .waitForExistence(timeout: 3)
        )
        XCTAssertTrue(
            waitForDisappearance(app.buttons["agent_job_cancel_no"], timeout: 15)
        )
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 2))
        attachScreenshot(app, name: "Agent job cancel without snapshot")
    }

    func testAgentJobCancelFailureShowsRetry() {
        let app = launchJobStatus(count: 2, cancelResult: "failure")
        _ = openJobPanel(app)
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 3))

        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].waitForExistence(timeout: 3))
        app.buttons["agent_job_cancel_yes"].tap()

        XCTAssertTrue(
            app.staticTexts["Cancel failed. Try again"].waitForExistence(timeout: 15)
        )
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        attachScreenshot(app, name: "Agent job cancel failure")
        app.buttons["agent_job_cancel_no"].tap()
        XCTAssertTrue(waitForDisappearance(app.buttons["agent_job_cancel_no"], timeout: 2))
    }

    func testAgentJobCancelNotFoundIsSilent() {
        let app = launchJobStatus(count: 2, cancelResult: "not_found")
        _ = openJobPanel(app)
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 3))

        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].waitForExistence(timeout: 3))
        app.buttons["agent_job_cancel_yes"].tap()

        XCTAssertTrue(waitForDisappearance(app.buttons["agent_job_cancel_no"], timeout: 15))
        XCTAssertFalse(app.staticTexts["Cancel failed. Try again"].exists)
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 2))
    }

    func testAgentJobInFlightSurvivesPanelDismiss() {
        let app = launchJobStatus(count: 2)
        _ = openJobPanel(app)
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 3))

        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].waitForExistence(timeout: 3))
        app.buttons["agent_job_cancel_yes"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["agent_job_cancel_spinner"]
                .waitForExistence(timeout: 3)
        )

        tapOutsidePanel(app)
        XCTAssertTrue(waitForDisappearance(panel(app), timeout: 3))
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 3))
        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 3))
        XCTAssertTrue(
            app.descendants(matching: .any)["agent_job_cancel_spinner"]
                .waitForExistence(timeout: 3)
        )
        XCTAssertTrue(waitForRowCount(app, count: 1, timeout: 15))
    }

    func testAgentJobPanelHeightAndScroll() {
        let single = launchJobStatus(count: 1)
        let singleBadge = single.buttons["agent_job_badge"]
        XCTAssertTrue(singleBadge.waitForExistence(timeout: 5))
        XCTAssertEqual(singleBadge.label, "1 agent task running")
        let singlePanel = openJobPanel(single)
        let singleHeight = singlePanel.frame.height
        XCTAssertGreaterThan(singleHeight, 0)
        single.terminate()

        let many = launchJobStatus(count: 10)
        let manyPanel = openJobPanel(many)
        XCTAssertTrue(waitForRowCount(many, count: 10, timeout: 5))
        let manyHeight = manyPanel.frame.height
        XCTAssertGreaterThan(manyHeight, singleHeight)
        let mic = many.buttons["conversation_mic"]
        XCTAssertTrue(mic.exists)
        XCTAssertLessThanOrEqual(manyPanel.frame.maxY, mic.frame.minY - 20)
        attachScreenshot(many, name: "Agent job panel capped")

        manyPanel.swipeUp()
        let lastRow = many.descendants(matching: .any)["agent_job_row_ui-job-10"]
        XCTAssertTrue(lastRow.waitForExistence(timeout: 5))
        XCTAssertTrue(lastRow.isHittable)
    }

    func testAgentJobBadgeClampsAt99Plus() {
        let app = launchJobStatus(count: 100)
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.value as? String, "99+")
        XCTAssertEqual(badge.label, "100 agent tasks running")
        attachScreenshot(app, name: "Agent job badge 99+")
    }

    func testAgentJobBadgeInCameraMode() {
        let app = launchJobStatus(count: 2, extra: ["-uiTestCamera", "on"])
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 3))
        tapOutsidePanel(app)
        XCTAssertTrue(waitForDisappearance(panel(app), timeout: 3))
    }

    func testAgentJobPanelDynamicType() {
        let app = launchJobStatus(
            count: 2,
            extra: [
                "-UIPreferredContentSizeCategoryName",
                "UICTContentSizeCategoryAccessibilityXXXL"
            ]
        )
        _ = openJobPanel(app)
        XCTAssertTrue(waitForRowCount(app, count: 2, timeout: 3))

        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job Dynamic Type")
        tapOutsidePanel(app)
        XCTAssertTrue(waitForDisappearance(panel(app), timeout: 3))
    }

    func testAgentJobChineseLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "Agent任务，2 个运行中")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Agent任务 (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["取消 Agent任务？"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job zh-Hans")
    }

    func testAgentJobTraditionalChineseLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(zh-Hant)", "-AppleLocale", "zh_TW"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "Agent任務，2 個執行中")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Agent任務 (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["取消 Agent任務？"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job zh-Hant")
    }

    func testAgentJobJapaneseLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(ja)", "-AppleLocale", "ja_JP"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "Agentタスク、2 件実行中")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Agentタスク (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["Agentタスクをキャンセルしますか？"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job ja")
    }

    func testAgentJobKoreanLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(ko)", "-AppleLocale", "ko_KR"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "Agent 작업, 2개 실행 중")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Agent 작업 (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["Agent 작업을 취소할까요?"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job ko")
    }

    func testAgentJobRussianLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(ru)", "-AppleLocale", "ru_RU"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "Запущены 2 задачи агента")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Задачи агента (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["Отменить задачу агента?"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job ru")
    }

    func testAgentJobGermanLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(de)", "-AppleLocale", "de_DE"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "2 Agent-Aufgaben laufen")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Agent-Aufgaben (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["Agent-Aufgabe abbrechen?"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job de")
    }

    func testAgentJobFrenchLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(fr)", "-AppleLocale", "fr_FR"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "2 tâches d'agent en cours")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Tâches d'agent (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(
            app.staticTexts["Annuler la tâche d'agent\u{00A0}?"].waitForExistence(timeout: 3)
        )
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job fr")
    }

    func testAgentJobItalianLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(it)", "-AppleLocale", "it_IT"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "2 attività dell'agente in corso")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Attività dell'agente (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["Annullare attività dell'agente?"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job it")
    }

    func testAgentJobSpanishLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(es)", "-AppleLocale", "es_ES"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "2 tareas del agente en curso")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Tareas del agente (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["¿Cancelar tarea del agente?"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job es")
    }

    func testAgentJobPortugueseLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(pt-BR)", "-AppleLocale", "pt_BR"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "2 tarefas do agente em andamento")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Tarefas do agente (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["Cancelar tarefa do agente?"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job pt")
    }

    func testAgentJobHindiLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(hi)", "-AppleLocale", "hi_IN"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "2 एजेंट कार्य चल रहे हैं")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "एजेंट कार्य (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["एजेंट कार्य रद्द करें?"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job hi")
    }

    func testAgentJobIndonesianLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(id)", "-AppleLocale", "id_ID"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "2 tugas agen berjalan")
        XCTAssertEqual(badge.value as? String, "2")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Tugas agen (2)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["Batalkan tugas agen?"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job id")
    }

    func testAgentJobArabicLocalization() {
        let app = launchJobStatus(
            count: 2,
            extra: ["-AppleLanguages", "(ar)", "-AppleLocale", "ar_SA"]
        )
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 5))
        XCTAssertEqual(badge.label, "٢ مهمتان قيد التشغيل")
        XCTAssertEqual(badge.value as? String, "٢")
        XCTAssertFalse(badge.label.contains("%"))

        badge.tap()
        XCTAssertTrue(panel(app).waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "مهمتا الوكيل (٢)"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.staticTexts["إلغاء مهمة الوكيل؟"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["agent_job_cancel_no"].exists)
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].exists)
        attachScreenshot(app, name: "Agent job ar")
    }

    // MARK: - RTL layout

    func testAIImageViewerPagingRTL() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestPermission", "granted",
            "-uiTestConnection", "idle",
            "-uiTestMessages", "sample",
            "-uiTestAIImagePair",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        let image = app.descendants(matching: .any)
            .matching(identifier: "chat_ai_image")
            .firstMatch
        XCTAssertTrue(image.waitForExistence(timeout: 5))
        // The decoded image needs a layout pass before it has a valid hit
        // point; tapping immediately can compute (-1, -1).
        let hittable = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "hittable == true"),
            object: image
        )
        XCTAssertEqual(XCTWaiter.wait(for: [hittable], timeout: 5), .completed)
        image.tap()

        let page = app.staticTexts["image_viewer_page"]
        XCTAssertTrue(page.waitForExistence(timeout: 5))
        let first = page.label
        XCTAssertTrue(first.contains("١"), first)

        app.swipeRight()
        let changed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label != %@", first),
            object: page
        )
        XCTAssertEqual(XCTWaiter.wait(for: [changed], timeout: 3), .completed)
        XCTAssertTrue(page.label.contains("٢"), page.label)
    }

    func testHistorySwipeDeleteRTL() {
        let app = launch([
            "-uiTestScreen", "history",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        let rows = app.buttons.matching(identifier: "history_row_select")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 10))
        XCTAssertEqual(rows.count, 2)

        rows.firstMatch.swipeRight()
        let alert = app.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 5))
        XCTAssertTrue(alert.buttons["إلغاء"].exists)
        alert.buttons["إلغاء"].tap()
        XCTAssertEqual(rows.count, 2)
    }

    func testMessageBubbleAlignmentRTL() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestPermission", "granted",
            "-uiTestConnection", "idle",
            "-uiTestMessages", "sample",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        let user = app.descendants(matching: .any)
            .matching(identifier: "message_user")
            .firstMatch
        let ai = app.descendants(matching: .any)
            .matching(identifier: "message_ai")
            .firstMatch
        XCTAssertTrue(user.waitForExistence(timeout: 5))
        XCTAssertTrue(ai.waitForExistence(timeout: 5))

        let window = app.windows.firstMatch.frame
        XCTAssertEqual(user.frame.minX, window.minX + 16, accuracy: 2)
        XCTAssertEqual(ai.frame.maxX, window.maxX - 16, accuracy: 2)
        attachScreenshot(app, name: "Bubble alignment ar")
    }

    func testAvatarHiddenClusterIsCenteredRTL() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestPermission", "granted",
            "-uiTestConnection", "listening",
            "-uiTestAvatarHidden",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        let hidden = app.buttons["conversation_avatar_hidden"]
        XCTAssertTrue(hidden.waitForExistence(timeout: 5))
        let windowMidX = app.windows.firstMatch.frame.midX
        XCTAssertLessThan(abs(hidden.frame.midX - windowMidX), 2)
        attachScreenshot(app, name: "Avatar hidden cluster ar")
    }

    func testSettingsBackAndTechnicalFieldsRTL() {
        let app = launch([
            "-uiTestScreen", "settings",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        let back = app.buttons["settings_back"]
        XCTAssertTrue(back.waitForExistence(timeout: 5))
        XCTAssertGreaterThan(back.frame.midX, app.windows.firstMatch.frame.midX)

        let server = app.textFields["settings_server"]
        XCTAssertTrue(server.exists)
        server.tap()
        server.typeText("127.0.0.1:18080")
        XCTAssertEqual(server.value as? String, "127.0.0.1:18080")
        attachScreenshot(app, name: "Settings technical fields ar")
    }

    func testTimelineChipsStayInBoundsRTL() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        let chips = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "timeline_hot_slot_chip_")
        )
        XCTAssertTrue(chips.firstMatch.waitForExistence(timeout: 5))
        let all = chips.allElementsBoundByIndex
        XCTAssertGreaterThanOrEqual(all.count, 2)

        let card = app.descendants(matching: .any)
            .matching(identifier: "timeline_card_2026-08-21")
            .firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        for chip in all.prefix(2) {
            XCTAssertGreaterThan(chip.frame.midX, card.frame.midX)
            XCTAssertLessThanOrEqual(chip.frame.maxX, card.frame.maxX + 1)
        }
        if all.count >= 2, abs(all[0].frame.minY - all[1].frame.minY) < 1 {
            XCTAssertGreaterThan(
                all[0].frame.midX,
                all[1].frame.midX,
                "the first chip should start at the right in RTL"
            )
        }

        let badge = app.buttons["timeline_image_badge"]
        if badge.waitForExistence(timeout: 3) {
            XCTAssertLessThan(
                badge.frame.midX,
                card.frame.midX,
                "the image-count badge should sit on the outer left corner in RTL"
            )
        }
        attachScreenshot(app, name: "Timeline chips ar")
    }

    func testTimelineGalleryPagingRTL() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home",
            "-AppleLanguages", "(ar)",
            "-AppleLocale", "ar_SA"
        ])

        let image = app.buttons["timeline_image_uitest-frame"]
        XCTAssertTrue(image.waitForExistence(timeout: 5))
        image.tap()
        let photo = app.buttons["timeline_gallery_photo_uitest-frame"]
        XCTAssertTrue(photo.waitForExistence(timeout: 5))
        photo.tap()

        let page = app.staticTexts["timeline_gallery_page"]
        XCTAssertTrue(page.waitForExistence(timeout: 5))
        let first = page.label
        XCTAssertTrue(first.contains("١"), first)

        app.swipeRight()
        let changed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label != %@", first),
            object: page
        )
        XCTAssertEqual(XCTWaiter.wait(for: [changed], timeout: 3), .completed)
        XCTAssertTrue(page.label.contains("٢"), page.label)
    }

    // MARK: - Dynamic Type

    private let accessibilityXXXL = "UICTContentSizeCategoryAccessibilityXXXL"

    func testSettingsAtAccessibilitySize() {
        let app = launch([
            "-uiTestScreen", "settings",
            "-UIPreferredContentSizeCategoryName", accessibilityXXXL
        ])
        XCTAssertTrue(app.textFields["settings_server"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.secureTextFields["settings_secret"].exists)
        XCTAssertTrue(app.buttons["settings_probe"].exists)
        XCTAssertTrue(app.buttons["settings_back"].exists)
        attachScreenshot(app, name: "Settings AX")
    }

    func testHistoryAtAccessibilitySize() {
        let app = launch([
            "-uiTestScreen", "history",
            "-UIPreferredContentSizeCategoryName", accessibilityXXXL
        ])
        XCTAssertTrue(
            app.buttons.matching(identifier: "history_row_select")
                .firstMatch.waitForExistence(timeout: 10)
        )
        XCTAssertTrue(app.textFields["history_search"].exists)
        XCTAssertTrue(app.buttons["history_back"].exists)
        attachScreenshot(app, name: "History AX")
    }

    func testMyLifeAtAccessibilitySize() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home",
            "-UIPreferredContentSizeCategoryName", accessibilityXXXL
        ])
        XCTAssertTrue(app.staticTexts["timeline_title"].waitForExistence(timeout: 5))
        XCTAssertTrue(
            app.buttons.matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "timeline_hot_slot_chip_")
            ).firstMatch.waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["timeline_audio_entry"].exists)
        XCTAssertTrue(app.buttons["timeline_back"].exists)
        attachScreenshot(app, name: "MyLife AX")
    }

    func testConversationAtAccessibilitySize() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestPermission", "granted",
            "-uiTestConnection", "listening",
            "-uiTestMessages", "sample",
            "-UIPreferredContentSizeCategoryName", accessibilityXXXL
        ])
        XCTAssertTrue(
            app.descendants(matching: .any)["conversation_status"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.buttons["conversation_mic"].exists)
        XCTAssertTrue(app.buttons["conversation_speaker"].exists)
        XCTAssertTrue(
            app.descendants(matching: .any).matching(identifier: "message_ai")
                .firstMatch.exists
        )
        attachScreenshot(app, name: "Conversation AX")
    }

    func testLongTextLocalesAtAccessibilitySize() {
        for code in ["de", "ru", "hi", "ar"] {
            let app = launch([
                "-uiTestScreen", "conversation",
                "-uiTestPermission", "granted",
                "-uiTestConnection", "listening",
                "-uiTestMessages", "sample",
                "-UIPreferredContentSizeCategoryName", accessibilityXXXL,
                "-AppleLanguages", "(\(code))",
                "-AppleLocale", appleLocale(for: code)
            ])
            XCTAssertTrue(
                app.descendants(matching: .any)["conversation_status"]
                    .waitForExistence(timeout: 5),
                code
            )
            XCTAssertTrue(app.buttons["conversation_mic"].exists, code)
            XCTAssertTrue(app.buttons["conversation_speaker"].exists, code)
            attachScreenshot(app, name: "Conversation AX \(code)")
            app.terminate()
        }
    }

    func testRealServerModeSwitchE2E() throws {
        guard ProcessInfo.processInfo.environment["LIVA_REAL_MODE_E2E"] == "1" else {
            throw XCTSkip("Manual real-server E2E; set LIVA_REAL_MODE_E2E=1")
        }

        let app = XCUIApplication()
        app.terminate()
        app.launch()

        let avatar = app.buttons["conversation_avatar"]
        guard avatar.waitForExistence(timeout: 20) else {
            attachScreenshot(app, name: "Real mode E2E not in conversation")
            return XCTFail("app did not reach the conversation")
        }
        let status = app.descendants(matching: .any)["conversation_status"]
        let connected = XCTNSPredicateExpectation(
            predicate: NSPredicate(
                format: "label CONTAINS[c] %@ OR label CONTAINS[c] %@",
                "listening",
                "Say something"
            ),
            object: status
        )
        XCTAssertEqual(XCTWaiter.wait(for: [connected], timeout: 20), .completed)

        try selectMode(app, avatar: avatar, switchID: "avatar_mode_dialogue", picker: "language_picker", auto: true)
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Translate mode"))
        sleep(2)
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Translate mode"))
        turnOffMode(app, avatar: avatar, switchID: "avatar_mode_dialogue")

        try selectMode(app, avatar: avatar, switchID: "avatar_mode_lecture", picker: "language_picker", auto: true)
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Translate mode"))
        sleep(2)
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Translate mode"))
        turnOffMode(app, avatar: avatar, switchID: "avatar_mode_lecture")

        try selectMode(app, avatar: avatar, switchID: "avatar_mode_transcribe", picker: "transcribe_source", originalSource: true)
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Transcribe mode"))
        sleep(2)
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Transcribe mode"))
        turnOffMode(app, avatar: avatar, switchID: "avatar_mode_transcribe")

        try selectMode(app, avatar: avatar, switchID: "avatar_mode_omni", picker: "omni_intro")
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Omni mode"))
        sleep(2)
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Omni mode"))
        turnOffMode(app, avatar: avatar, switchID: "avatar_mode_omni")
        attachScreenshot(app, name: "Real mode E2E complete")
    }

    private func selectMode(
        _ app: XCUIApplication,
        avatar: XCUIElement,
        switchID: String,
        picker: String,
        auto: Bool = false,
        originalSource: Bool = false
    ) throws {
        avatar.tap()
        let sheet = avatarSheet(app)
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        let toggle = app.switches[switchID]
        XCTAssertTrue(toggle.waitForExistence(timeout: 3))
        XCTAssertTrue(toggle.isEnabled)
        toggle.tap()

        switch picker {
        case "language_picker":
            let dialog = app.descendants(matching: .any)["language_picker_dialog"]
            XCTAssertTrue(dialog.waitForExistence(timeout: 5))
            if auto {
                app.buttons["language_picker_select"].tap()
                let autoItem = app.buttons["Auto-detect"]
                XCTAssertTrue(autoItem.waitForExistence(timeout: 3))
                autoItem.tap()
            }
            app.buttons["language_picker_confirm"].tap()
        case "transcribe_source":
            let dialog = app.descendants(matching: .any)["transcribe_source_dialog"]
            XCTAssertTrue(dialog.waitForExistence(timeout: 5))
            if originalSource {
                app.buttons["transcribe_source_original"].tap()
            }
            app.buttons["transcribe_source_confirm"].tap()
        case "omni_intro":
            let dialog = app.descendants(matching: .any)["omni_intro_dialog"]
            XCTAssertTrue(dialog.waitForExistence(timeout: 5))
            app.buttons["omni_dialog_enable"].tap()
        default:
            XCTFail("unknown picker \(picker)")
        }
        XCTAssertTrue(waitForDisappearance(sheet, timeout: 5))
    }

    private func turnOffMode(
        _ app: XCUIApplication,
        avatar: XCUIElement,
        switchID: String
    ) {
        avatar.tap()
        let sheet = avatarSheet(app)
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        let toggle = app.switches[switchID]
        XCTAssertTrue(toggle.waitForExistence(timeout: 3))
        toggle.tap()
        XCTAssertTrue(sheet.exists)
        app.buttons["avatar_sheet_close"].tap()
        XCTAssertTrue(waitForDisappearance(sheet, timeout: 5))
        sleep(2)
        XCTAssertFalse(app.buttons["conversation_avatar"].label.contains("mode"))
    }

    func testRealServerAgentJobCancel() throws {
        guard ProcessInfo.processInfo.environment["LIVA_REAL_JOB_E2E"] == "1" else {
            throw XCTSkip("Manual real-server E2E; set LIVA_REAL_JOB_E2E=1")
        }

        let app = XCUIApplication()
        app.activate()
        let badge = app.buttons["agent_job_badge"]
        XCTAssertTrue(badge.waitForExistence(timeout: 20), "no running job badge")
        badge.tap()
        let panel = panel(app)
        XCTAssertTrue(panel.waitForExistence(timeout: 5))

        let action = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "agent_job_cancel_action_")
        ).firstMatch
        XCTAssertTrue(action.waitForExistence(timeout: 5))
        let identifier = action.identifier
        let jobID = identifier.replacingOccurrences(
            of: "agent_job_cancel_action_",
            with: ""
        )
        action.tap()
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].waitForExistence(timeout: 3))
        app.buttons["agent_job_cancel_yes"].tap()
        attachScreenshot(app, name: "Real server agent job cancelling")

        // Real Hermes cancellation can converge before the 1s tick exposes the
        // spinner; the row disappearing is the user-visible success signal.
        let row = app.descendants(matching: .any)["agent_job_row_\(jobID)"]
        XCTAssertTrue(waitForDisappearance(row, timeout: 30))
    }

    func testAgentJobLastCancelClosesPanel() {
        let app = launchJobStatus(count: 1)
        let panel = openJobPanel(app)
        XCTAssertTrue(waitForRowCount(app, count: 1, timeout: 3))

        let action = app.buttons["agent_job_cancel_action_ui-job-1"]
        XCTAssertTrue(action.waitForExistence(timeout: 3))
        action.tap()
        XCTAssertTrue(app.buttons["agent_job_cancel_yes"].waitForExistence(timeout: 3))
        app.buttons["agent_job_cancel_yes"].tap()
        XCTAssertTrue(waitForDisappearance(panel, timeout: 15))
        XCTAssertTrue(waitForDisappearance(app.buttons["agent_job_badge"], timeout: 5))
    }

    private func avatarSheet(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any)["avatar_customization_sheet"]
    }

    func testAvatarSheetOpensAndSwitchesDisabledWhenNotReady() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestAvatarSheet"
        ])
        let sheet = avatarSheet(app)
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        let dialogue = app.switches["avatar_mode_dialogue"]
        XCTAssertTrue(dialogue.exists)
        XCTAssertEqual(dialogue.label, "Conversation mode")
        XCTAssertFalse(dialogue.isEnabled)
        XCTAssertTrue(app.buttons["avatar_sheet_close"].exists)
        attachScreenshot(app, name: "Avatar sheet")

        app.buttons["avatar_sheet_close"].tap()
        XCTAssertTrue(waitForDisappearance(sheet, timeout: 3))
    }

    func testAvatarSheetAtAccessibilitySize() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestAvatarSheet",
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryAccessibilityXXXL"
        ])
        let sheet = avatarSheet(app)
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        XCTAssertTrue(app.switches["avatar_mode_dialogue"].exists)
        attachScreenshot(app, name: "Avatar sheet AX top")
        let takePhoto = app.buttons["avatar_take_photo"]
        if !takePhoto.isHittable {
            sheet.swipeUp()
        }
        XCTAssertTrue(takePhoto.waitForExistence(timeout: 3))
        attachScreenshot(app, name: "Avatar sheet AX")
    }

    func testAvatarHiddenClusterIsCentered() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestAvatarHidden"
        ])
        let hidden = app.buttons["conversation_avatar_hidden"]
        XCTAssertTrue(hidden.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["conversation_avatar"].exists)
        let windowMidX = app.windows.firstMatch.frame.midX
        XCTAssertLessThan(abs(hidden.frame.midX - windowMidX), 2)
        attachScreenshot(app, name: "Avatar hidden cluster")
    }

    func testAvatarShrinkAndRestore() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestAvatarSheet"
        ])
        let sheet = avatarSheet(app)
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        app.buttons["avatar_scale_shrink"].tap()
        app.buttons["avatar_sheet_close"].tap()
        XCTAssertTrue(waitForDisappearance(sheet, timeout: 3))
        XCTAssertTrue(app.buttons["conversation_avatar_hidden"].waitForExistence(timeout: 3))
        XCTAssertFalse(app.buttons["conversation_avatar"].exists)

        app.buttons["conversation_avatar_hidden"].tap()
        XCTAssertTrue(sheet.waitForExistence(timeout: 3))
        app.buttons["avatar_scale_normal"].tap()
        app.buttons["avatar_sheet_close"].tap()
        XCTAssertTrue(waitForDisappearance(sheet, timeout: 3))
        XCTAssertTrue(app.buttons["conversation_avatar"].waitForExistence(timeout: 3))
        XCTAssertFalse(app.buttons["conversation_avatar_hidden"].exists)
    }

    func testAvatarCustomImageAndReset() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestAvatar",
            "-uiTestAvatarSheet"
        ])
        let sheet = avatarSheet(app)
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        let reset = app.buttons["avatar_reset"]
        XCTAssertTrue(reset.waitForExistence(timeout: 5))
        reset.tap()
        XCTAssertTrue(waitForDisappearance(sheet, timeout: 3))
    }

    func testAvatarCropFixture() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestAvatarCrop"
        ])
        let crop = app.descendants(matching: .any)["avatar_crop_view"]
        XCTAssertTrue(crop.waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["avatar_crop_image"].exists)
        XCTAssertTrue(app.buttons["avatar_crop_confirm"].exists)
        attachScreenshot(app, name: "Avatar crop")

        app.buttons["avatar_crop_cancel"].tap()
        XCTAssertTrue(waitForDisappearance(crop, timeout: 3))
        XCTAssertTrue(app.buttons["conversation_mic"].exists)
    }

    func testModeDialoguePickerShowsBadge() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestModeReady",
            "-uiTestAvatarSheet"
        ])
        XCTAssertTrue(avatarSheet(app).waitForExistence(timeout: 5))
        let dialogue = app.switches["avatar_mode_dialogue"]
        XCTAssertTrue(dialogue.waitForExistence(timeout: 3))
        XCTAssertTrue(dialogue.isEnabled)
        dialogue.tap()

        let dialog = app.descendants(matching: .any)["language_picker_dialog"]
        XCTAssertTrue(dialog.waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["language_picker_confirm"].exists)
        app.buttons["language_picker_confirm"].tap()
        XCTAssertTrue(waitForDisappearance(dialog, timeout: 3))
        XCTAssertTrue(waitForDisappearance(avatarSheet(app), timeout: 3))

        let avatar = app.buttons["conversation_avatar"]
        XCTAssertTrue(avatar.waitForExistence(timeout: 3))
        XCTAssertTrue(avatar.label.contains("Translate mode"))
        attachScreenshot(app, name: "Translate mode badge")
    }

    func testModeDialogueOffKeepsSheet() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestMode", "dialogue",
            "-uiTestAvatarSheet"
        ])
        let sheet = avatarSheet(app)
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        let dialogue = app.switches["avatar_mode_dialogue"]
        XCTAssertTrue(dialogue.waitForExistence(timeout: 3))
        XCTAssertEqual(dialogue.value as? String, "1")
        dialogue.tap()
        XCTAssertTrue(sheet.exists)
        app.buttons["avatar_sheet_close"].tap()
        XCTAssertTrue(waitForDisappearance(sheet, timeout: 3))
        XCTAssertFalse(app.buttons["conversation_avatar"].label.contains("Translate mode"))
    }

    func testModeLecturePickerFixture() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestModePicker", "lecture"
        ])
        let dialog = app.descendants(matching: .any)["language_picker_dialog"]
        XCTAssertTrue(dialog.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Speaker's language"].exists)
        attachScreenshot(app, name: "Language picker")
        app.buttons["language_picker_cancel"].tap()
        XCTAssertTrue(waitForDisappearance(dialog, timeout: 3))
        XCTAssertTrue(avatarSheet(app).exists)
        app.buttons["avatar_sheet_close"].tap()
    }

    func testModeTranscribePickerConfirms() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestModePicker", "transcribe"
        ])
        let dialog = app.descendants(matching: .any)["transcribe_source_dialog"]
        XCTAssertTrue(dialog.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["transcribe_source_original"].exists)
        XCTAssertTrue(app.buttons["transcribe_source_voice"].exists)
        attachScreenshot(app, name: "Transcribe source picker")
        app.buttons["transcribe_source_confirm"].tap()
        XCTAssertTrue(waitForDisappearance(dialog, timeout: 3))
        XCTAssertTrue(waitForDisappearance(avatarSheet(app), timeout: 3))
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Transcribe mode"))
    }

    func testModeDialogAtAccessibilitySize() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestModePicker", "transcribe",
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryAccessibilityXXXL"
        ])
        let dialog = app.descendants(matching: .any)["transcribe_source_dialog"]
        XCTAssertTrue(dialog.waitForExistence(timeout: 5))
        let confirm = app.buttons["transcribe_source_confirm"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 3))
        for _ in 0..<3 where !confirm.isHittable {
            dialog.swipeUp()
        }
        XCTAssertTrue(confirm.isHittable, "confirm button unreachable at AX size")
        attachScreenshot(app, name: "Transcribe dialog AX")

        confirm.tap()
        XCTAssertTrue(waitForDisappearance(dialog, timeout: 3))
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Transcribe mode"))
    }

    func testModeOmniPickerConfirms() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestModePicker", "omni"
        ])
        let dialog = app.descendants(matching: .any)["omni_intro_dialog"]
        XCTAssertTrue(dialog.waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Omni intro")
        app.buttons["omni_dialog_enable"].tap()
        XCTAssertTrue(waitForDisappearance(dialog, timeout: 3))
        XCTAssertTrue(waitForDisappearance(avatarSheet(app), timeout: 3))
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Omni mode"))
    }

    func testLectureSecondLineSuppressesStreamingBubble() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestMode", "lecture"
        ])
        XCTAssertTrue(
            app.descendants(matching: .any)["conversation_transcription"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertTrue(
            app.descendants(matching: .any)["conversation_lecture_ai"].exists
        )
        XCTAssertFalse(
            app.descendants(matching: .any)["conversation_streaming_ai"].exists
        )
        XCTAssertTrue(app.buttons["conversation_avatar"].label.contains("Translate mode"))
        attachScreenshot(app, name: "Lecture second line")
    }

    func testHiddenClusterModeAndJobBadge() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestAvatarHidden",
            "-uiTestMode", "lecture",
            "-uiTestJobStatus"
        ])
        let hidden = app.buttons["conversation_avatar_hidden"]
        XCTAssertTrue(hidden.waitForExistence(timeout: 5))
        XCTAssertTrue(hidden.label.contains("Translate mode"))
        XCTAssertEqual(app.buttons.matching(identifier: "agent_job_badge").count, 1)
        let windowMidX = app.windows.firstMatch.frame.midX
        XCTAssertLessThan(abs(hidden.frame.midX - windowMidX), 2)
        attachScreenshot(app, name: "Hidden cluster mode and job")
    }

    func testCameraHiddenClusterHasSingleJobBadge() {
        let app = launch([
            "-uiTestScreen", "conversation",
            "-uiTestConnection", "listening",
            "-uiTestCamera", "on",
            "-uiTestAvatarHidden",
            "-uiTestJobStatus"
        ])
        XCTAssertTrue(app.buttons["conversation_avatar_hidden"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["conversation_avatar"].exists)
        XCTAssertEqual(app.buttons.matching(identifier: "agent_job_badge").count, 1)
        attachScreenshot(app, name: "Camera hidden cluster")
    }

    func testTimelineHomeAndSearch() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])

        XCTAssertTrue(app.staticTexts["timeline_title"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.textFields["timeline_search"].exists)
        XCTAssertTrue(app.buttons["timeline_granularity_day"].exists)
        XCTAssertTrue(app.buttons["timeline_granularity_week"].exists)
        XCTAssertTrue(app.buttons["timeline_granularity_month"].exists)
        XCTAssertTrue(app.buttons["timeline_granularity_year"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["timeline_card_2026-08-21"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["timeline_audio_entry"].exists)

        let timelineImage = app.buttons["timeline_image_uitest-frame"]
        XCTAssertTrue(timelineImage.waitForExistence(timeout: 5))
        XCTAssertEqual(
            timelineImage.frame.width,
            timelineImage.frame.height,
            accuracy: 1.0,
            "timeline thumbnails must be square"
        )
        attachScreenshot(app, name: "Timeline Home")

        app.buttons["timeline_granularity_month"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["timeline_card_2026-08"].waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Timeline Month")

        let search = app.textFields["timeline_search"]
        search.tap()
        search.typeText("hello")
        XCTAssertTrue(app.staticTexts["timeline_search_total"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["3 results"].waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Timeline Search")
    }

    func testTimelineMonthCardChildTapsDoNotDrillDown() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])

        XCTAssertTrue(app.buttons["timeline_granularity_month"].waitForExistence(timeout: 5))
        app.buttons["timeline_granularity_month"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["timeline_card_2026-08"].waitForExistence(timeout: 5))

        let image = app.buttons["timeline_image_uitest-frame"]
        XCTAssertTrue(image.waitForExistence(timeout: 5))
        image.tap()
        XCTAssertTrue(app.staticTexts["timeline_gallery_title"].waitForExistence(timeout: 5))
        app.buttons["timeline_gallery_back"].tap()
        XCTAssertTrue(app.staticTexts["timeline_title"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["timeline_scope"].exists)
        XCTAssertTrue(app.buttons["timeline_granularity_month"].isSelected)

        let topic = app.buttons["travel"]
        XCTAssertTrue(topic.waitForExistence(timeout: 5))
        topic.tap()
        XCTAssertTrue(app.staticTexts["timeline_search_total"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["timeline_scope"].exists)
        app.buttons["timeline_back"].tap()
        XCTAssertTrue(app.buttons["timeline_granularity_month"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["timeline_scope"].exists)

        let card = app.descendants(matching: .any)["timeline_card_2026-08"]
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        card.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.06)).tap()
        XCTAssertTrue(app.buttons["timeline_scope"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["timeline_granularity_day"].isSelected)
    }

    func testTimelineEmptyState() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "empty"
        ])
        XCTAssertTrue(app.staticTexts["No records yet — start a conversation"].waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Timeline Empty")
    }

    func testTimelineBackReturnsToConversation() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])
        XCTAssertTrue(app.buttons["timeline_back"].waitForExistence(timeout: 5))
        app.buttons["timeline_back"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(identifier: "conversation_status")
                .firstMatch
                .waitForExistence(timeout: 5)
        )
    }


    func testTimelineHotSlotAndAgentResult() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Active at'")).firstMatch.waitForExistence(timeout: 5))
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Active at'")).firstMatch.tap()

        XCTAssertTrue(app.staticTexts["timeline_hotslot_title"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Hello from this slot"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Transcript text (transcript)"].waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Timeline Hot Slot")

        app.staticTexts["Agent task"].firstMatch.tap()
        XCTAssertTrue(app.otherElements["agent_job_result_overlay"].waitForExistence(timeout: 5))
        app.buttons["agent_job_result_close"].tap()
        XCTAssertTrue(app.staticTexts["timeline_hotslot_title"].waitForExistence(timeout: 5))

        app.buttons["timeline_hotslot_back"].tap()
        XCTAssertTrue(app.staticTexts["timeline_title"].waitForExistence(timeout: 5))
    }

    func testTimelineAgentOverlayUsesFullWindowInLandscape() {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }

        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])
        let active = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH 'Active at'")
        ).firstMatch
        XCTAssertTrue(active.waitForExistence(timeout: 5))
        active.tap()

        XCTAssertTrue(app.staticTexts["timeline_hotslot_title"].waitForExistence(timeout: 5))
        app.staticTexts["Agent task"].firstMatch.tap()

        let overlay = app.otherElements["agent_job_result_overlay"]
        XCTAssertTrue(overlay.waitForExistence(timeout: 5))

        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(waitForWindow(app, landscape: true, timeout: 5))

        // The viewer lives above the Hot Slot content and must use the full
        // window width rather than the 600pt timeline column.
        let frame = overlay.frame
        XCTAssertGreaterThan(frame.width, 600)
        attachScreenshot(app, name: "Timeline agent overlay landscape")

        app.buttons["agent_job_result_close"].tap()
        XCTAssertTrue(waitForDisappearance(overlay, timeout: 5))
        XCTAssertTrue(waitForWindow(app, landscape: false, timeout: 5))
        XCTAssertTrue(app.staticTexts["timeline_hotslot_title"].waitForExistence(timeout: 5))
    }

    func testTimelineSearchHitOpensEventList() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])
        let search = app.textFields["timeline_search"]
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.tap()
        search.typeText("hello")
        XCTAssertTrue(app.staticTexts["3 results"].waitForExistence(timeout: 5))

        let hit = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Chat,'")).firstMatch
        XCTAssertTrue(hit.waitForExistence(timeout: 5))
        hit.tap()
        XCTAssertTrue(app.staticTexts["timeline_hotslot_title"].waitForExistence(timeout: 5))
        app.buttons["timeline_hotslot_back"].tap()
        XCTAssertTrue(app.staticTexts["timeline_title"].waitForExistence(timeout: 5))
    }


    func testTimelineGalleryViewerAndEdit() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])
        let image = app.buttons["timeline_image_uitest-frame"]
        XCTAssertTrue(image.waitForExistence(timeout: 5))
        image.tap()

        XCTAssertTrue(app.staticTexts["timeline_gallery_title"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Analyzing... · 8 photos"].waitForExistence(timeout: 5))
        let photo = app.buttons["timeline_gallery_photo_uitest-frame"]
        XCTAssertTrue(photo.waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Timeline Gallery")

        photo.tap()
        XCTAssertTrue(app.staticTexts["timeline_gallery_page"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.staticTexts["timeline_gallery_page"].label, "1 / 8")
        XCTAssertTrue(app.buttons["timeline_gallery_save"].exists)

        app.swipeLeft()
        XCTAssertTrue(app.staticTexts["2 / 8"].waitForExistence(timeout: 3))
        app.swipeRight()
        XCTAssertTrue(app.staticTexts["1 / 8"].waitForExistence(timeout: 3))

        attachScreenshot(app, name: "Timeline Gallery Viewer")
        app.buttons["timeline_gallery_viewer_close"].tap()

        XCTAssertTrue(app.buttons["timeline_gallery_edit"].waitForExistence(timeout: 5))
        app.buttons["timeline_gallery_edit"].tap()
        let select = app.buttons["timeline_gallery_select_uitest-frame"]
        XCTAssertTrue(select.waitForExistence(timeout: 5))
        select.tap()
        let delete = app.buttons["timeline_gallery_delete"]
        XCTAssertTrue(delete.waitForExistence(timeout: 5))
        XCTAssertTrue(delete.isEnabled)
        attachScreenshot(app, name: "Timeline Gallery Edit")

        app.buttons["timeline_gallery_back"].tap()
        XCTAssertTrue(app.staticTexts["timeline_gallery_title"].waitForExistence(timeout: 5))
        app.buttons["timeline_gallery_back"].tap()
        XCTAssertTrue(app.staticTexts["timeline_title"].waitForExistence(timeout: 5))
    }


    func testTimelineAudioGalleryPlaybackAndTimeJump() {
        let app = launch([
            "-uiTestScreen", "timeline",
            "-uiTestTimelineFixture", "home"
        ])
        let entry = app.buttons["timeline_audio_entry"].firstMatch
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()

        XCTAssertTrue(app.staticTexts["timeline_audio_title"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["2 segments"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["This is a transcript long enough to render as the body."].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["A fallback summary for the second segment."].waitForExistence(timeout: 5))
        attachScreenshot(app, name: "Timeline Audio")

        let play = app.buttons["timeline_audio_play_uitest-audio-1"]
        XCTAssertTrue(play.waitForExistence(timeout: 5))
        play.tap()
        XCTAssertTrue(
            app.buttons.matching(NSPredicate(format: "identifier == 'timeline_audio_play_uitest-audio-1' AND label == 'Pause'"))
                .firstMatch
                .waitForExistence(timeout: 3)
        )
        app.buttons["timeline_audio_play_uitest-audio-1"].tap()

        let timeJump = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'View events within 10 minutes'")).firstMatch
        XCTAssertTrue(timeJump.waitForExistence(timeout: 5))
        XCTAssertGreaterThanOrEqual(timeJump.frame.height, 32)
        timeJump.tap()
        XCTAssertTrue(app.staticTexts["timeline_hotslot_title"].waitForExistence(timeout: 5))
        app.buttons["timeline_hotslot_back"].tap()
        XCTAssertTrue(app.staticTexts["timeline_audio_title"].waitForExistence(timeout: 5))
        let audioBack = app.buttons["timeline_audio_back"]
        XCTAssertTrue(audioBack.waitForExistence(timeout: 5))
        audioBack.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(app.staticTexts["timeline_title"].waitForExistence(timeout: 5))
    }

    func testAIGeneratedNoticeShowsOnce() {
        let base = [
            "-uiTestScreen", "conversation",
            "-uiTestPermission", "granted",
            "-uiTestConnection", "listening",
            "-uiTestShowAINotice"
        ]

        let first = launch(base + ["-uiTestResetAINotice"])
        XCTAssertTrue(first.staticTexts["ai_generated_notice"].waitForExistence(timeout: 5))

        // The notice must stay until the user taps close.
        Thread.sleep(forTimeInterval: 6)
        XCTAssertTrue(first.staticTexts["ai_generated_notice"].exists)

        first.buttons["ai_generated_notice_close"].tap()
        XCTAssertFalse(first.staticTexts["ai_generated_notice"].waitForExistence(timeout: 2))
        first.terminate()

        let second = launch(base)
        XCTAssertFalse(second.staticTexts["ai_generated_notice"].waitForExistence(timeout: 2))
    }

    func testAIGeneratedNoticeStaysUntilDismissed() {
        let base = [
            "-uiTestScreen", "conversation",
            "-uiTestPermission", "granted",
            "-uiTestConnection", "listening",
            "-uiTestShowAINotice"
        ]

        let first = launch(base + ["-uiTestResetAINotice"])
        XCTAssertTrue(first.staticTexts["ai_generated_notice"].waitForExistence(timeout: 5))
        first.terminate()

        // Not dismissed: the next launch must show it again.
        let second = launch(base)
        XCTAssertTrue(second.staticTexts["ai_generated_notice"].waitForExistence(timeout: 5))
        second.buttons["ai_generated_notice_close"].tap()
        XCTAssertFalse(second.staticTexts["ai_generated_notice"].waitForExistence(timeout: 2))
        second.terminate()

        // Dismissed: the third launch must not show it.
        let third = launch(base)
        XCTAssertFalse(third.staticTexts["ai_generated_notice"].waitForExistence(timeout: 2))
    }

    func testSettingsLegalAndLicenses() {
        let app = launch(["-uiTestScreen", "settings"])
        XCTAssertTrue(app.buttons["settings_privacy"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["settings_eula"].exists)
        XCTAssertTrue(app.buttons["settings_support"].exists)

        let licenses = app.buttons["settings_licenses"]
        XCTAssertTrue(licenses.exists)
        licenses.tap()

        XCTAssertTrue(app.staticTexts["licenses_title"].waitForExistence(timeout: 5))
        app.buttons["licenses_close"].tap()
        XCTAssertTrue(app.buttons["settings_privacy"].waitForExistence(timeout: 5))
    }

    func testSettingsPrivacyOpensSafari() {
        let app = launch([
            "-uiTestScreen", "settings",
            "-AppleLanguages", "(en)",
            "-AppleLocale", "en_US"
        ])
        let privacy = app.buttons["settings_privacy"]
        XCTAssertTrue(privacy.waitForExistence(timeout: 5))
        privacy.tap()

        attachScreenshot(app, name: "Privacy Safari")

        // SFSafariViewController's close button is a system chrome button with
        // no stable identifier; tap the top-left X by coordinate. If the row
        // did nothing, this coordinate would hit Settings' back button and the
        // following assertion would fail.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.09, dy: 0.095)).tap()
        XCTAssertTrue(privacy.waitForExistence(timeout: 5))
    }

}
