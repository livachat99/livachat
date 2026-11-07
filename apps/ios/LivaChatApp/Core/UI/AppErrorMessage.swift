import Foundation

/// A user-visible error title key plus technical detail for logs only.
struct AppErrorMessage: Equatable, Sendable {
    let key: String
    let logDetail: String

    init(_ key: String, logDetail: String = "") {
        self.key = key
        self.logDetail = logDetail
    }
}
