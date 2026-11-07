import LivaChatSDK
import XCTest
@testable import LivaChatApp

private final class FakeProbe: ServerProbing, @unchecked Sendable {
    var result: ProbeResult = .available
    var calls: [(address: ServerAddress, token: String, deviceID: String)] = []

    func probe(address: ServerAddress, wireToken: String, deviceID: String) async -> ProbeResult {
        calls.append((address, wireToken, deviceID))
        return result
    }
}

final class SettingsViewModelTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suiteName: String!

    override func setUp() {
        super.setUp()
        suiteName = "dev.livachat.settingsvm.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
        super.tearDown()
    }

    @MainActor
    private func makeViewModel(
        probe: FakeProbe = FakeProbe(),
        onApply: @escaping () -> Void = {},
        onHaptic: @escaping (Bool) -> Void = { _ in },
        onAvdetector: @escaping (Bool) -> Void = { _ in }
    ) -> (SettingsViewModel, SettingsStore, FakeCredentials, FakeProbe) {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()
        let viewModel = SettingsViewModel(
            store: store,
            credentials: credentials,
            probe: probe,
            onApply: onApply,
            onHapticChanged: onHaptic,
            onAvdetectorChanged: onAvdetector
        )
        return (viewModel, store, credentials, probe)
    }

    @MainActor
    func testInitLoadsSavedConfig() throws {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()
        _ = try store.saveServerConfig(address: "a.example:1", secret: "secret", credentials: credentials)

        let viewModel = SettingsViewModel(
            store: store,
            credentials: credentials,
            probe: FakeProbe(),
            onApply: {}
        )

        XCTAssertEqual(viewModel.address, "a.example:1")
        XCTAssertEqual(viewModel.secret, "secret")
        XCTAssertFalse(viewModel.isDirty)
        XCTAssertFalse(viewModel.canSave)
    }

    @MainActor
    func testEmptyAddressHidesAndClearsStoredSecret() {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()
        credentials.values[.serverSecret] = "stale-secret"
        credentials.values[.wireToken] = "stale-wire-token"

        let viewModel = SettingsViewModel(
            store: store,
            credentials: credentials,
            probe: FakeProbe(),
            onApply: {}
        )

        XCTAssertEqual(viewModel.address, "")
        XCTAssertEqual(viewModel.secret, "")
        XCTAssertEqual(viewModel.savedSecret, "")
        XCTAssertFalse(viewModel.isDirty)
        XCTAssertNil(credentials.values[.serverSecret])
        XCTAssertNil(credentials.values[.wireToken])
    }

    @MainActor
    func testClearingAddressClearsDisplayedSecret() throws {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()
        _ = try store.saveServerConfig(
            address: "a.example:1",
            secret: "secret",
            credentials: credentials
        )
        let viewModel = SettingsViewModel(
            store: store,
            credentials: credentials,
            probe: FakeProbe(),
            onApply: {}
        )
        XCTAssertEqual(viewModel.secret, "secret")

        viewModel.address = ""

        XCTAssertEqual(viewModel.secret, "")
        XCTAssertFalse(viewModel.canSave)
        XCTAssertTrue(viewModel.isDirty)
        XCTAssertEqual(credentials.values[.serverSecret], "secret")
    }

    @MainActor
    func testValidationAndDirtyState() {
        let (viewModel, _, _, _) = makeViewModel()
        viewModel.address = "bad"
        viewModel.secret = "s"
        XCTAssertTrue(viewModel.isDirty)
        XCTAssertFalse(viewModel.canSave)
        viewModel.checkService()
        XCTAssertEqual(viewModel.validationMessageKey, "settings_error_address")

        viewModel.address = "a.example:1"
        XCTAssertTrue(viewModel.canSave)

        viewModel.secret = "  "
        XCTAssertFalse(viewModel.canSave)
    }

    @MainActor
    func testCheckServiceUsesDerivedTokenAndDeviceID() async throws {
        let probe = FakeProbe()
        let (viewModel, _, credentials, _) = makeViewModel(probe: probe)
        viewModel.address = "a.example:1"
        viewModel.secret = "secret"

        viewModel.checkService()
        try await waitUntil { !viewModel.isProbing && viewModel.probeResult != nil }

        XCTAssertEqual(viewModel.probeResult, .available)
        XCTAssertEqual(probe.calls.count, 1)
        XCTAssertEqual(probe.calls[0].token, WireToken.make(secret: "secret"))
        XCTAssertEqual(probe.calls[0].deviceID, credentials.deviceIDValue)
    }

    @MainActor
    func testApplyPersistsAndNotifies() throws {
        var applied = 0
        let (viewModel, _, credentials, _) = makeViewModel(onApply: { applied += 1 })
        viewModel.address = "a.example:1"
        viewModel.secret = "secret"

        viewModel.requestApply()
        XCTAssertTrue(viewModel.showApplyConfirmation)

        XCTAssertTrue(viewModel.apply())
        XCTAssertEqual(applied, 1)
        XCTAssertFalse(viewModel.showApplyConfirmation)
        XCTAssertEqual(credentials.values[.serverSecret], "secret")
        XCTAssertEqual(credentials.values[.wireToken], WireToken.make(secret: "secret"))
        XCTAssertFalse(viewModel.isDirty)
    }

    @MainActor
    func testApplyFailureDoesNotNotify() {
        var applied = 0
        let (viewModel, _, credentials, _) = makeViewModel(onApply: { applied += 1 })
        credentials.writeError = CredentialError.keychain(-1, "write")
        viewModel.address = "a.example:1"
        viewModel.secret = "secret"

        XCTAssertFalse(viewModel.apply())
        XCTAssertEqual(applied, 0)
        XCTAssertEqual(viewModel.saveErrorKey, "settings_save_failed")
    }

    @MainActor
    func testHapticTogglePersistsAndNotifies() {
        var received: [Bool] = []
        let (viewModel, store, _, _) = makeViewModel(onHaptic: { received.append($0) })

        viewModel.hapticEnabled = false

        XCTAssertFalse(store.hapticEnabled)
        XCTAssertEqual(received, [false])
    }

    @MainActor
    func testCameraTogglesPersistWithoutDirtyState() {
        let (viewModel, store, _, _) = makeViewModel()

        viewModel.cameraDefaultFront = true
        viewModel.cameraAutoOnJoin = true

        XCTAssertTrue(store.cameraDefaultFront)
        XCTAssertTrue(store.cameraAutoOnJoin)
        XCTAssertFalse(viewModel.isDirty)
        XCTAssertFalse(viewModel.canSave)
    }

    @MainActor
    func testTLSAllowSelfSignedPersistsOnApply() throws {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()
        _ = try store.saveServerConfig(address: "a.example:1", secret: "secret", credentials: credentials)
        var applied = 0
        let viewModel = SettingsViewModel(
            store: store,
            credentials: credentials,
            probe: FakeProbe(),
            onApply: { applied += 1 }
        )

        XCTAssertTrue(viewModel.tlsAllowSelfSigned)
        viewModel.tlsAllowSelfSigned = false
        XCTAssertTrue(viewModel.isDirty)
        XCTAssertTrue(viewModel.apply())

        XCTAssertFalse(store.tlsAllowSelfSigned)
        XCTAssertFalse(viewModel.savedTLSAllowSelfSigned)
        XCTAssertEqual(applied, 1)
        XCTAssertFalse(viewModel.isDirty)
    }

    @MainActor
    func testProbeFactoryReceivesDraftTLSPolicy() async throws {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()
        var receivedPolicy: TLSPolicy?
        let viewModel = SettingsViewModel(
            store: store,
            credentials: credentials,
            probe: FakeProbe(),
            probeFactory: { policy in
                receivedPolicy = policy
                return FakeProbe()
            },
            onApply: {}
        )
        viewModel.address = "a.example:1"
        viewModel.secret = "secret"
        viewModel.tlsAllowSelfSigned = true

        viewModel.checkService()
        try await waitUntil { viewModel.probeResult != nil }

        guard let receivedPolicy else {
            XCTFail("probe factory not called")
            return
        }
        if case .allowSelfSigned = receivedPolicy {
            // expected
        } else {
            XCTFail("expected allowSelfSigned policy")
        }
    }

    @MainActor
    func testEditingClearsProbeResult() async throws {
        let (viewModel, _, _, _) = makeViewModel()
        viewModel.address = "a.example:1"
        viewModel.secret = "secret"
        viewModel.checkService()
        try await waitUntil { viewModel.probeResult != nil }

        viewModel.secret = "changed"
        XCTAssertNil(viewModel.probeResult)
    }

    func testLegalLinksUseExpectedHTTPSURLs() {
        XCTAssertEqual(LegalLinks.privacy.absoluteString, "https://livachat99.github.io/privacy/")
        XCTAssertEqual(LegalLinks.eula.absoluteString, "https://livachat99.github.io/terms/")
        XCTAssertEqual(LegalLinks.support.absoluteString, "https://livachat99.github.io/support/")
        XCTAssertEqual(LegalLinks.privacy.scheme, "https")
        XCTAssertEqual(LegalLinks.eula.scheme, "https")
        XCTAssertEqual(LegalLinks.support.scheme, "https")
    }

    @MainActor
    func testLicensesTextIsBundled() {
        let text = LicensesView.licenseText(bundle: Bundle(for: SettingsStore.self))
        XCTAssertTrue(text.contains("Apache License"))
        XCTAssertTrue(text.contains("MIT License"))
        XCTAssertTrue(text.contains("BSD-2-Clause License"))
    }

    @MainActor
    func testAvdetectorEnabledPersistsAndNotifies() {
        var changes: [Bool] = []
        let (viewModel, store, _, _) = makeViewModel(onAvdetector: { changes.append($0) })
        XCTAssertTrue(viewModel.avdetectorEnabled)

        viewModel.avdetectorEnabled = false
        XCTAssertFalse(store.avdetectorEnabled)
        XCTAssertEqual(changes, [false])
    }

    @MainActor
    private func waitUntil(
        timeout: TimeInterval = 1,
        condition: @MainActor () -> Bool
    ) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("condition not met")
    }
}
