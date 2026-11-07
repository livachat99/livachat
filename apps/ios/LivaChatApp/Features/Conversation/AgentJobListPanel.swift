import LivaChatSDK
import SwiftUI

private struct AgentJobRowHeightKey: PreferenceKey {
    static let defaultValue: [String: CGFloat] = [:]

    static func reduce(value: inout [String: CGFloat], nextValue: () -> [String: CGFloat]) {
        value.merge(nextValue(), uniquingKeysWith: { $1 })
    }
}

private struct AgentJobHeaderHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private struct AgentJobConfirmHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

struct AgentJobListPanel: View {
    @ObservedObject var model: AgentJobStatusModel
    let maxHeight: CGFloat

    @AccessibilityFocusState private var headerFocused: Bool
    @State private var rowHeights: [String: CGFloat] = [:]
    @State private var headerHeight: CGFloat = 0

    private var sortedJobs: [AgentJob] {
        model.jobs.sorted {
            if $0.startedAtMs == $1.startedAtMs {
                return $0.jobID < $1.jobID
            }
            return $0.startedAtMs < $1.startedAtMs
        }
    }

    private var chromeHeight: CGFloat {
        headerHeight + AppSpacing.s + AppSpacing.m * 2
    }

    private var contentHeight: CGFloat {
        chromeHeight + rowHeights.values.reduce(0, +)
    }

    private var panelHeight: CGFloat {
        guard headerHeight > 0 else { return max(0, maxHeight) }
        return max(0, min(contentHeight, maxHeight))
    }

    private var listHeight: CGFloat {
        max(0, panelHeight - chromeHeight)
    }

    var body: some View {
        VStack(spacing: AppSpacing.s) {
            header
                .accessibilityFocused($headerFocused)
            ScrollView {
                TimelineView(.periodic(from: Date(), by: 1)) { context in
                    let nowMs = Int64(context.date.timeIntervalSince1970 * 1000)
                    VStack(spacing: 0) {
                        ForEach(sortedJobs, id: \.jobID) { job in
                            AgentJobRow(
                                job: job,
                                nowMs: nowMs,
                                cancelState: model.cancelState?.job.jobID == job.jobID
                                    ? model.cancelState
                                    : nil,
                                cancelInFlight: model.cancelState?.inFlight == true,
                                onRequestCancel: { model.requestCancel(job) },
                                onDismissCancel: { model.dismissCancel() },
                                onConfirmCancel: { model.confirmCancel() }
                            )
                            .background {
                                GeometryReader { proxy in
                                    Color.clear.preference(
                                        key: AgentJobRowHeightKey.self,
                                        value: [job.jobID: proxy.size.height]
                                    )
                                }
                            }
                        }
                    }
                }
            }
            .frame(height: listHeight)
            .accessibilityIdentifier("agent_job_list")
        }
        .padding(.horizontal, AppSpacing.m)
        .padding(.vertical, AppSpacing.m)
        .frame(height: panelHeight)
        .frame(maxWidth: .infinity)
        .background {
            RoundedRectangle(cornerRadius: AppRadius.l)
                .fill(AppColor.surface)
        }
        .overlay {
            RoundedRectangle(cornerRadius: AppRadius.l)
                .stroke(AppColor.gold, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
        .shadow(color: Color.black.opacity(0.3), radius: 12, x: 0, y: 6)
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
        .accessibilityIdentifier("agent_job_panel")
        .onPreferenceChange(AgentJobRowHeightKey.self) { rowHeights = $0 }
        .onPreferenceChange(AgentJobHeaderHeightKey.self) { headerHeight = $0 }
        .onChange(of: sortedJobs.map(\.jobID)) { ids in
            rowHeights = rowHeights.filter { ids.contains($0.key) }
        }
        .onAppear {
            Task { @MainActor in
                headerFocused = true
            }
        }
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            AgentIcon(size: 18, color: AppColor.brandIndigo)
            Text(L10n.plural("conv_agent_jobs_title", sortedJobs.count))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.label)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .background {
            GeometryReader { proxy in
                Color.clear.preference(
                    key: AgentJobHeaderHeightKey.self,
                    value: proxy.size.height
                )
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAction(named: Text(L10n.t("common_close"))) {
            model.dismissPanel()
        }
    }
}

private struct AgentJobRow: View {
    let job: AgentJob
    let nowMs: Int64
    let cancelState: AgentJobCancelState?
    let cancelInFlight: Bool
    let onRequestCancel: () -> Void
    let onDismissCancel: () -> Void
    let onConfirmCancel: () -> Void

    @State private var confirmCardHeight: CGFloat = 0

    private var isConfirming: Bool {
        cancelState != nil
    }

    private var started: String {
        let time = AgentJobFormatting.startedTime(milliseconds: job.startedAtMs)
        return L10n.format("conv_agent_job_started", time)
    }

    private var elapsed: String {
        AgentJobFormatting.elapsed(
            milliseconds: AgentJobFormatting.runningMilliseconds(for: job, nowMs: nowMs)
        )
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            baseContent
                .accessibilityHidden(isConfirming)
            if isConfirming {
                Color.clear
                    .frame(maxWidth: .infinity)
                    .frame(height: confirmCardHeight)
                    .accessibilityHidden(true)
            }
        }
        .overlay {
            if let cancelState {
                ZStack {
                    AppColor.background.opacity(0.82)
                        .contentShape(Rectangle())
                        .onTapGesture {}
                        .accessibilityHidden(true)
                    confirmCard(cancelState)
                        .padding(.horizontal, AppSpacing.xs)
                        .background {
                            GeometryReader { proxy in
                                Color.clear.preference(
                                    key: AgentJobConfirmHeightKey.self,
                                    value: proxy.size.height
                                )
                            }
                        }
                }
            }
        }
        .onPreferenceChange(AgentJobConfirmHeightKey.self) { height in
            confirmCardHeight = height
        }
        .onChange(of: cancelState) { state in
            if state == nil {
                confirmCardHeight = 0
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("agent_job_row_\(job.jobID)")
    }

    private var baseContent: some View {
        ZStack(alignment: .bottomTrailing) {
            VStack(alignment: .leading, spacing: 0) {
                Text(job.summary)
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.body)
                    .lineLimit(3)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                HStack(spacing: AppSpacing.s) {
                    Text(started)
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.caption)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Text(elapsed)
                        .foregroundColor(AppColor.success)
                        .appFont(.label)
                        .lineLimit(1)
                        .fixedSize()
                }
                .padding(.top, AppSpacing.xs)
            }
            .padding(.trailing, 44)
            .accessibilityElement(children: .combine)
            .accessibilityLabel(LocalizedList.join([job.summary, started, elapsed]))
            .accessibilityHidden(isConfirming)

            Button(action: onRequestCancel) {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .regular))
                    .foregroundColor(AppColor.textSecondary)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(cancelInFlight)
            .accessibilityLabel(L10n.t("conv_agent_job_cancel_action"))
            .accessibilityHidden(isConfirming)
            .accessibilityIdentifier("agent_job_cancel_action_\(job.jobID)")
        }
        .padding(.vertical, AppSpacing.s)
    }

    private func confirmCard(_ state: AgentJobCancelState) -> some View {
        AgentJobCancelConfirmView(
            state: state,
            onNo: onDismissCancel,
            onYes: onConfirmCancel
        )
    }
}

private struct AgentJobCancelConfirmView: View {
    let state: AgentJobCancelState
    let onNo: () -> Void
    let onYes: () -> Void

