import SwiftUI

struct TimelineBubbleView: View {
    let row: HotSlotTextRow
    let maxBubbleWidth: CGFloat
    let serverBaseURL: String
    let onOpenLink: (URL) -> Void
    let onAgentJobClick: ((String) -> Void)?

    @Environment(\.layoutDirection) private var layoutDirection
    @State private var linkOpenCount = 0
    @State private var textNeedsMaxWidth = false
    @ScaledMetric(relativeTo: .body) private var bodyFontSize: CGFloat = 16

    private var isRTL: Bool { layoutDirection == .rightToLeft }
    private var isUser: Bool { row.side == .user }
    private var isAgent: Bool { row.side == .agent }
    private var clickableAsAgentJob: Bool { isAgent && onAgentJobClick != nil }

    private var contentMaxWidth: CGFloat {
        max(0, maxBubbleWidth - AppSpacing.m * 2)
    }

    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 0) }
            VStack(alignment: .leading, spacing: 0) {
                if isAgent { agentHeader }
                content
            }
            .padding(.horizontal, AppSpacing.m)
            .padding(.vertical, AppSpacing.s)
            .background(isUser ? AppColor.brandIndigo : AppColor.surface)
            .clipShape(BubbleShape(isUser: isUser, isRTL: isRTL))
            .overlay {
                if !isUser {
                    BubbleShape(isUser: false, isRTL: isRTL)
                        .stroke(AppColor.surfaceVariant, lineWidth: 1)
                }
            }
            // Keep the tap area to the visible bubble; the flexible max-width
            // frame below is wider than the content for short agent rows.
            .contentShape(Rectangle())
            .modifier(TimelineAgentTap(enabled: clickableAsAgentJob, action: scheduleAgentOpen))
            // Android caps the bubble including its padding; applying the
            // frame after padding keeps Hot Slot equal to conversation and
            // History detail. The flexible frame takes the max width, so user
            // bubbles need trailing alignment to stay flush right.
            .frame(maxWidth: maxBubbleWidth, alignment: isUser ? .trailing : .leading)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier(
                isUser ? "message_user" : (clickableAsAgentJob ? "message_agent_job" : "message_ai")
            )
            if !isUser { Spacer(minLength: 0) }
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder
    private var content: some View {
        if isAgent {
            let collapsed = AgentJobCollapse.collapsed(markdown: row.body, key: row.key)
            let markdown = collapsed?.prefix ?? row.body
            let hasTable = MarkdownTableFallback.containsTable(markdown)
            MarkdownMessageView(
                markdown: markdown,
                cacheKey: "\(row.key):\(collapsed == nil ? "full" : "prefix")",
                serverBaseURL: serverBaseURL,
                onOpenLink: handleLink
            )
            .frame(maxWidth: hasTable ? .infinity : nil, alignment: .leading)
            .background {
                if !hasTable {
                    GeometryReader { proxy in
                        Color.clear.preference(
                            key: MessageTextHeightKey.self,
                            value: proxy.size.height
                        )
                    }
                }
            }
            .onPreferenceChange(MessageTextHeightKey.self) { height in
                guard !hasTable else { return }
                textNeedsMaxWidth = height > bodyFontSize * 2.4
            }
            .frame(width: textNeedsMaxWidth ? contentMaxWidth : nil, alignment: .leading)
            if let collapsed {
                HStack(spacing: AppSpacing.xs) {
                    Text(L10n.plural("conv_job_remaining_chars", collapsed.remainingCharacters))
                        .mylifeFont(.button)
                        .foregroundColor(AppColor.brandIndigo)
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                        .font(.system(size: 14, weight: .regular))
                        .foregroundColor(AppColor.brandIndigo)
                        .accessibilityHidden(true)
                }
                .padding(.top, AppSpacing.xs)
                .accessibilityElement(children: .combine)
            }
        } else {
            Text(row.body)
                .mylifeFont(.bubbleBody)
                .foregroundColor(isUser ? AppColor.textPrimary : AppColor.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .background {
                    if !isUser {
                        GeometryReader { proxy in
                            Color.clear.preference(
                                key: MessageTextHeightKey.self,
                                value: proxy.size.height
                            )
                        }
                    }
                }
                .onPreferenceChange(MessageTextHeightKey.self) { height in
                    guard !isUser else { return }
                    textNeedsMaxWidth = height > bodyFontSize * 2.4
                }
                .frame(
                    width: !isUser && textNeedsMaxWidth ? contentMaxWidth : nil,
                    alignment: .leading
                )
        }
    }

    private var agentHeader: some View {
        HStack(spacing: AppSpacing.xs) {
            AgentIcon(size: 16, color: AppColor.textSecondary)
            Text(L10n.t("mylife_agent_task"))
                .mylifeFont(.label)
                .foregroundColor(AppColor.textSecondary)
            if clickableAsAgentJob {
                Image(systemName: "arrow.up.left.and.arrow.down.right")
                    .font(.system(size: 14, weight: .regular))
                    .foregroundColor(AppColor.textSecondary)
                    .accessibilityHidden(true)
            }
        }
        .padding(.bottom, AppSpacing.xs)
    }

    private func handleLink(_ url: URL) {
        linkOpenCount += 1
        onOpenLink(url)
    }

    private func scheduleAgentOpen() {
        guard let onAgentJobClick else { return }
        let observed = linkOpenCount
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 120_000_000)
            guard observed == linkOpenCount else { return }
            onAgentJobClick(row.key)
        }
    }
}

private struct TimelineAgentTap: ViewModifier {
    let enabled: Bool
    let action: () -> Void

    @ViewBuilder
    func body(content: Content) -> some View {
        if enabled {
            content.onTapGesture(perform: action)
        } else {
            content
        }
    }
}
