import SwiftUI

struct HistoryListView: View {
    @ObservedObject var viewModel: HistoryListViewModel
    let onBack: () -> Void
    let onSelect: (String) -> Void

    var body: some View {
        VStack(spacing: 0) {
            header
            searchField
            content
        }
        .background(AppColor.background.ignoresSafeArea())
        .task {
            await viewModel.initialLoad()
        }
        .alert(
            L10n.t("history_delete_title"),
            isPresented: Binding(
                get: { viewModel.showDeleteConfirm != nil },
                set: { if !$0 { viewModel.showDeleteConfirm = nil } }
            )
        ) {
            Button(L10n.t("common_cancel"), role: .cancel) {
                viewModel.showDeleteConfirm = nil
            }
            Button(L10n.t("common_delete"), role: .destructive) {
                if let id = viewModel.showDeleteConfirm {
                    viewModel.deleteSession(id: id)
                }
            }
        } message: {
            Text(L10n.t("history_delete_message"))
        }
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            Button(action: onBack) {
                Image(systemName: "chevron.backward")
                    .foregroundColor(AppColor.textPrimary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("common_back"))
            .accessibilityIdentifier("history_back")

            Text(L10n.t("history_title"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.title)

            Spacer()

            Button {
                viewModel.showClearAllConfirm = true
            } label: {
                Text(L10n.t("history_clear"))
                    .foregroundColor(
                        viewModel.canClear && !viewModel.isClearing
                            ? AppColor.error
                            : AppColor.textTertiary
                    )
                    .appFont(.label)
                    .frame(minHeight: 44)
            }
            .disabled(!viewModel.canClear || viewModel.isClearing)
            .accessibilityIdentifier("history_clear")
            .alert(
                L10n.t("history_clear_all_title"),
                isPresented: $viewModel.showClearAllConfirm
            ) {
                Button(L10n.t("common_cancel"), role: .cancel) {}
                Button(L10n.t("history_clear"), role: .destructive) {
                    viewModel.clearAll()
                }
            } message: {
                Text(L10n.t("history_clear_all_message"))
            }
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 52)
    }

    private var searchField: some View {
        HStack(spacing: AppSpacing.s) {
            Image(systemName: "magnifyingglass")
                .foregroundColor(AppColor.textSecondary)
                .accessibilityHidden(true)
            TextField(L10n.t("history_search_placeholder"), text: $viewModel.searchQuery)
                .textInputAutocapitalization(.never)
                .disableAutocorrection(true)
                .foregroundColor(AppColor.textPrimary)
                .appFont(.body)
                .accessibilityIdentifier("history_search")
        }
        .padding(.horizontal, AppSpacing.m)
        .frame(minHeight: 48)
        .background(AppColor.background)
        .overlay {
            RoundedRectangle(cornerRadius: AppRadius.m)
                .stroke(AppColor.surfaceVariant, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .padding(.horizontal, AppSpacing.m)
        .onChange(of: viewModel.searchQuery) { _ in
            viewModel.searchQueryChanged()
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading {
            HistoryLoadingSkeleton()
        } else if viewModel.sessions.isEmpty {
            HistoryEmptyState()
        } else {
            ScrollView {
                LazyVStack(spacing: AppSpacing.s) {
                    ForEach(viewModel.sessions) { session in
                        HistorySessionRow(
                            session: session,
                            onSelect: { onSelect(session.id) },
                            onDelete: { viewModel.showDeleteConfirm = session.id }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.m)
                .padding(.top, AppSpacing.s)
                .padding(.bottom, AppSpacing.m)
            }
            .accessibilityIdentifier("history_list")
        }
    }
}
