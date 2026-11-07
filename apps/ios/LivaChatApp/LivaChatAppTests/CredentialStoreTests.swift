import XCTest
@testable import LivaChatApp

final class CredentialStoreTests: XCTestCase {
    private var store: KeychainCredentialStore!
    private var service: String!

    override func setUp() {
        super.setUp()
        service = "com.livachat.livachatapp.tests.\(UUID().uuidString)"
        store = KeychainCredentialStore(service: service)
    }

    override func tearDown() {
        for account in CredentialAccount.allCases {
            try? store.delete(account)
        }
        store = nil
        service = nil
        super.tearDown()
    }

    func testWriteReadDelete() throws {
        XCTAssertNil(try store.read(.serverSecret))
        try store.write("secret-value", for: .serverSecret)
        XCTAssertEqual(try store.read(.serverSecret), "secret-value")

        try store.write("updated", for: .serverSecret)
        XCTAssertEqual(try store.read(.serverSecret), "updated")

        try store.delete(.serverSecret)
        XCTAssertNil(try store.read(.serverSecret))
    }

    func testDeviceIDIsPersistentUUID() throws {
        let first = try store.deviceID()
        XCTAssertNotNil(UUID(uuidString: first))
        XCTAssertEqual(try store.deviceID(), first)

        let secondStore = KeychainCredentialStore(service: service)
        XCTAssertEqual(try secondStore.deviceID(), first)
    }
}
