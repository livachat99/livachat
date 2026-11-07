import XCTest
@testable import LivaChatApp

final class FakeCredentials: CredentialStoring {
    var values: [CredentialAccount: String] = [:]
    var deviceIDValue = UUID().uuidString
    var readError: Error?
    var writeError: Error?

    func read(_ account: CredentialAccount) throws -> String? {
        if let readError { throw readError }
        return values[account]
    }

    func write(_ value: String, for account: CredentialAccount) throws {
        if let writeError { throw writeError }
        values[account] = value
    }

    func delete(_ account: CredentialAccount) throws {
        values.removeValue(forKey: account)
    }

    func deviceID() throws -> String {
        deviceIDValue
    }
}

final class SettingsStoreTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suiteName: String!

    override func setUp() {
        super.setUp()
        suiteName = "dev.livachat.tests.\(UUID().uuidString)"
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
    func testSaveAndLoadServerConfig() throws {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()

        let saved = try store.saveServerConfig(
            address: " Example.com:8080 ",
            secret: " secret ",
            credentials: credentials
        )

        XCTAssertEqual(saved.address, "example.com:8080")
        XCTAssertEqual(saved.secret, "secret")
        XCTAssertEqual(saved.wireToken, WireToken.make(secret: "secret"))
        XCTAssertEqual(credentials.values[.serverSecret], "secret")
        XCTAssertEqual(credentials.values[.wireToken], saved.wireToken)

        let loaded = try store.loadSavedConfig(credentials: credentials)
        XCTAssertEqual(loaded?.address, "example.com:8080")
        XCTAssertEqual(loaded?.wireToken, saved.wireToken)
        XCTAssertEqual(loaded?.deviceID, credentials.deviceIDValue)
    }

    @MainActor
    func testLoadRepairsStaleWireTokenCache() throws {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()
        _ = try store.saveServerConfig(address: "a.example:1", secret: "secret", credentials: credentials)
        credentials.values[.wireToken] = "stale"

        let loaded = try store.loadSavedConfig(credentials: credentials)

        XCTAssertEqual(loaded?.wireToken, WireToken.make(secret: "secret"))
        XCTAssertEqual(credentials.values[.wireToken], WireToken.make(secret: "secret"))
    }

    @MainActor
    func testLoadRequiresAddressAndSecret() throws {
        let store = SettingsStore(defaults: defaults)
        let credentials = FakeCredentials()
        XCTAssertNil(try store.loadSavedConfig(credentials: credentials))

        try store.saveServerConfig(address: "a.example:1", secret: "s", credentials: credentials)
        credentials.values.removeValue(forKey: .serverSecret)
        XCTAssertNil(try store.loadSavedConfig(credentials: credentials))
    }

    @MainActor
    func testAvatarVisibleDefaultAndPersist() {
        let store = SettingsStore(defaults: defaults)
        XCTAssertTrue(store.avatarVisible)

        store.avatarVisible = false
        let reloaded = SettingsStore(defaults: defaults)
        XCTAssertFalse(reloaded.avatarVisible)
    }

    @MainActor
    func testAvdetectorEnabledDefaultAndPersist() {
        let store = SettingsStore(defaults: defaults)
        XCTAssertTrue(store.avdetectorEnabled)

        store.avdetectorEnabled = false
        let reloaded = SettingsStore(defaults: defaults)
        XCTAssertFalse(reloaded.avdetectorEnabled)
    }

    @MainActor
    func testValidation() {
        XCTAssertThrowsError(try SettingsStore.validate(address: "bad", secret: "s")) { error in
            XCTAssertEqual(error as? SettingsValidationError, .invalidAddress)
        }
        XCTAssertThrowsError(try SettingsStore.validate(address: "a.example:1", secret: "  ")) { error in
            XCTAssertEqual(error as? SettingsValidationError, .emptySecret)
        }
        XCTAssertNoThrow(try SettingsStore.validate(address: "a.example:1", secret: "s"))
    }

    @MainActor
    func testHapticAndOnboardingPersist() {
        let store = SettingsStore(defaults: defaults)
        XCTAssertTrue(store.hapticEnabled)
        XCTAssertTrue(store.tlsAllowSelfSigned)
        store.hapticEnabled = false
        store.tlsAllowSelfSigned = false
        store.markOnboardingCompleted()

        let reloaded = SettingsStore(defaults: defaults)
        XCTAssertFalse(reloaded.hapticEnabled)
        XCTAssertFalse(reloaded.tlsAllowSelfSigned)
        XCTAssertTrue(reloaded.onboardingCompleted)
    }

    @MainActor
    func testCameraPreferencesDefaultAndPersist() {
        let store = SettingsStore(defaults: defaults)
        XCTAssertFalse(store.cameraDefaultFront)
        XCTAssertFalse(store.cameraAutoOnJoin)

        store.cameraDefaultFront = true
        store.cameraAutoOnJoin = true

        let reloaded = SettingsStore(defaults: defaults)
        XCTAssertTrue(reloaded.cameraDefaultFront)
        XCTAssertTrue(reloaded.cameraAutoOnJoin)
    }
}
