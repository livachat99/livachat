import SwiftUI

struct AgentJobResultOverlay: View {
    let text: String
    let cacheKey: String
    let timestamp: Date
    let serverBaseURL: String
    let background: Color
    let onDismiss: () -> Void
    let onOpenLink: (URL) -> Void

    init(
        message: ChatMessage,
        serverBaseURL: String,
        onDismiss: @escaping () -> Void,
        onOpenLink: @escaping (URL) -> Void
    ) {
        self.init(
            text: message.text,
            cacheKey: "\(message.id):full",
            timestamp: message.timestamp,
            serverBaseURL: serverBaseURL,
            background: AppColor.surface,
            onDismiss: onDismiss,
            onOpenLink: onOpenLink
        )
    }

    init(
        text: String,
        cacheKey: String,
        timestamp: Date,
        serverBaseURL: String,
        background: Color = AppColor.surface,
        onDismiss: @escaping () -> Void,
        onOpenLink: @escaping (URL) -> Void
    ) {
        self.text = text
        self.cacheKey = cacheKey
        self.timestamp = timestamp
        self.serverBaseURL = serverBaseURL
        self.background = background
        self.onDismiss = onDismiss
        self.onOpenLink = onOpenLink
    }

    var body: some View {
        GeometryReader { geometry in
            let isLandscape = geometry.size.width > geometry.size.height
            ZStack(alignment: .topTrailing) {
                // Android keeps the status/navigation bar area on the app
                // background while the overlay content is the bubble Surface.
                AppColor.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    if !isLandscape {
                        header
                    }
                    ScrollView {
                        MarkdownMessageView(
                            markdown: text,
                            cacheKey: cacheKey,
                            serverBaseURL: serverBaseURL,
                            onOpenLink: onOpenLink
                        )
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, AppSpacing.m)
                            .padding(.top, isLandscape ? AppSpacing.xs : 0)
                            .padding(.bottom, AppSpacing.m)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                // Keep the bubble Surface inside the safe area; the status
                // bar/home indicator strips stay on the app background
                // (Android keeps those system bars on background_dark).
                .background(background, ignoresSafeAreaEdges: [])

                if isLandscape {
                    floatingCloseButton
                        .padding(AppSpacing.s)
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
        }
        .onAppear {
            AppOrientationController.shared.unlockForAgentJobFullScreen()
        }
        .onDisappear {
            AppOrientationController.shared.restorePortrait()
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("agent_job_result_overlay")
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            AgentIcon(size: 18, color: AppColor.brandIndigo)
            Text(timeText)
                .foregroundColor(AppColor.textSecondary)
                .appFont(.caption)
            Spacer(minLength: 0)
            closeButton
        }
        .padding(.leading, AppSpacing.m)
        .padding(.trailing, AppSpacing.xs)
        .frame(minHeight: 64)
    }

    private var closeButton: some View {
        Button(action: onDismiss) {
            closeIcon
                .frame(width: 48, height: 48)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(L10n.t("common_close"))
        .accessibilityIdentifier("agent_job_result_close")
    }

    private var floatingCloseButton: some View {
        Button(action: onDismiss) {
            closeIcon
                .frame(width: 48, height: 48)
                .background(Color.black.opacity(0.35))
                .clipShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(L10n.t("common_close"))
        .accessibilityIdentifier("agent_job_result_close")
    }

    private var closeIcon: some View {
        Image(systemName: "xmark")
            .font(.system(size: 18, weight: .regular))
            .foregroundColor(AppColor.textPrimary)
    }

    private var timeText: String {
        AgentTimeLabel.text(for: timestamp)
    }
}

enum AgentTimeLabel {
    static func text(
        for date: Date,
        locale: Locale = AppLocale.current,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        AgentJobFormatting.clockTime(date: date, locale: locale, timeZone: timeZone)
    }
}
