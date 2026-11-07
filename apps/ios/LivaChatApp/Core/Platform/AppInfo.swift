import Foundation

enum AppInfo {
    static var shortVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0"
    }

    static var buildNumber: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1"
    }

    /// Matches the server's `^(ios|android)-\d+\.\d+\.\d+(\.\d+)?$` pattern.
    static var clientVersion: String {
        "ios-\(shortVersion).\(buildNumber)"
    }

    static var displayVersion: String {
        "\(shortVersion).\(buildNumber)"
    }
}
