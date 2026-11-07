import SwiftUI

struct FrameGalleryView: View {
    @StateObject private var viewModel: FrameGalleryViewModel
    let from: String
    let to: String
    let label: String
    let focusFrameId: String?
    let pending: Bool
    let onBack: () -> Void

    @State private var focusConsumed = false
    @State private var highlightedFrameId: String?

    init(
        store: MylifeServicing,
        from: String,
        to: String,
        label: String,
        focusFrameId: String?,
        pending: Bool,
        onBack: @escaping () -> Void
    ) {
        _viewModel = StateObject(wrappedValue: FrameGalleryViewModel(store: store))
        self.from = from
        self.to = to
        self.label = label
        self.focusFrameId = focusFrameId
        self.pending = pending
        self.onBack = onBack
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            MylifePalette.abyss.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                content
                if viewModel.editMode {
                    editBar
                }
            }
            .allowsHitTesting(viewModel.viewerIndex == nil)

            if let index = viewModel.viewerIndex {
                GalleryViewerOverlay(
                    frames: viewModel.frames,
                    initialPage: index,
                    onDismiss: viewModel.dismissViewer,
                    onNearEnd: viewModel.loadMore,
                    saveTip: viewModel.imageSaveTip,
                    savingFrameId: viewModel.savingFrameId,
                    onSaveClick: { viewModel.saveFrame(id: $0) }
                )
                .zIndex(2)
            }

