import SwiftUI

struct PermissionView: View {
    @ObservedObject var container: AppContainer

    private var granted: Bool { container.micPermission == .granted }
    private var denied: Bool { container.micPermission == .denied }

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: AppSpacing.l) {
                    Spacer(minLength: AppSpacing.l)

                    Text(L10n.t("perm_dialog_title"))
                        .foregroundColor(AppColor.textPrimary)
                        .appFont(.title)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, AppSpacing.m)

                    micCard

                    cameraCard

                    if denied {
                        Text(L10n.t("perm_mic_required_hint"))
                            .foregroundColor(AppColor.error)
                            .appFont(.label)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, AppSpacing.m)
                    }

                    Button(action: container.startOrRequestPermission) {
                        Text(L10n.t("perm_start_chat"))
                            .foregroundColor(.white)
                            .appFont(.body)
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: 56)
                            .background(AppColor.brandIndigo)
                            .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
                    }
                    .accessibilityIdentifier("permission_start")

                    if denied {
                        Button(action: container.openSystemSettings) {
                            Text(L10n.t("perm_go_to_settings"))
                                .foregroundColor(AppColor.brandIndigo)
                                .appFont(.label)
                                .frame(minHeight: 44)
                        }
                        .accessibilityIdentifier("permission_settings")
                    }

                    Spacer(minLength: AppSpacing.l)
                }
                .padding(.horizontal, AppSpacing.l)
                .frame(minHeight: geometry.size.height)
            }
        }
        .background(AppColor.background.ignoresSafeArea())
        .onAppear {
            container.refreshMicPermission()
            container.refreshCameraPermission()
        }
    }

    private var cameraCard: some View {
        HStack(spacing: AppSpacing.m) {
            ZStack {
                Circle()
                    .fill(AppColor.brandIndigo.opacity(0.18))
                    .frame(width: 44, height: 44)
                Image(systemName: "camera.fill")
                    .foregroundColor(AppColor.brandIndigo)
            }

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(L10n.t("perm_camera_title"))
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.body)
                Text(L10n.t("perm_camera_desc"))
                    .foregroundColor(AppColor.textSecondary)
                    .appFont(.label)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer()

            if container.cameraPermission == .granted {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(AppColor.success)
                    .accessibilityLabel(L10n.t("perm_granted"))
            } else {
                Button(action: container.requestCameraPermission) {
                    Text(L10n.t("perm_grant_action"))
                        .foregroundColor(AppColor.brandIndigo)
                        .appFont(.label)
                        .frame(minWidth: 44, minHeight: 44)
                }
                .accessibilityIdentifier("permission_camera")
            }
        }
        .padding(AppSpacing.m)
        .background(AppColor.surfaceVariant)
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
    }

    private var micCard: some View {
        HStack(spacing: AppSpacing.m) {
            ZStack {
                Circle()
                    .fill(AppColor.brandIndigo.opacity(0.18))
                    .frame(width: 44, height: 44)
                Image(systemName: "mic.fill")
                    .foregroundColor(AppColor.brandIndigo)
            }

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(L10n.t("perm_mic_title"))
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.body)
                Text(L10n.t("perm_mic_desc"))
                    .foregroundColor(AppColor.textSecondary)
                    .appFont(.label)
            }

            Spacer()

            if granted {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(AppColor.success)
                    .accessibilityLabel(L10n.t("perm_granted"))
            } else {
                Button(action: container.startOrRequestPermission) {
                    Text(L10n.t("perm_grant_action"))
                        .foregroundColor(AppColor.brandIndigo)
                        .appFont(.label)
                        .frame(minWidth: 44, minHeight: 44)
                }
            }
        }
        .padding(AppSpacing.m)
        .background(AppColor.surfaceVariant)
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
    }
}
