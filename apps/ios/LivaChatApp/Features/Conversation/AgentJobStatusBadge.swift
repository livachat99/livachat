import SwiftUI

struct AgentJobStatusBadge: View {
    let count: Int
    let action: () -> Void

    var body: some View {
        if count > 0 {
            Button(action: action) {
                HStack(spacing: AppSpacing.xs) {
                    AgentIcon(size: 14, color: AppColor.textPrimary)
                    Text(AgentJobFormatting.badgeCount(count))
                        .foregroundColor(AppColor.textPrimary)
                        .appFont(.caption)
                        .lineLimit(1)
                }
                .padding(.horizontal, AppSpacing.s)
                .frame(minHeight: 28)
                .background(
                    Capsule()
                        .fill(AppColor.brandIndigo.opacity(0.7))
                )
                .overlay {
                    Capsule()
                        .stroke(AppColor.surface, lineWidth: 1.5)
                }
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L10n.plural("conv_agent_jobs_running", count))
            .accessibilityValue(AgentJobFormatting.badgeCount(count))
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("agent_job_badge")
            .transition(.scale.combined(with: .opacity))
        }
    }
}
