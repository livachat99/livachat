import SwiftUI

struct LicensesView: View {
    let bundle: Bundle

    @Environment(\.dismiss) private var dismiss

    init(bundle: Bundle = .main) {
        self.bundle = bundle
    }

    private var text: String {
        Self.licenseText(bundle: bundle)
    }

    static func licenseText(bundle: Bundle) -> String {
        guard let url = bundle.url(forResource: "LICENSES", withExtension: "txt"),
              let content = try? String(contentsOf: url, encoding: .utf8) else {
            return L10n.t("licenses_unavailable")
        }
        return content
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: AppSpacing.s) {
                Text(L10n.t("licenses_title"))
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.title)
                    .accessibilityIdentifier("licenses_title")

                Spacer()

                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .foregroundColor(AppColor.textSecondary)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel(L10n.t("common_close"))
                .accessibilityIdentifier("licenses_close")
            }
            .padding(.horizontal, AppSpacing.m)
            .frame(minHeight: 52)

            ScrollView {
                Text(text)
                    .foregroundColor(AppColor.textSecondary)
                    .appFont(.caption)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(AppSpacing.m)
            }
        }
        .background(AppColor.background.ignoresSafeArea())
    }
}
