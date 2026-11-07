import SwiftUI

struct HistorySessionRow: View {
    let session: HistorySession
    let onSelect: () -> Void
    let onDelete: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.layoutDirection) private var layoutDirection
    @State private var offset: CGFloat = 0

    private var isRTL: Bool { layoutDirection == .rightToLeft }

    private var title: String {
        session.firstUserMessage ?? session.summary ?? L10n.t("history_untitled")
    }

    private var subtitle: String {
        HistoryDateFormatting.subtitle(for: session)
    }

    var body: some View {
        ZStack(alignment: .trailing) {
            RoundedRectangle(cornerRadius: AppRadius.m)
                .fill(AppColor.error)
            Image(systemName: "trash")
                .foregroundColor(.white)
                .padding(.trailing, AppSpacing.l)
                .accessibilityHidden(true)
            cardContent
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("history_row")
        .accessibilityAction(named: Text(L10n.t("common_delete")), onDelete)
    }

    private var cardContent: some View {
        HStack(spacing: AppSpacing.s) {
            selectArea
            deleteButton
        }
        .padding(AppSpacing.m)
        .background(AppColor.surface)
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .overlay {
            RoundedRectangle(cornerRadius: AppRadius.m)
                .stroke(AppColor.surfaceVariant, lineWidth: 1)
        }
        .offset(x: offset)
        .simultaneousGesture(swipeGesture)
    }

    private var selectArea: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text(title)
                .foregroundColor(AppColor.textPrimary)
                .appFont(.body)
                .lineLimit(2)
                .truncationMode(.tail)
            Text(subtitle)
                .foregroundColor(AppColor.textTertiary)
                .appFont(.caption)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(LocalizedList.join([title, subtitle]))
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { onSelect() }
        .accessibilityIdentifier("history_row_select")
    }

    private var deleteButton: some View {
        Button(action: onDelete) {
            Image(systemName: "trash")
                .foregroundColor(AppColor.textTertiary)
                .frame(width: 44, height: 44)
        }
        .accessibilityLabel(L10n.t("common_delete"))
        .accessibilityIdentifier("history_delete")
    }

    private var swipeGesture: some Gesture {
        DragGesture(minimumDistance: 12)
            .onChanged { value in
                if let revealed = HistorySwipeMath.revealOffset(
                    value.translation,
                    isRTL: isRTL
                ) {
                    offset = revealed
                }
            }
            .onEnded { value in
                let shouldDelete = HistorySwipeMath.shouldDelete(
                    value.translation,
                    isRTL: isRTL
                )
                withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
                    offset = 0
                }
                if shouldDelete {
                    onDelete()
                }
            }
    }
}

struct HistoryEmptyState: View {
    var body: some View {
        VStack(spacing: AppSpacing.m) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 64, weight: .regular))
                .foregroundColor(AppColor.textSecondary)
                .accessibilityHidden(true)
            Text(L10n.t("history_empty_title"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.title)
                .multilineTextAlignment(.center)
            Text(L10n.t("history_empty_subtitle"))
                .foregroundColor(AppColor.textSecondary)
                .appFont(.body)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(AppSpacing.xl)
        .accessibilityIdentifier("history_empty")
    }
}

struct HistoryLoadingSkeleton: View {
    let lineCount: Int

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dimmed = false

    init(lineCount: Int = 5) {
        self.lineCount = lineCount
    }

    var body: some View {
        VStack(spacing: AppSpacing.s) {
            ForEach(0..<lineCount, id: \.self) { index in
                HStack(spacing: AppSpacing.s) {
                    Circle()
                        .fill(AppColor.surfaceVariant.opacity(0.55))
                        .frame(width: 40, height: 40)
                    VStack(alignment: .leading, spacing: AppSpacing.xs) {
                        RoundedRectangle(cornerRadius: 7)
                            .fill(AppColor.surfaceVariant.opacity(index % 3 == 0 ? 0.35 : 0.5))
                            .frame(height: 14)
                            .frame(maxWidth: index % 3 == 0 ? 220 : .infinity)
                        RoundedRectangle(cornerRadius: 6)
                            .fill(AppColor.surfaceVariant.opacity(0.3))
                            .frame(width: 150, height: 12)
                    }
                }
                .padding(AppSpacing.m)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(AppColor.surface)
                .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
            }
        }
        .padding(.horizontal, AppSpacing.m)
        .opacity(dimmed ? 0.55 : 1)
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                dimmed = true
            }
        }
        .accessibilityIdentifier("history_loading")
        .accessibilityHidden(true)
    }
}

/// Direction-aware swipe math for the history row.
enum HistorySwipeMath {
    static let revealLimit: CGFloat = 120
    static let deleteThreshold: CGFloat = 80

    /// Logical card offset while dragging toward the delete edge. SwiftUI
    /// mirrors `offset(x:)` in RTL, so the value stays negative in both
    /// directions and the modifier produces the physical slide.
    static func revealOffset(_ translation: CGSize, isRTL: Bool) -> CGFloat? {
        guard abs(translation.width) > abs(translation.height) else { return nil }
        let towardTrailing = isRTL ? translation.width : -translation.width
        guard towardTrailing > 0 else { return nil }
        return -min(towardTrailing, revealLimit)
    }

    static func shouldDelete(_ translation: CGSize, isRTL: Bool) -> Bool {
        guard abs(translation.width) > abs(translation.height) else { return false }
        let towardTrailing = isRTL ? translation.width : -translation.width
        return towardTrailing >= deleteThreshold
    }
}
