import SwiftUI

struct EventListView: View {
    @StateObject private var viewModel: HotSlotViewModel
    let serverBaseURL: String
    let onBack: () -> Void
    let onOpenLink: (URL) -> Void

    init(
        store: MylifeServicing,
        tsMs: Int64,
        pending: Bool,
        serverBaseURL: String,
        onBack: @escaping () -> Void,
        onOpenLink: @escaping (URL) -> Void
    ) {
        let window: Int64 = 10 * 60_000
        _viewModel = StateObject(wrappedValue: HotSlotViewModel(
            store: store,
            startMs: tsMs - window,
            endMs: tsMs + window,
            titleTsMs: tsMs,
            pending: pending,
            anchorTs: tsMs
        ))
        self.serverBaseURL = serverBaseURL
        self.onBack = onBack
        self.onOpenLink = onOpenLink
    }

    var body: some View {
        HotSlotScreen(
            viewModel: viewModel,
            serverBaseURL: serverBaseURL,
            onBack: onBack,
            onOpenLink: onOpenLink
        )
    }
}
