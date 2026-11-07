import Foundation

struct MylifeAPIError: LocalizedError, Equatable, Sendable {
    let statusCode: Int
    let code: String
    let message: String

    var errorDescription: String? {
        message.isEmpty ? "HTTP \(statusCode)" : message
    }
}

/// URLSession reports task cancellation as `URLError.cancelled`, not
/// `CancellationError`; callers must treat both as cancellation, never as a
/// user-facing failure.
func isMylifeCancellation(_ error: Error) -> Bool {
    if error is CancellationError { return true }
    if let urlError = error as? URLError, urlError.code == .cancelled { return true }
    return false
}
