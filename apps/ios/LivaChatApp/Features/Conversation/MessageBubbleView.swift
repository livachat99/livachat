import LivaChatSDK
import SwiftUI

struct MessageBubbleView: View, Equatable {
    let message: ChatMessage
    let maxBubbleWidth: CGFloat
    let serverBaseURL: String
    let layoutDirection: LayoutDirection
    let bodyFontSize: CGFloat
    let onOpenLink: (URL) -> Void
    let onImageTap: (() -> Void)?
    let onJobResultTap: (() -> Void)?

    @State private var linkOpenCount = 0
    @State private var textNeedsMaxWidth = false

    private var isRTL: Bool { layoutDirection == .rightToLeft }

    private var contentMaxWidth: CGFloat {
        max(0, maxBubbleWidth - AppSpacing.m * 2)
    }

    init(
        message: ChatMessage,
        maxBubbleWidth: CGFloat,
        serverBaseURL: String,
        layoutDirection: LayoutDirection,
        bodyFontSize: CGFloat,
        onOpenLink: @escaping (URL) -> Void,
        onImageTap: (() -> Void)? = nil,
        onJobResultTap: (() -> Void)? = nil
    ) {
        self.message = message
        self.maxBubbleWidth = maxBubbleWidth
        self.serverBaseURL = serverBaseURL
        self.layoutDirection = layoutDirection
        self.bodyFontSize = bodyFontSize
        self.onOpenLink = onOpenLink
        self.onImageTap = onImageTap
        self.onJobResultTap = onJobResultTap
    }

    var body: some View {
        let isUser = message.role == .user
        let isJobResult = !isUser && message.isAgentJobResult && onJobResultTap != nil
        let collapsed = isJobResult
            ? AgentJobCollapse.collapsed(markdown: message.text, key: message.id)
            : nil
        let modeSuffix = MessageModeSuffix.key(mode: message.mode, role: message.role)
            .map(L10n.t)
        let displayText = MessageModeSuffix.appended(
            collapsed?.prefix ?? message.text,
            suffix: modeSuffix
        )
        let hasTable = !isUser && MarkdownTableFallback.containsTable(displayText)

        HStack {
            if isUser { Spacer(minLength: 0) }
            VStack(
                alignment: .leading,
                spacing: message.image != nil && !message.text.isEmpty ? AppSpacing.s : 0
            ) {
                if !isUser, message.source == .agent {
                    agentHeader(message, showsJobResultHint: isJobResult)
                }
                if let image = message.image, !isUser {
                    AIImageView(
                        image: image,
                        cacheKey: message.id,
                        onTap: onImageTap
                    )
                    .frame(maxWidth: .infinity)
                }
                if !message.text.isEmpty {
                    if isUser {
                        Text(displayText)
                            .foregroundColor(AppColor.textPrimary)
                            .appFont(.body)
                    } else {
                        MarkdownMessageView(
                            markdown: displayText,
                            cacheKey: "\(message.id):\(collapsed == nil ? "full" : "prefix")",
                            serverBaseURL: serverBaseURL,
                            onOpenLink: handleLink
                        )
                        .frame(maxWidth: hasTable ? .infinity : nil, alignment: .leading)
                        .background {
                            if message.image == nil, !hasTable {
                                GeometryReader { proxy in
                                    Color.clear.preference(
                                        key: MessageTextHeightKey.self,
                                        value: proxy.size.height
                                    )
                                }
                            }
                        }
                        .onPreferenceChange(MessageTextHeightKey.self) { height in
                            guard message.image == nil, !hasTable else { return }
                            // A wrapped message uses the full shared max width so
                            // every multi-line AI bubble has the same right edge;
                            // a one-line message keeps hugging its text.
                            textNeedsMaxWidth = height > bodyFontSize * 2.4
                        }
                        .frame(width: textNeedsMaxWidth ? contentMaxWidth : nil, alignment: .leading)
                        if let collapsed {
                            collapseFooter(remaining: collapsed.remainingCharacters)
                                .padding(.top, AppSpacing.xs)
                        }
                    }
                }
            }
            .frame(
                maxWidth: (message.image != nil || hasTable) ? .infinity : nil,
                alignment: .leading
            )
            .padding(.horizontal, AppSpacing.m)
            .padding(.vertical, AppSpacing.s + 2)
            .background(isUser ? AppColor.brandIndigo : AppColor.surface)
            .clipShape(BubbleShape(isUser: isUser, isRTL: isRTL))
            .overlay {
                if !isUser {
                    BubbleShape(isUser: false, isRTL: isRTL)
                        .stroke(AppColor.surfaceVariant, lineWidth: 1)
                }
            }
            .modifier(
                AgentJobBubbleInteraction(
                    enabled: isJobResult,
                    fullResultLabel: L10n.t("common_view_full_result"),
                    action: { scheduleOpenJobResult() }
                )
            )
            .accessibilityElement(children: isUser ? .combine : .contain)
            .modifier(MessageAccessibility(role: message.role, text: isUser ? displayText : ""))
            .accessibilityIdentifier(
                isUser ? "message_user" : (isJobResult ? "message_agent_job" : "message_ai")
            )
            // Android widthIn(max = 0.75 * screenWidth) caps the bubble including
            // its padding; applying the frame after padding keeps that parity.
            // The flexible frame takes the max width, so user bubbles need
            // trailing alignment to stay flush with the screen edge.
            .frame(maxWidth: maxBubbleWidth, alignment: isUser ? .trailing : .leading)
            if !isUser { Spacer(minLength: 0) }
        }
    }

