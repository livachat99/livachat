import Foundation
import Security

enum CredentialAccount: String, CaseIterable {
    case serverSecret = "server_secret"
    case wireToken = "wire_token"
    case deviceID = "device_id"
}

enum CredentialError: LocalizedError, Equatable {
    case keychain(OSStatus, String)
    case invalidData(String)

    var errorDescription: String? {
        switch self {
        case let .keychain(status, operation):
            return "Keychain \(operation) failed (\(status))"
        case let .invalidData(account):
            return "Keychain value for \(account) is invalid"
        }
    }
}

protocol CredentialStoring: AnyObject {
    func read(_ account: CredentialAccount) throws -> String?
    func write(_ value: String, for account: CredentialAccount) throws
    func delete(_ account: CredentialAccount) throws
    func deviceID() throws -> String
}

final class KeychainCredentialStore: CredentialStoring, @unchecked Sendable {
    static let defaultService = "com.livachat.livachatapp.credentials"

    private let service: String
    private let lock = NSLock()
    private var fallbackDeviceID: String?

    init(service: String = KeychainCredentialStore.defaultService) {
        self.service = service
    }

    func read(_ account: CredentialAccount) throws -> String? {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account.rawValue,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        query[kSecAttrSynchronizable as String] = kCFBooleanFalse

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess else {
            throw CredentialError.keychain(status, "read \(account.rawValue)")
        }
        guard let data = item as? Data, let value = String(data: data, encoding: .utf8) else {
            throw CredentialError.invalidData(account.rawValue)
        }
        return value
    }

    func write(_ value: String, for account: CredentialAccount) throws {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account.rawValue
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]

        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }
        guard updateStatus == errSecItemNotFound else {
            throw CredentialError.keychain(updateStatus, "update \(account.rawValue)")
        }

        var addQuery = query
        attributes.forEach { addQuery[$0.key] = $0.value }
        addQuery[kSecAttrSynchronizable as String] = kCFBooleanFalse
        let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw CredentialError.keychain(addStatus, "add \(account.rawValue)")
        }
    }

    func delete(_ account: CredentialAccount) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account.rawValue
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw CredentialError.keychain(status, "delete \(account.rawValue)")
        }
    }

    func deviceID() throws -> String {
        if let stored = try? read(.deviceID), UUID(uuidString: stored) != nil {
            return stored
        }

        lock.lock()
        if let fallbackDeviceID {
            lock.unlock()
            return fallbackDeviceID
        }
        lock.unlock()

        let generated = UUID().uuidString
        do {
            try write(generated, for: .deviceID)
            return generated
        } catch {
            // Keychain may be unavailable on some managed devices. Keep a
            // process-lifetime ID so the connection still works; it will
            // change after relaunch.
            lock.lock()
            fallbackDeviceID = generated
            lock.unlock()
            return generated
        }
    }
}
