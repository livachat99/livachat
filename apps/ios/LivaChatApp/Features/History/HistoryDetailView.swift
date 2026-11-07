import SwiftUI

struct HistoryDetailView: View {
    @StateObject private var viewModel: HistoryDetailViewModel
    let serverBaseURL: String
    let onOpenLink: (URL) -> Void
    let onBack: () -> Void

    @State private var showDeleteConfirm = false
    @Environment(\.layoutDirection) private var layoutDirection
    @ScaledMetric(relativeTo: .body) private var messageBodyFontSize: CGFloat = 16

    init(
        service: HistoryService,
        sessionID: String,
        serverBaseURL: String,
        onOpenLink: @escaping (URL) -> Void,
        onBack: @escaping () -> Void
    ) {
        _viewModel = StateObject(
            wrappedValue: HistoryDetailViewModel(service: service, sessionID: sessionID)
        )
        self.serverBaseURL = serverBaseURL
        self.onOpenLink = onOpenLink
        self.onBack = onBack
    }

    var body: some View {
        ZStack(alignment: .top) {
            VStack(spacing: 0) {
                header
                GeometryReader { geometry in
                    if viewModel.isLoading {
                        ProgressView()
                            .progressViewStyle(CircularProgressViewStyle(tint: AppColor.brandIndigo))
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .accessibilityIdentifier("history_detail_loading")
                    } else {
                        ScrollView {
                            LazyVStack(spacing: AppSpacing.m) {
                                ForEach(viewModel.messages) { message in
                                    MessageBubbleView(
                                        message: message,
                                        maxBubbleWidth: AppLayout.messageBubbleMaxWidth(
                                            containerWidth: geometry.size.width
                                        ),
                                        serverBaseURL: serverBaseURL,
                                        layoutDirection: layoutDirection,
                                        bodyFontSize: messageBodyFontSize,
                                        onOpenLink: onOpenLink,
                                        onImageTap: {
                                            viewModel.openImageViewer(messageID: message.id)
                                        },
                                        onJobResultTap: {
                                            viewModel.openAgentJobResult(messageID: message.id)
                                        }
                                    )
                                    .id(message.id)
                                }
                            }
                            .padding(.horizontal, AppSpacing.m)
                            .padding(.vertical, AppSpacing.s)
                        }
                    }
                }
            }
            .allowsHitTesting(viewModel.agentJobResultMessageID == nil)
        }
        .background(AppColor.background.ignoresSafeArea())
        .agentJobResultWindow(
            owner: .history,
            payload: agentJobResultWindowPayload,
            onDismiss: viewModel.closeAgentJobResult
        )
        .task {
            await viewModel.loadIfNeeded()
        }
        .fullScreenCover(
            isPresented: Binding(
                get: { viewModel.imageViewerMessageID != nil },
                set: { if !$0 { viewModel.closeImageViewer() } }
            )
        ) {
            if let messageID = viewModel.imageViewerMessageID {
                ImageViewerOverlay(
                    images: viewModel.imageMessages,
                    initialMessageID: messageID,
                    onDismiss: viewModel.closeImageViewer
                )
            }
        }
        .alert(
            L10n.t("history_delete_title"),
            isPresented: $showDeleteConfirm
        ) {
            Button(L10n.t("common_cancel"), role: .cancel) {}
            Button(L10n.t("common_delete"), role: .destructive) {
                Task {
                    await viewModel.deleteSession()
                    onBack()
                }
            }
        } message: {
            Text(L10n.t("history_detail_delete_message"))
        }
    }

    private var agentJobResultWindowPayload: AgentJobResultWindowPayload? {
        guard let message = viewModel.agentJobResultMessage else { return nil }
        return AgentJobResultWindowPayload(
            text: message.text,
            cacheKey: "\(message.id):full",
            timestamp: message.timestamp,
            serverBaseURL: serverBaseURL
        )
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            Button(action: onBack) {
                Image(systemName: "chevron.backward")
                    .foregroundColor(AppColor.textPrimary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("common_back"))
            .accessibilityIdentifier("history_detail_back")

            Text(L10n.t("history_detail_title"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.title)
                .lineLimit(2)

            Spacer()

            Button {
                showDeleteConfirm = true
            } label: {
                Image(systemName: "trash")
                    .foregroundColor(AppColor.textPrimary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("common_delete"))
            .accessibilityIdentifier("history_detail_delete")
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 52)
    }
}