    private func agentHeader(_ message: ChatMessage, showsJobResultHint: Bool) -> some View {
        HStack(spacing: AppSpacing.xs) {
            AgentIcon(size: 16, color: AppColor.textSecondary)
            Text(
                L10n.titleWithDetail(
                    L10n.t("conv_agent_header"),
                    AgentTimeLabel.text(for: message.timestamp)
                )
            )
                .foregroundColor(AppColor.textSecondary)
                .appFont(.caption)
            if showsJobResultHint {
                Image(systemName: "arrow.up.left.and.arrow.down.right")
                    .font(.system(size: 14, weight: .regular))
                    .foregroundColor(AppColor.textSecondary)
                    .padding(.leading, AppSpacing.xs)
                    .accessibilityLabel(L10n.t("common_view_full_result"))
            }
        }
        .padding(.bottom, AppSpacing.xs)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("message_agent_header")
    }

    private func collapseFooter(remaining: Int) -> some View {
        HStack(spacing: AppSpacing.xs) {
            Text(L10n.plural("conv_job_remaining_chars", remaining))
                .foregroundColor(AppColor.brandIndigo)
                .appFont(.label)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("message_agent_collapsed")
    }

    private func handleLink(_ url: URL) {
        linkOpenCount += 1
        onOpenLink(url)
    }

    private func scheduleOpenJobResult() {
        guard let onJobResultTap else { return }
        let observedCount = linkOpenCount
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 120_000_000)
            guard observedCount == linkOpenCount else { return }
            onJobResultTap()
        }
    }
}

extension MessageBubbleView {
    /// Ignores the action closures on purpose: they are recreated by the
    /// parent on every body evaluation, but for an unchanged message they
    /// resolve to the same behavior. Comparing the data (not the closures)
    /// lets SwiftUI skip settled rows while a streaming token updates the
    /// conversation, instead of re-parsing every visible Markdown bubble.
    nonisolated static func == (lhs: MessageBubbleView, rhs: MessageBubbleView) -> Bool {
        lhs.maxBubbleWidth == rhs.maxBubbleWidth
            && lhs.serverBaseURL == rhs.serverBaseURL
            && lhs.layoutDirection == rhs.layoutDirection
            && lhs.bodyFontSize == rhs.bodyFontSize
            && lhs.message.id == rhs.message.id
            && lhs.message.role == rhs.message.role
            && lhs.message.text == rhs.message.text
            && lhs.message.isFinal == rhs.message.isFinal
            && lhs.message.timestamp == rhs.message.timestamp
            && lhs.message.source == rhs.message.source
            && lhs.message.mode == rhs.message.mode
            && lhs.message.image?.mime == rhs.message.image?.mime
            && lhs.message.image?.width == rhs.message.image?.width
            && lhs.message.image?.height == rhs.message.image?.height
            && lhs.message.image?.orientation == rhs.message.image?.orientation
            && lhs.message.image?.cameraFacing == rhs.message.image?.cameraFacing
            && lhs.message.image?.data.count == rhs.message.image?.data.count
    }
}