    var body: some View {
        VStack(spacing: AppSpacing.xs) {
            Text(L10n.t("conv_agent_job_cancel_question"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.body)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .frame(maxWidth: .infinity)
            if state.failed {
                Text(L10n.t("conv_agent_job_cancel_failed"))
                    .foregroundColor(AppColor.error)
                    .appFont(.caption)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity)
                    .accessibilityIdentifier("agent_job_cancel_error")
            }
            if state.inFlight {
                HStack(spacing: AppSpacing.xs) {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .tint(AppColor.brandIndigo)
                        .scaleEffect(0.7)
                        .frame(width: 14, height: 14)
                        .accessibilityHidden(true)
                    Text(L10n.t("conv_agent_job_cancel_in_progress"))
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.label)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(L10n.t("conv_agent_job_cancel_in_progress"))
                .accessibilityIdentifier("agent_job_cancel_spinner")
            } else {
                HStack(spacing: AppSpacing.s) {
                    actionButton(
                        L10n.t("conv_agent_job_cancel_no"),
                        identifier: "agent_job_cancel_no",
                        action: onNo
                    )
                    actionButton(
                        L10n.t("conv_agent_job_cancel_yes"),
                        identifier: "agent_job_cancel_yes",
                        action: onYes
                    )
                }
            }
        }
        .padding(.horizontal, AppSpacing.s)
        .padding(.vertical, AppSpacing.xs)
        .frame(maxWidth: .infinity)
        .background {
            RoundedRectangle(cornerRadius: AppRadius.m)
                .fill(AppColor.surface)
        }
        .overlay {
            RoundedRectangle(cornerRadius: AppRadius.m)
                .stroke(AppColor.gold, lineWidth: 1)
        }
        .shadow(color: Color.black.opacity(0.25), radius: 8, x: 0, y: 4)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("agent_job_cancel_confirm")
    }

    private func actionButton(
        _ title: String,
        identifier: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .foregroundColor(AppColor.brandIndigo)
                .appFont(.label)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }
}
