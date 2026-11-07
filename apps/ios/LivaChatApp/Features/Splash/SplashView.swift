import SwiftUI

struct SplashView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulsing = false

    var body: some View {
        VStack(spacing: AppSpacing.m) {
            Spacer()

            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [AppColor.brandIndigo, AppColor.brandViolet],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 80, height: 80)
                    .scaleEffect(pulsing ? 1.05 : 1.0)
                Text("~")
                    .foregroundColor(.white)
                    .appFont(.headline)
            }

            Text(verbatim: "LivaChat")
                .foregroundColor(AppColor.textPrimary)
                .appFont(.headline)

            Text(L10n.t("splash_tagline"))
                .foregroundColor(AppColor.textSecondary)
                .appFont(.label)

            Spacer()

            Text(L10n.t("splash_waking"))
                .foregroundColor(AppColor.textSecondary)
                .appFont(.label)
                .padding(.bottom, AppSpacing.xxl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(AppColor.background.ignoresSafeArea())
        .accessibilityIdentifier("splash_root")
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) {
                pulsing = true
            }
        }
    }
}