struct MessageTextHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private struct AgentJobBubbleInteraction: ViewModifier {
    let enabled: Bool
    let fullResultLabel: String
    let action: () -> Void

    @ViewBuilder
    func body(content: Content) -> some View {
        if enabled {
            content
                .contentShape(Rectangle())
                .onTapGesture(perform: action)
                .accessibilityAction(named: Text(fullResultLabel), action)
        } else {
            content
        }
    }
}

private struct MessageAccessibility: ViewModifier {
    let role: ChatRole
    let text: String

    @ViewBuilder
    func body(content: Content) -> some View {
        if text.isEmpty {
            content
        } else {
            content.accessibilityLabel(
                L10n.titleWithDetail(
                    L10n.t(role == .user ? "conv_speaker_user" : "conv_speaker_ai"),
                    text
                )
            )
        }
    }
}

enum MessageModeSuffix {
    static func key(mode: ChatMessageMode?, role: ChatRole) -> String? {
        switch mode {
        case .transcribe:
            return "conv_msg_suffix_transcript"
        case .translate:
            return role == .user ? "conv_msg_suffix_original" : "conv_msg_suffix_translation"
        case nil:
            return nil
        }
    }

    static func appended(_ text: String, suffix: String?) -> String {
        guard !text.isEmpty, let suffix else { return text }
        return "\(text) \(suffix)"
    }
}

/// Chat bubble with the tail on the bottom-trailing corner. SwiftUI already
/// mirrors shapes in right-to-left layouts, so the path must stay authored for
/// LTR; adding an `isRTL` flip here would cancel that mirroring out.
struct BubbleShape: Shape {
    let isUser: Bool
    let isRTL: Bool

    /// The tail marks the outer screen edge. iOS does not mirror custom
    /// `Shape` paths for `\.layoutDirection` (verified in the simulator), so
    /// the RTL corner swap is explicit here. Do not "simplify" this away.
    static func tailAtBottomRight(isUser: Bool, isRTL: Bool) -> Bool {
        isUser ? !isRTL : isRTL
    }

    func path(in rect: CGRect) -> Path {
        let large: CGFloat = AppRadius.l
        let tail: CGFloat = AppRadius.s
        let topLeft = large
        let topRight = large
        let tailOnRight = Self.tailAtBottomRight(isUser: isUser, isRTL: isRTL)
        let bottomRight = tailOnRight ? tail : large
        let bottomLeft = tailOnRight ? large : tail

        var path = Path()
        path.move(to: CGPoint(x: rect.minX + topLeft, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - topRight, y: rect.minY))
        path.addArc(
            center: CGPoint(x: rect.maxX - topRight, y: rect.minY + topRight),
            radius: topRight,
            startAngle: .degrees(-90),
            endAngle: .degrees(0),
            clockwise: false
        )
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - bottomRight))
        path.addArc(
            center: CGPoint(x: rect.maxX - bottomRight, y: rect.maxY - bottomRight),
            radius: bottomRight,
            startAngle: .degrees(0),
            endAngle: .degrees(90),
            clockwise: false
        )
        path.addLine(to: CGPoint(x: rect.minX + bottomLeft, y: rect.maxY))
        path.addArc(
            center: CGPoint(x: rect.minX + bottomLeft, y: rect.maxY - bottomLeft),
            radius: bottomLeft,
            startAngle: .degrees(90),
            endAngle: .degrees(180),
            clockwise: false
        )
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + topLeft))
        path.addArc(
            center: CGPoint(x: rect.minX + topLeft, y: rect.minY + topLeft),
            radius: topLeft,
            startAngle: .degrees(180),
            endAngle: .degrees(270),
            clockwise: false
        )
        path.closeSubpath()
        return path
    }
}
