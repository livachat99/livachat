import SwiftUI

struct HotSlotView: View {
    @StateObject private var viewModel: HotSlotViewModel
    let serverBaseURL: String
    let onBack: () -> Void
    let onOpenLink: (URL) -> Void

    init(
        store: MylifeServicing,
        startMs: Int64,
        endMs: Int64,
        pending: Bool,
        serverBaseURL: String,
        onBack: @escaping () -> Void,
        onOpenLink: @escaping (URL) -> Void
    ) {
        _viewModel = StateObject(wrappedValue: HotSlotViewModel(
            store: store,
            startMs: startMs,
            endMs: endMs,
            titleTsMs: startMs,
            pending: pending
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
