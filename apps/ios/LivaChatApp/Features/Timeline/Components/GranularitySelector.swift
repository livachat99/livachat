import SwiftUI

struct GranularitySelector: View {
    let selected: Granularity
    let onSelect: (Granularity) -> Void

    private func title(_ granularity: Granularity) -> String {
        switch granularity {
        case .day: return L10n.t("mylife_granularity_day")
        case .week: return L10n.t("mylife_granularity_week")
        case .month: return L10n.t("mylife_granularity_month")
        case .year: return L10n.t("mylife_granularity_year")
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Granularity.allCases, id: \.self) { granularity in
                Button {
                    onSelect(granularity)
                } label: {
                    HStack(spacing: AppSpacing.xs) {
                        if selected == granularity {
                            Image(systemName: "checkmark")
                                .font(.system(size: 18, weight: .medium))
                                .foregroundColor(MylifePalette.gold)
                                .frame(width: 18, height: 18)
                                .accessibilityHidden(true)
                        } else {
                            Spacer().frame(width: 18, height: 18)
                        }
                        Text(title(granularity))
                            .mylifeFont(.label)
                            .foregroundColor(MylifePalette.dust)
                    }
                    .frame(maxWidth: .infinity, minHeight: 40)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(title(granularity))
                .accessibilityAddTraits(selected == granularity ? .isSelected : [])
                .accessibilityIdentifier("timeline_granularity_\(granularity.rawValue)")
            }
        }
        .background(MylifePalette.panel.opacity(0.6))
        .overlay {
            RoundedRectangle(cornerRadius: AppRadius.s)
                .stroke(MylifePalette.line, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.s))
        .padding(.horizontal, AppSpacing.m)
    }
}
