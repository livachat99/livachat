import SwiftUI

struct MylifeSearchBar: View {
    @Binding var query: String
    var focus: FocusState<Bool>.Binding
    var onSubmit: () -> Void

    var body: some View {
        HStack(spacing: AppSpacing.s) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 18, weight: .regular))
                .foregroundColor(MylifePalette.dust)
                .accessibilityHidden(true)

            TextField(L10n.t("mylife_search_placeholder"), text: $query)
                .mylifeFont(.body)
                .foregroundColor(MylifePalette.star)
                .textInputAutocapitalization(.never)
                .disableAutocorrection(true)
                .submitLabel(.search)
                .focused(focus)
                .onSubmit(onSubmit)
                .accessibilityIdentifier("timeline_search")

            if !query.isEmpty {
                Button {
                    query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18, weight: .regular))
                        .foregroundColor(MylifePalette.dust)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(L10n.t("common_clear"))
                .accessibilityIdentifier("timeline_search_clear")
            }
        }
        .padding(.horizontal, AppSpacing.m)
        .frame(minHeight: 56)
        .background(MylifePalette.panel.opacity(focus.wrappedValue ? 0.6 : 0.4))
        .overlay {
            RoundedRectangle(cornerRadius: AppRadius.xs)
                .stroke(MylifePalette.line, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.xs))
        .padding(.horizontal, AppSpacing.m)
    }
}
