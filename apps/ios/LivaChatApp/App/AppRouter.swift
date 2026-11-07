import Combine
import Foundation

@MainActor
final class AppRouter: ObservableObject {
    enum Phase: Equatable {
        case splash
        case permission
        case conversation
    }

    @Published var phase: Phase = .splash
    @Published var showSettings = false
    @Published var showHistory = false
    @Published var showTimeline = false
}