            if let tip = viewModel.deleteTip {
                deleteTip(tip)
            }
        }
        .preferredColorScheme(.dark)
        .onAppear { viewModel.load(from: from, to: to) }
        .onDisappear { viewModel.flushOnExit() }
        .alert(
            L10n.t("mylife_delete_photos"),
            isPresented: Binding(
                get: { viewModel.deleteConfirm },
                set: { if !$0 { viewModel.dismissDeleteConfirm() } }
            )
        ) {
            Button(L10n.t("common_cancel"), role: .cancel) {
                viewModel.dismissDeleteConfirm()
            }
            Button(L10n.t("common_delete"), role: .destructive) {
                viewModel.deleteSelected()
            }
        } message: {
            Text(L10n.plural("mylife_delete_confirm", viewModel.selectedFrameIds.count))
        }
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            HStack(spacing: AppSpacing.s) {
                Button {
                    if viewModel.editMode {
                        viewModel.exitEditMode()
                    } else {
                        onBack()
                    }
                } label: {
                    Image(systemName: "chevron.backward")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(MylifePalette.dust)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .disabled(viewModel.deleting)
                .accessibilityLabel(L10n.t("common_back"))
                .accessibilityIdentifier("timeline_gallery_back")

                Text(label)
                    .mylifeFont(.subpageTitle)
                    .foregroundColor(MylifePalette.gold)
                    .lineLimit(2)
                    .truncationMode(.tail)
                    .accessibilityIdentifier("timeline_gallery_title")
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if case .data = viewModel.state {
                Text(countText)
                    .mylifeFont(.label)
                    .foregroundColor(MylifePalette.dust)
                    .lineLimit(1)
                    .accessibilityIdentifier("timeline_gallery_count")
            }

            HStack {
                Spacer()
                if canShowEdit {
                    Button {
                        if viewModel.editMode {
                            viewModel.exitEditMode()
                        } else {
                            viewModel.enterEditMode()
                        }
                    } label: {
                        Text(L10n.t(viewModel.editMode ? "mylife_edit_exit" : "mylife_edit"))
                            .mylifeFont(.button)
                            .foregroundColor(MylifePalette.gold)
                            .frame(minHeight: 44)
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.deleting)
                    .accessibilityIdentifier("timeline_gallery_edit")
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 52)
    }

    private var canShowEdit: Bool {
        viewModel.editMode || (viewModel.total > 0 && !viewModel.frames.isEmpty)
    }

    private var countText: String {
        if pending {
            return L10n.plural("mylife_analyzing_frames", viewModel.total)
        }
        return L10n.plural("mylife_frames_count", viewModel.total)
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {
        case .loading:
            MylifeLoadingHint(text: L10n.t("mylife_loading"))
        case .empty:
            MylifeEmptyState(
                title: L10n.t("mylife_gallery_empty_title"),
                subtitle: L10n.t("mylife_analysis_pending"),
                icon: "photo"
            )
        case let .error(message):
            MylifeErrorHint(message: message) {
                viewModel.load(from: from, to: to)
            }
        case let .data(frames, _, _, loadingMore):
            grid(frames: frames, loadingMore: loadingMore)
        }
    }

    private func grid(frames: [FrameItem], loadingMore: Bool) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVGrid(
                    columns: Array(repeating: GridItem(.flexible(), spacing: AppSpacing.xs), count: 3),
                    spacing: AppSpacing.xs
                ) {
                    ForEach(Array(frames.enumerated()), id: \.element.id) { index, frame in
                        frameCell(frame: frame, index: index)
                            .id(frame.frameId)
                            .onAppear {
                                if index >= frames.count - 6 {
                                    viewModel.loadMore()
                                }
                            }
                    }
                }
                .padding(AppSpacing.xs)

                if loadingMore {
                    MylifeLoadingHint(text: L10n.t("mylife_loading"))
                        .frame(height: 44)
                }
            }
            .onAppear { focusIfNeeded(frames: frames, proxy: proxy) }
            .onChange(of: viewModel.state) { _ in
                focusIfNeeded(frames: viewModel.frames, proxy: proxy)
            }
        }
    }

    private func frameCell(frame: FrameItem, index: Int) -> some View {
        ZStack(alignment: .bottomTrailing) {
            Button {
                viewModel.onFrameClick(index)
            } label: {
                MylifeSquareImage(url: frame.mediaUrl)
                    .frame(maxWidth: .infinity)
                    .overlay {
                        if highlightedFrameId == frame.frameId {
                            RoundedRectangle(cornerRadius: AppRadius.s)
                                .stroke(MylifePalette.gold, lineWidth: 2)
                        }
                    }
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L10n.t("chat_ai_image"))
            .accessibilityIdentifier("timeline_gallery_photo_\(frame.frameId)")

            if viewModel.editMode {
                selectionBox(frame: frame)
                    .padding(2)
            }
        }
    }

    private func selectionBox(frame: FrameItem) -> some View {
        let selected = viewModel.selectedFrameIds.contains(frame.frameId)
        let enabled = !(frame.eventRef?.isEmpty ?? true)
        return Button {
            viewModel.toggleSelect(frame)
        } label: {
            ZStack {
                RoundedRectangle(cornerRadius: AppRadius.xs)
                    .fill(selected ? MylifePalette.gold : Color.black.opacity(enabled ? 0.35 : 0.2))
                    .frame(width: 22, height: 22)
                    .overlay {
                        RoundedRectangle(cornerRadius: AppRadius.xs)
                            .stroke(
                                selected ? MylifePalette.gold : Color.white.opacity(enabled ? 0.85 : 0.3),
                                lineWidth: 1.5
                            )
                    }
                if selected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(MylifePalette.abyss)
                }
            }
            .padding(6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled || viewModel.deleting)
        .accessibilityLabel(L10n.t("mylife_select_photo"))
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("timeline_gallery_select_\(frame.frameId)")
    }

    private var editBar: some View {
        HStack {
            Spacer()
            Button {
                viewModel.requestDelete()
            } label: {
                HStack(spacing: AppSpacing.s) {
                    if viewModel.deleting {
                        ProgressView()
                            .progressViewStyle(CircularProgressViewStyle(tint: MylifePalette.red))
                            .scaleEffect(0.8)
                            .frame(width: 18, height: 18)
                        Text(L10n.t("mylife_deleting"))
                    } else {
                        Text(L10n.format("mylife_delete_count", viewModel.selectedFrameIds.count))
                    }
                }
                .mylifeFont(.button)
                .foregroundColor(MylifePalette.red)
                .padding(.horizontal, AppSpacing.m)
                .frame(minHeight: 44)
                .overlay {
                    RoundedRectangle(cornerRadius: AppRadius.l)
                        .stroke(MylifePalette.red, lineWidth: 1)
                }
            }
            .buttonStyle(.plain)
            .disabled(viewModel.selectedFrameIds.isEmpty || viewModel.deleting)
            .accessibilityIdentifier("timeline_gallery_delete")
            Spacer()
        }
        .padding(.vertical, AppSpacing.s)
    }

    private func deleteTip(_ text: String) -> some View {
        Text(text)
            .foregroundColor(MylifePalette.star)
            .mylifeFont(.button)
            .padding(.horizontal, AppSpacing.m)
            .padding(.vertical, AppSpacing.s)
            .background(Color.black.opacity(0.75))
            .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
            .padding(.bottom, viewModel.editMode ? 84 : 32)
            .accessibilityIdentifier("timeline_gallery_delete_tip")
    }

    private func focusIfNeeded(frames: [FrameItem], proxy: ScrollViewProxy) {
        guard !focusConsumed, case .data = viewModel.state else { return }
        focusConsumed = true
        guard let focusFrameId,
              frames.contains(where: { $0.frameId == focusFrameId }) else { return }
        proxy.scrollTo(focusFrameId, anchor: .center)
        highlightedFrameId = focusFrameId
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if highlightedFrameId == focusFrameId { highlightedFrameId = nil }
        }
    }
}
