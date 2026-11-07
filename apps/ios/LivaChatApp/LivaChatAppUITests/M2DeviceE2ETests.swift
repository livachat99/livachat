import XCTest

@MainActor
final class M2DeviceE2ETests: XCTestCase {
    // Override with M2_MOCK_URL; the default is a placeholder LAN address.
    private let mockURL = ProcessInfo.processInfo.environment["M2_MOCK_URL"]
        ?? "ws://192.168.1.10:18080/v1/ws/stream"
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")

    @discardableResult
    private func allowSystemAlert(timeout: TimeInterval = 5) -> Bool {
        let alert = springboard.alerts.firstMatch
        guard alert.waitForExistence(timeout: timeout) else { return false }
        for label in ["Allow", "OK", "允许", "好", "使用 App 时允许"] {
            if alert.buttons[label].exists {
                alert.buttons[label].tap()
                return true
            }
        }
        let buttons = alert.buttons
        if buttons.count > 0 {
            buttons.element(boundBy: buttons.count - 1).tap()
            return true
        }
        return false
    }

    func testCameraLifecycleAgainstMockServer() throws {
        guard ProcessInfo.processInfo.environment["M2_DEVICE_E2E"] == "1" else {
            throw XCTSkip("Manual device E2E; set M2_DEVICE_E2E=1 and M2_MOCK_URL to a reachable mock on 18080")
        }
        let app = XCUIApplication()
        app.launchEnvironment = [
            "LIVACHAT_SERVER_URL": mockURL,
            "LIVACHAT_SERVER_TOKEN": "dev-token",
            "LIVACHAT_DEVICE_ID": UUID().uuidString,
        ]
        app.launchArguments = ["-uiTestSkipSplash"]
        addUIInterruptionMonitor(withDescription: "Allow system permissions") { alert in
            for label in ["Allow", "OK", "允许", "好", "使用 App 时允许"] {
                if alert.buttons[label].exists {
                    alert.buttons[label].tap()
                    return true
                }
            }
            return false
        }
        app.launch()

        if app.buttons["permission_start"].waitForExistence(timeout: 3) {
            app.buttons["permission_start"].tap()
            allowSystemAlert()
            // The app intentionally stays on the permission screen after
            // mic authorization; the user starts the conversation explicitly.
            if app.buttons["permission_start"].waitForExistence(timeout: 2) {
                app.buttons["permission_start"].tap()
            }
        }

        XCTAssertTrue(app.buttons["conversation_camera"].waitForExistence(timeout: 10))
        app.buttons["conversation_camera"].tap()
        allowSystemAlert(timeout: 3)

        XCTAssertTrue(
            app.buttons["conversation_switch_camera"].waitForExistence(timeout: 15),
            "camera preview did not become active"
        )

        // Give the ~1fps uplink a few seconds to reach the mock server.
        sleep(4)

        app.buttons["conversation_switch_camera"].tap()
        sleep(2)

        app.buttons["conversation_camera"].tap()
        XCTAssertTrue(app.buttons["conversation_camera"].waitForExistence(timeout: 5))

        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "M2 device camera"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
