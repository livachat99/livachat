import SwiftUI

struct SettingsView: View {
    @ObservedObject var viewModel: SettingsViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showingLicenses = false

    var body: some View {
        VStack(spacing: 0) {
            header

            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.l) {
                    serviceSection
                    videoSection
                    generalSection
                    aboutSection
                }
                .padding(AppSpacing.m)
            }
        }
        .background(AppColor.background.ignoresSafeArea())
        .alert(
            L10n.t("settings_apply_confirm_title"),
            isPresented: $viewModel.showApplyConfirmation
        ) {
            Button(L10n.t("common_cancel"), role: .cancel) {
                viewModel.cancelApply()
            }
            Button(L10n.t("settings_apply"), role: .destructive) {
                if viewModel.apply() {
                    dismiss()
                }
            }
        } message: {
            Text(L10n.t("settings_apply_confirm_message"))
        }
        .sheet(isPresented: $showingLicenses) {
            LicensesView()
                .appLocalizationEnvironment()
        }
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            Button {
                dismiss()
            } label: {
                Image(systemName: "chevron.backward")
                    .foregroundColor(AppColor.textPrimary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("common_back"))
            .accessibilityIdentifier("settings_back")

            Text(L10n.t("settings_title"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.title)

            Spacer()
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 52)
    }

    private var serviceSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.s) {
            sectionHeader(L10n.t("settings_section_service"))

            VStack(alignment: .leading, spacing: AppSpacing.m) {
                labeledField(L10n.t("settings_server_label")) {
                    TextField(L10n.t("settings_server_placeholder"), text: $viewModel.address)
                        .textInputAutocapitalization(.never)
                        .disableAutocorrection(true)
                        .keyboardType(.URL)
                        .multilineTextAlignment(.leading)
                        .environment(\.layoutDirection, .leftToRight)
                        .foregroundColor(AppColor.textPrimary)
                        .appFont(.body)
                        .accessibilityIdentifier("settings_server")
                }

                labeledField(L10n.t("settings_auth_token")) {
                    HStack(spacing: AppSpacing.s) {
                        if viewModel.showSecret {
                            TextField(L10n.t("settings_auth_token_placeholder"), text: $viewModel.secret)
                                .textInputAutocapitalization(.never)
                                .disableAutocorrection(true)
                                .multilineTextAlignment(.leading)
                                .foregroundColor(AppColor.textPrimary)
                                .appFont(.body)
                                .accessibilityIdentifier("settings_secret")
                        } else {
                            SecureField(L10n.t("settings_auth_token_placeholder"), text: $viewModel.secret)
                                .multilineTextAlignment(.leading)
                                .foregroundColor(AppColor.textPrimary)
                                .appFont(.body)
                                .accessibilityIdentifier("settings_secret")
                        }

                        Button {
                            viewModel.showSecret.toggle()
                        } label: {
                            Image(systemName: viewModel.showSecret ? "eye.slash" : "eye")
                                .foregroundColor(AppColor.textSecondary)
                                .frame(width: 44, height: 44)
                        }
                        .accessibilityLabel(
                            viewModel.showSecret
                                ? L10n.t("settings_hide_token")
                                : L10n.t("settings_show_token")
                        )
                        .accessibilityIdentifier("settings_reveal_secret")
                    }
                    .environment(\.layoutDirection, .leftToRight)
                }

                tlsToggleRow

                probeRow

                if let key = viewModel.validationMessageKey {
                    Text(L10n.t(key))
                        .foregroundColor(AppColor.error)
                        .appFont(.caption)
                        .accessibilityIdentifier("settings_validation_error")
                }

                if let saveErrorKey = viewModel.saveErrorKey {
                    Text(L10n.t(saveErrorKey))
                        .foregroundColor(AppColor.error)
                        .appFont(.caption)
                        .accessibilityIdentifier("settings_save_error")
                }

                if viewModel.isDirty {
                    Button {
                        viewModel.requestApply()
                    } label: {
                        Text(L10n.t("settings_save"))
                            .foregroundColor(.white)
                            .appFont(.body)
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: 56)
                            .background(viewModel.canSave ? AppColor.brandIndigo : AppColor.surfaceVariant)
                            .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
                    }
                    .disabled(!viewModel.canSave)
                    .accessibilityIdentifier("settings_save")
                }
            }
            .padding(AppSpacing.m)
            .background(AppColor.surface)
            .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
        }
    }

    private var tlsToggleRow: some View {
        HStack(alignment: .center, spacing: AppSpacing.m) {
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(L10n.t("settings_tls_allow_self_signed"))
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.body)
                Text(L10n.t("settings_tls_allow_self_signed_desc"))
                    .foregroundColor(AppColor.textSecondary)
                    .appFont(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Toggle("", isOn: $viewModel.tlsAllowSelfSigned)
                .labelsHidden()
                .tint(AppColor.brandIndigo)
                .accessibilityIdentifier("settings_tls_allow_self_signed")
        }
        .padding(.vertical, AppSpacing.xs)
    }

    private var probeRow: some View {
        Button {
            guard viewModel.canProbe, !viewModel.isProbing else { return }
            viewModel.checkService()
        } label: {
            HStack(alignment: .center, spacing: AppSpacing.m) {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(L10n.t("settings_check_service"))
                        .foregroundColor(AppColor.textPrimary)
                        .appFont(.body)
                    Text(L10n.t("settings_check_service_desc"))
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer()

                if viewModel.isProbing {
                    ProgressView()
                        .tint(AppColor.brandIndigo)
                        .frame(width: 20, height: 20)
                } else if let result = viewModel.probeResult {
                    Text(probeText(result))
                        .foregroundColor(probeColor(result))
                        .appFont(.label)
                        .accessibilityIdentifier("settings_probe_status")
                } else {
                    Text(L10n.t("settings_check_action"))
                        .foregroundColor(viewModel.canProbe ? AppColor.brandIndigo : AppColor.textTertiary)
                        .appFont(.label)
                }
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("settings_probe")
        .padding(.vertical, AppSpacing.xs)
    }

    private var videoSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.s) {
            sectionHeader(L10n.t("settings_section_video"))
            VStack(spacing: AppSpacing.s) {
                toggleRow(
                    title: L10n.t("settings_default_front_camera"),
                    subtitle: L10n.t("settings_default_front_camera_desc"),
                    isOn: $viewModel.cameraDefaultFront,
                    identifier: "settings_camera_default_front"
                )
                toggleRow(
                    title: L10n.t("settings_auto_camera_on_join"),
                    subtitle: L10n.t("settings_auto_camera_on_join_desc"),
                    isOn: $viewModel.cameraAutoOnJoin,
                    identifier: "settings_camera_auto_on_join"
                )
            }
            .padding(AppSpacing.m)
            .background(AppColor.surface)
            .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
        }
    }

    private func toggleRow(
        title: String,
        subtitle: String,
        isOn: Binding<Bool>,
        identifier: String
    ) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(title)
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.body)
                Text(subtitle)
                    .foregroundColor(AppColor.textSecondary)
                    .appFont(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Toggle("", isOn: isOn)
                .labelsHidden()
                .tint(AppColor.brandIndigo)
                .accessibilityIdentifier(identifier)
        }
    }

    private var generalSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.s) {
            sectionHeader(L10n.t("settings_section_general"))
            VStack(spacing: AppSpacing.s) {
                HStack {
                    VStack(alignment: .leading, spacing: AppSpacing.xs) {
                        Text(L10n.t("settings_haptic"))
                            .foregroundColor(AppColor.textPrimary)
                            .appFont(.body)
                        Text(L10n.t("settings_haptic_desc"))
                            .foregroundColor(AppColor.textSecondary)
                            .appFont(.caption)
                    }
                    Spacer()
                    Toggle("", isOn: $viewModel.hapticEnabled)
                        .labelsHidden()
                        .tint(AppColor.brandIndigo)
                        .accessibilityIdentifier("settings_haptic")
                }
                toggleRow(
                    title: L10n.t("settings_ai_proactive"),
                    subtitle: L10n.t("settings_ai_proactive_desc"),
                    isOn: $viewModel.avdetectorEnabled,
                    identifier: "settings_ai_proactive"
                )
            }
            .padding(AppSpacing.m)
            .background(AppColor.surface)
            .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
        }
    }

    private func legalRow(
        _ title: String,
        identifier: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack {
                Text(title)
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.body)
                Spacer()
                Image(systemName: "chevron.forward")
                    .foregroundColor(AppColor.textTertiary)
            }
            .padding(.horizontal, AppSpacing.m)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }

    private func open(_ url: URL) {
        SafariLinkPresenter.shared.present(url)
    }

    private var aboutSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.s) {
            sectionHeader(L10n.t("settings_section_about"))

            VStack(spacing: 0) {
                HStack {
                    Text(verbatim: "LivaChat")
                        .foregroundColor(AppColor.textPrimary)
                        .appFont(.body)
                    Spacer()
                    Text(viewModel.versionText)
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.label)
                }
                .padding(.horizontal, AppSpacing.m)
                .frame(minHeight: 52)

                Divider().background(AppColor.surfaceVariant)
                legalRow(
                    L10n.t("settings_privacy_policy"),
                    identifier: "settings_privacy"
                ) {
                    open(LegalLinks.privacy)
                }
                Divider().background(AppColor.surfaceVariant)
                legalRow(
                    L10n.t("settings_eula"),
                    identifier: "settings_eula"
                ) {
                    open(LegalLinks.eula)
                }
                Divider().background(AppColor.surfaceVariant)
                legalRow(
                    L10n.t("settings_support"),
                    identifier: "settings_support"
                ) {
                    open(LegalLinks.support)
                }
                Divider().background(AppColor.surfaceVariant)
                legalRow(
                    L10n.t("settings_open_source_licenses"),
                    identifier: "settings_licenses"
                ) {
                    showingLicenses = true
                }
            }
            .background(AppColor.surface)
            .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
        }
    }

    private func sectionHeader(_ text: String) -> some View {
        Text(text)
            .foregroundColor(AppColor.brandIndigo)
            .appFont(.label)
            .padding(.leading, AppSpacing.xs)
    }

    private func labeledField<Content: View>(
        _ label: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.s) {
            Text(label)
                .foregroundColor(AppColor.textPrimary)
                .appFont(.body)
            content()
                .padding(.horizontal, AppSpacing.m - 4)
                .frame(minHeight: 44)
                .background(AppColor.background)
                .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
                .overlay {
                    RoundedRectangle(cornerRadius: AppRadius.m)
                        .stroke(AppColor.surfaceVariant, lineWidth: 1)
                }
        }
    }

    private func probeText(_ result: ProbeResult) -> String {
        switch result {
        case .available:
            return L10n.t("settings_status_available")
        case .authFailed:
            return L10n.t("settings_status_auth_failed")
        case .unreachable:
            return L10n.t("settings_status_unavailable")
        }
    }

    private func probeColor(_ result: ProbeResult) -> Color {
        result == .available ? AppColor.success : AppColor.error
    }
}
