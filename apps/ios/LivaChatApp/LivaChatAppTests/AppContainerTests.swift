import LivaChatSDK
import SwiftUI
import XCTest
@testable import LivaChatApp

@MainActor
private final class ContainerPermission: MicPermissionProviding {
    var state: MicPermissionState
    var permissionAfterRequest: MicPermissionState

    init(_ state: MicPermissionState, afterRequest: MicPermissionState = .granted) {
        self.state = state
        self.permissionAfterRequest = afterRequest
    }

    func current() -> MicPermissionState { state }
    func request(_ completion: @escaping @MainActor (MicPermissionState) -> Void) {
        state = permissionAfterRequest
        completion(state)
    }
}

private final class ContainerProbe: ServerProbing, Sendable {
    func probe(address: ServerAddress, wireToken: String, deviceID: String) async -> ProbeResult {
        .available
    }
}

@MainActor
final class AppContainerTests: XCTestCase {
    private func makeContainer(
        arguments: [String] = ["-uiTestSkipSplash"],
        permission: ContainerPermission = ContainerPermission(.granted),
        onboarding: Bool = false
    ) -> (AppContainer, SettingsStore, FakeCredentials, ContainerPermission) {
        let suite = "dev.livachat.container.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        let store = SettingsStore(defaults: defaults)
        if onboarding {
            store.markOnboardingCompleted()
        }
        let credentials = FakeCredentials()
        let container = AppContainer(
            launchOptions: AppLaunchOptions(arguments: arguments, environment: [:]),
            settingsStore: store,
            credentials: credentials,
            permissionProvider: permission,
            probe: ContainerProbe()
        )
        return (container, store, credentials, permission)
    }

    func testFreshLaunchRoutesToPermission() async {
        let (container, _, _, _) = makeContainer(onboarding: false)
        await container.bootstrap()
        XCTAssertEqual(container.router.phase, .permission)
    }

    func testCompletedOnboardingAndPermissionRoutesToConversationWithoutConfig() async {
        let (container, _, _, _) = makeContainer(onboarding: true)
        await container.bootstrap()

        XCTAssertEqual(container.router.phase, .conversation)
        XCTAssertTrue(container.conversationViewModel?.requiresServerConfig == true)
    }

    func testDeniedPermissionStaysOnPermissionScreen() async {
        let (container, _, _, _) = makeContainer(
            permission: ContainerPermission(.denied),
            onboarding: true
        )
        await container.bootstrap()
        XCTAssertEqual(container.router.phase, .permission)
    }

    func testRequestingPermissionStaysOnPermissionUntilStart() async {
        let permission = ContainerPermission(.undetermined, afterRequest: .granted)
        let (container, store, _, _) = makeContainer(
            permission: permission,
            onboarding: false
        )
        await container.bootstrap()
        XCTAssertEqual(container.router.phase, .permission)

        container.startOrRequestPermission()

        XCTAssertEqual(container.micPermission, .granted)
        XCTAssertFalse(store.onboardingCompleted)
        XCTAssertEqual(container.router.phase, .permission)

        container.startOrRequestPermission()

        XCTAssertTrue(store.onboardingCompleted)
        XCTAssertEqual(container.router.phase, .conversation)
    }

    func testReturningFromSettingsRechecksPermission() async {
        let permission = ContainerPermission(.undetermined, afterRequest: .granted)
        let (container, _, _, _) = makeContainer(
            permission: permission,
            onboarding: true
        )
        await container.bootstrap()
        XCTAssertEqual(container.router.phase, .permission)

        permission.state = .granted
        container.handleScenePhase(.active)

        XCTAssertEqual(container.micPermission, .granted)
        XCTAssertEqual(container.router.phase, .permission)

        container.startOrRequestPermission()
        XCTAssertEqual(container.router.phase, .conversation)
    }

    func testUITestConversationPreviewDoesNotStartRuntime() async {
        let (container, _, _, _) = makeContainer(
            arguments: [
                "-uiTestScreen", "conversation",
                "-uiTestSkipSplash",
                "-uiTestMessages", "sample",
                "-uiTestConnection", "listening"
            ],
            onboarding: true
        )
        await container.bootstrap()

        guard let viewModel = container.conversationViewModel else {
            XCTFail("conversation view model missing")
            return
        }
        XCTAssertEqual(container.router.phase, .conversation)
        XCTAssertEqual(viewModel.sessionState, .listening)
        XCTAssertEqual(viewModel.messages.count, 2)
        XCTAssertFalse(viewModel.requiresServerConfig)
    }

    func testUITestSettingsScreenOpensSheet() async {
        let (container, _, _, _) = makeContainer(
            arguments: ["-uiTestScreen", "settings", "-uiTestSkipSplash"],
            onboarding: true
        )
        await container.bootstrap()

        XCTAssertEqual(container.router.phase, .conversation)
        XCTAssertTrue(container.router.showSettings)
        XCTAssertNotNil(container.settingsViewModel)
    }

    func testUITestJobStatusPreviewSeedsJobs() async {
        let (container, _, _, _) = makeContainer(
            arguments: [
                "-uiTestScreen", "conversation",
                "-uiTestJobStatusCount", "3",
                "-uiTestSkipSplash"
            ],
            onboarding: true
        )
        await container.bootstrap()

        XCTAssertEqual(container.agentJobStatusModel.jobs.map(\.jobID), [
            "ui-job-1", "ui-job-2", "ui-job-3"
        ])
    }

    func testOpeningHistoryAndSettingsDismissesJobPanel() async {
        let (container, _, _, _) = makeContainer(onboarding: true)
        await container.bootstrap()
        let job = AgentJob(
            jobID: "job-1",
            agentName: "hermes",
            summary: "Task",
            startedAtMs: 1_000,
            runningTimeMs: 2_000
        )
        container.agentJobStatusModel.replaceJobs([job])
        container.agentJobStatusModel.togglePanel()
        XCTAssertTrue(container.agentJobStatusModel.isPanelPresented)

        container.openHistory()
        XCTAssertFalse(container.agentJobStatusModel.isPanelPresented)

        container.agentJobStatusModel.togglePanel()
        container.openSettings()
        XCTAssertFalse(container.agentJobStatusModel.isPanelPresented)
    }
}
