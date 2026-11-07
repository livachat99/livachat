import SwiftUI

struct HistoryFlowView: View {
    let service: HistoryService
    let serverBaseURL: String
    let onOpenLink: (URL) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @StateObject private var listViewModel: HistoryListViewModel
    @State private var selectedSessionID: String?

    init(
        service: HistoryService,
        serverBaseURL: String,
        onOpenLink: @escaping (URL) -> Void
    ) {
        self.service = service
        self.serverBaseURL = serverBaseURL
        self.onOpenLink = onOpenLink
        _listViewModel = StateObject(wrappedValue: HistoryListViewModel(service: service))
    }

    var body: some View {
        ZStack {
            if let selectedSessionID {
                HistoryDetailView(
                    service: service,
                    sessionID: selectedSessionID,
                    serverBaseURL: serverBaseURL,
                    onOpenLink: onOpenLink,
                    onBack: { self.selectedSessionID = nil }
                )
                .transition(reduceMotion ? .identity : .move(edge: .trailing))
            } else {
                HistoryListView(
                    viewModel: listViewModel,
                    onBack: { dismiss() },
                    onSelect: { selectedSessionID = $0 }
                )
                .transition(reduceMotion ? .identity : .opacity)
            }
        }
        .background(AppColor.background.ignoresSafeArea())
        .animation(
            reduceMotion ? nil : .easeInOut(duration: 0.2),
            value: selectedSessionID
        )
    }
}
