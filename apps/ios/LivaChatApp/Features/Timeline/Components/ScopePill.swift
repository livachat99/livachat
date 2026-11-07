import SwiftUI

struct ScopePill: View {
    let scopeKey: String
    let onClear: () -> Void

    var body: some View {
        let label = MylifeDateLabels.scopeLabel(scopeKey)
        Button(action: onClear) {
            Text(L10n.format("mylife_scope_filter", label))
                .mylifeFont(.label)
                .foregroundColor(MylifePalette.abyss)
                .padding(.horizontal, AppSpacing.m)
                .padding(.vertical, AppSpacing.s)
                .background(MylifePalette.gold.opacity(0.9))
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("timeline_scope")
    }
}
