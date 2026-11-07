import Combine
import Foundation
import SwiftUI

enum AgentJobResultOwner: String {
    case conversation
    case history
    case timeline
}

struct AgentJobResultWindowPayload: Equatable {
    let text: String
    let cacheKey: String
    let timestamp: Date
    let serverBaseURL: String
}

/// Single source of truth for the agent job result full-screen viewer. Each
/// screen publishes its current result through `agentJobResultWindow`; the
/// matching window-level host renders it outside the screen's content width
/// cap (History/Timeline use a 600pt column).
@MainActor
final class AgentJobResultWindowCenter: ObservableObject {
    struct Presentation: Identifiable {
        let id: UUID
        let owner: AgentJobResultOwner
        let payload: AgentJobResultWindowPayload
        let onDismiss: () -> Void
    }

    @Published private(set) var presentation: Presentation?

    func present(
        owner: AgentJobResultOwner,
        payload: AgentJobResultWindowPayload,
        onDismiss: @escaping () -> Void
    ) {
        if let existing = presentation, existing.owner == owner, existing.payload == payload {
            // Keep the view identity (and scroll position) while refreshing
            // the dismiss closure for a replaced view model.
            presentation = Presentation(
                id: existing.id,
                owner: owner,
                payload: payload,
                onDismiss: onDismiss
            )
            return
        }
        presentation = Presentation(
            id: UUID(),
            owner: owner,
            payload: payload,
            onDismiss: onDismiss
        )
    }

    func dismiss() {
        presentation = nil
    }

    func dismiss(owner: AgentJobResultOwner) {
        guard presentation?.owner == owner else { return }
        presentation = nil
    }

    func dismiss(id: UUID) {
        guard presentation?.id == id else { return }
        presentation = nil
    }
}

struct AgentJobResultWindowModifier: ViewModifier {
    @EnvironmentObject private var center: AgentJobResultWindowCenter
    let owner: AgentJobResultOwner
    let payload: AgentJobResultWindowPayload?
    let onDismiss: () -> Void

    func body(content: Content) -> some View {
        content
            .onAppear { sync(payload) }
            // Use the value handed to onChange: the action can run with the
            // previous body's captured modifier instance (stale payload).
            .onChange(of: payload) { newPayload in sync(newPayload) }
            .onDisappear { center.dismiss(owner: owner) }
    }

    private func sync(_ payload: AgentJobResultWindowPayload?) {
        if let payload {
            center.present(owner: owner, payload: payload, onDismiss: onDismiss)
        } else {
            center.dismiss(owner: owner)
        }
    }
}

extension View {
    func agentJobResultWindow(
        owner: AgentJobResultOwner,
        payload: AgentJobResultWindowPayload?,
        onDismiss: @escaping () -> Void
    ) -> some View {
        modifier(AgentJobResultWindowModifier(owner: owner, payload: payload, onDismiss: onDismiss))
    }
}
