import Foundation

/// Receives protocol events and state mutations from the session manager.
internal protocol EventSink: AnyObject, Sendable {
    func emit(_ event: LivaChatEvent)
    func updateSessionState(_ state: SessionState)
}
