import SwiftUI

private struct AgentRowPayload {
    let key: String
    let text: String
    let tsMs: Int64
}

struct HotSlotScreen: View {
    @ObservedObject var viewModel: HotSlotViewModel
    let serverBaseURL: String
    let onBack: () -> Void
    let onOpenLink: (URL) -> Void

    @State private var located = false
    @State private var highlightKey: String?
    @State private var lastAgentPayload: AgentRowPayload?

    private var locale: Locale { MylifeDateLabels.effectiveLocale }

    private var rows: [HotSlotRow] {
        guard case let .data(events, _, _) = viewModel.state else { return [] }
        return buildHotSlotRows(
            events: events,
            locale: locale,
            transcriptSuffix: L10n.t("conv_msg_suffix_transcript"),
            originalSuffix: L10n.t("conv_msg_suffix_original"),
            translationSuffix: L10n.t("conv_msg_suffix_translation")
        )
    }

    var body: some View {
        ZStack(alignment: .top) {
            MylifePalette.abyss.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                content
            }
            .allowsHitTesting(viewModel.viewerFrame == nil && viewModel.viewerAgentJobKey == nil)

            if let frame = viewModel.viewerFrame {
                FrameViewerOverlay(
                    frame: frame,
                    saveTip: viewModel.imageSaveTip,
                    saving: viewModel.saving,
                    onSaveClick: viewModel.saveViewerFrame,
                    onDismiss: viewModel.dismissViewer
                )
                .zIndex(2)
            }
        }
        .preferredColorScheme(.dark)
        .agentJobResultWindow(
            owner: .timeline,
            payload: agentJobResultWindowPayload,
            onDismiss: viewModel.dismissAgentJob
        )
        .onAppear {
            if let anchor = viewModel.anchorTs {
                viewModel.ensureLoadedUntil(anchor)
            }
        }
        .onChange(of: viewModel.state) { _ in
            if let anchor = viewModel.anchorTs {
                viewModel.ensureLoadedUntil(anchor)
            }
        }
        .onChange(of: viewModel.viewerAgentJobKey) { key in
            guard let key else {
                lastAgentPayload = nil
                return
            }
            if let payload = resolveAgentPayload(key) {
                lastAgentPayload = payload
            } else if lastAgentPayload?.key != key {
                viewModel.dismissAgentJob()
            }
        }
    }

    private var agentJobResultWindowPayload: AgentJobResultWindowPayload? {
        guard let key = viewModel.viewerAgentJobKey,
              let payload = agentPayload(for: key) else {
            return nil
        }
        return AgentJobResultWindowPayload(
            text: payload.text,
            cacheKey: "timeline-\(payload.key):full",
            timestamp: Date(timeIntervalSince1970: Double(payload.tsMs) / 1000),
            serverBaseURL: serverBaseURL
        )
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            Button(action: onBack) {
                Image(systemName: "chevron.backward")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundColor(MylifePalette.dust)
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L10n.t("common_back"))
            .accessibilityIdentifier("timeline_hotslot_back")

            Text(formatHotSlotTitle(startMs: viewModel.titleTsMs, locale: locale))
                .mylifeFont(.subpageTitle)
                .foregroundColor(MylifePalette.gold)
                .lineLimit(2)
                .truncationMode(.tail)
                .accessibilityIdentifier("timeline_hotslot_title")

            Spacer()

            if viewModel.pending {
                Text(L10n.t("mylife_analyzing"))
                    .mylifeFont(.label)
                    .foregroundColor(MylifePalette.gold.opacity(0.8))
                    .padding(.trailing, AppSpacing.s)
            }
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 52)
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {
        case .loading:
            MylifeLoadingHint(text: L10n.t("mylife_loading"))
        case .empty:
            if viewModel.pending {
                pendingEmpty
            } else {
                MylifeEmptyState(title: L10n.t("mylife_hotslot_empty"), icon: "info.circle")
            }
        case let .error(message):
            MylifeErrorHint(message: message) { viewModel.retry() }
        case .data:
            GeometryReader { geometry in
                rowList(
                    maxBubbleWidth: AppLayout.messageBubbleMaxWidth(
                        containerWidth: geometry.size.width
                    )
                )
            }
        }
    }

    private var pendingEmpty: some View {
        VStack(spacing: AppSpacing.m) {
            Text(L10n.t("mylife_analyzing_content"))
                .mylifeFont(.body)
                .foregroundColor(MylifePalette.dust)
            Button {
                viewModel.retry()
            } label: {
                Text(L10n.t("mylife_refresh"))
                    .mylifeFont(.button)
                    .foregroundColor(MylifePalette.gold)
                    .padding(.horizontal, AppSpacing.m)
                    .frame(minHeight: 44)
                    .overlay {
                        RoundedRectangle(cornerRadius: AppRadius.l)
                            .stroke(MylifePalette.gold, lineWidth: 1)
                    }
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func rowList(maxBubbleWidth: CGFloat) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        rowView(row, maxBubbleWidth: maxBubbleWidth)
                            .overlay {
                                if highlightKey == row.key {
                                    RoundedRectangle(cornerRadius: AppRadius.s)
                                        .stroke(MylifePalette.gold, lineWidth: 2)
                                }
                            }
                            .onAppear {
                                if index >= rows.count - 4 {
                                    viewModel.loadMore()
                                }
                            }
                    }
                }
                .padding(.horizontal, AppSpacing.m)
                .padding(.vertical, AppSpacing.xs)
                .padding(.bottom, AppSpacing.xl)
            }
            .onChange(of: rows) { _ in
                locateIfNeeded(rows: rows, proxy: proxy)
            }
            .onAppear {
                locateIfNeeded(rows: rows, proxy: proxy)
            }
        }
    }

    @ViewBuilder
    private func rowView(_ row: HotSlotRow, maxBubbleWidth: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if let label = row.timeLabel {
                Text(label)
                    .mylifeFont(.label)
                    .foregroundColor(MylifePalette.dust)
                    .frame(maxWidth: .infinity)
                    .padding(.top, AppSpacing.s)
                    .padding(.bottom, AppSpacing.xs)
            }
            switch row {
            case let .text(key, timeLabel, tsMs, side, body):
                TimelineBubbleView(
                    row: HotSlotTextRow(
                        key: key,
                        timeLabel: timeLabel,
                        tsMs: tsMs,
                        side: side,
                        body: body
                    ),
                    maxBubbleWidth: maxBubbleWidth,
                    serverBaseURL: serverBaseURL,
                    onOpenLink: onOpenLink,
                    onAgentJobClick: { key in
                        viewModel.onAgentJobClick(rowKey: key)
                    }
                )
            case let .frames(_, _, frames):
                FrameRowView(frames: frames, onFrameClick: viewModel.onFrameClick)
            }
        }
        .id(row.id)
    }

    private func locateIfNeeded(rows: [HotSlotRow], proxy: ScrollViewProxy) {
        guard !located, let anchor = viewModel.anchorTs, !rows.isEmpty else { return }
        guard let index = anchorRowIndex(rows, anchorTs: anchor) else { return }
        proxy.scrollTo(rows[index].id, anchor: .center)
        located = true
        let key = rows[index].key
        highlightKey = key
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if highlightKey == key { highlightKey = nil }
        }
    }

    @ViewBuilder
    private func agentPayload(for key: String) -> AgentRowPayload? {
        resolveAgentPayload(key) ?? (lastAgentPayload?.key == key ? lastAgentPayload : nil)
    }

    private func resolveAgentPayload(_ key: String) -> AgentRowPayload? {
        guard let row = rows.first(where: { $0.key == key }),
              case let .text(_, _, tsMs, side, body) = row,
              side == .agent else { return nil }
        return AgentRowPayload(key: key, text: body, tsMs: tsMs)
    }
}
