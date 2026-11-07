import XCTest

@MainActor
final class AudioSpikeUITests: XCTestCase {
    private func value(of key: String, in status: String) -> Int {
        for token in status.split(separator: " ") where token.hasPrefix("\(key)=") {
            return Int(token.dropFirst(key.count + 1)) ?? 0
        }
        return 0
    }

    func testAudioCaptureSourceSwitches() {
        let app = XCUIApplication()
        app.launchArguments = ["-audioDebugHarness"]
        app.launch()

        let start = app.buttons["audio_spike_start"]
        XCTAssertTrue(start.waitForExistence(timeout: 10))
        start.tap()

        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let alert = springboard.alerts.firstMatch
        if alert.waitForExistence(timeout: 3) {
            for label in ["Allow", "OK", "允许"] where alert.buttons[label].exists {
                alert.buttons[label].tap()
                break
            }
        }

        let status = app.staticTexts["audio_spike_status"]
        let source = app.staticTexts["audio_spike_source_status"]
        let aec = app.staticTexts["audio_spike_aec"]
        XCTAssertEqual(XCTWaiter.wait(
            for: [XCTNSPredicateExpectation(
                predicate: NSPredicate(format: "label BEGINSWITH %@", "capturing"),
                object: status
            )],
            timeout: 10
        ), .completed)
        XCTAssertEqual(source.label, "Source: voice")

        app.buttons["audio_spike_source"].tap()
        XCTAssertEqual(XCTWaiter.wait(
            for: [XCTNSPredicateExpectation(
                predicate: NSPredicate(format: "label BEGINSWITH %@", "capturing"),
                object: status
            )],
            timeout: 10
        ), .completed)
        XCTAssertEqual(source.label, "Source: original")
        #if !targetEnvironment(simulator)
        XCTAssertTrue(aec.label.contains("disabled"), aec.label)
        #endif

        app.buttons["audio_spike_source"].tap()
        XCTAssertEqual(source.label, "Source: voice")
        #if !targetEnvironment(simulator)
        XCTAssertTrue(aec.label.contains("enabled"), aec.label)
        #endif
        app.buttons["audio_spike_stop"].tap()
    }

    func testAudioSpikeStartsAndQueuesTone() {
        let app = XCUIApplication()
        app.launchArguments = ["-audioDebugHarness"]
        app.launch()

        addUIInterruptionMonitor(withDescription: "microphone permission") { alert in
            for label in ["Allow", "OK", "允许"] where alert.buttons[label].exists {
                alert.buttons[label].tap()
                return true
            }
            return false
        }

        let start = app.buttons["audio_spike_start"]
        XCTAssertTrue(start.waitForExistence(timeout: 10), "audio spike harness not shown")
        start.tap()

        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let alert = springboard.alerts.firstMatch
        if alert.waitForExistence(timeout: 3) {
            for label in ["Allow", "OK", "允许"] where alert.buttons[label].exists {
                alert.buttons[label].tap()
                break
            }
        }

        let status = app.staticTexts["audio_spike_status"]
        let capturing = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label BEGINSWITH %@", "capturing"),
            object: status
        )
        XCTAssertEqual(XCTWaiter.wait(for: [capturing], timeout: 10), .completed)

        app.buttons["audio_spike_tone"].tap()
        XCTAssertEqual(status.label, "tone queued")

        sleep(1)
        app.buttons["audio_spike_stats"].tap()
        let diagnostic = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        diagnostic.name = "Audio spike diagnostics"
        diagnostic.lifetime = .keepAlways
        add(diagnostic)
        XCTAssertTrue(status.label.hasPrefix("capturing"), "unexpected status: \(status.label)")
        XCTAssertTrue(status.label.contains("scheduled=50"), "tone was not scheduled: \(status.label)")
        XCTAssertTrue(app.staticTexts["audio_spike_aec"].label.contains("enabled"))

        #if !targetEnvironment(simulator)
        XCTAssertTrue(
            value(of: "taps", in: status.label) > 0,
            "microphone tap never fired: \(status.label)"
        )
        XCTAssertTrue(
            value(of: "uplink", in: status.label) > 0,
            "capture never produced uplink frames: \(status.label)"
        )

        app.buttons["audio_spike_mute"].tap()
        sleep(2)
        app.buttons["audio_spike_stats"].tap()
        let mutedUplink = value(of: "uplink", in: status.label)
        sleep(1)
        app.buttons["audio_spike_stats"].tap()
        XCTAssertLessThanOrEqual(
            abs(value(of: "uplink", in: status.label) - mutedUplink),
            2,
            "capture kept sending after the mute window: \(status.label)"
        )

        app.buttons["audio_spike_mute"].tap()
        sleep(1)
        app.buttons["audio_spike_stats"].tap()
        XCTAssertGreaterThan(
            value(of: "uplink", in: status.label),
            mutedUplink,
            "capture did not resume after unmute: \(status.label)"
        )
        #endif

        app.buttons["audio_spike_interrupt"].tap()
        XCTAssertEqual(status.label, "interrupt simulated interrupts=1")

        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "Audio spike harness"
        attachment.lifetime = .keepAlways
        add(attachment)

        app.buttons["audio_spike_stop"].tap()
        let stopped = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label == %@", "stopped"),
            object: status
        )
        XCTAssertEqual(XCTWaiter.wait(for: [stopped], timeout: 5), .completed)
    }
}
