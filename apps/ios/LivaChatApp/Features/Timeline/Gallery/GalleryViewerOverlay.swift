import NetworkImage
import SwiftUI

struct GalleryViewerOverlay: View {
    let frames: [FrameItem]
    let initialPage: Int
    let onDismiss: () -> Void
    let onNearEnd: () -> Void
    let saveTip: String?
    let savingFrameId: String?
    let onSaveClick: (String) -> Void

    @State private var pageIndex: Int
    @State private var captionExpanded = false

    init(
        frames: [FrameItem],
        initialPage: Int,
        onDismiss: @escaping () -> Void,
        onNearEnd: @escaping () -> Void,
        saveTip: String?,
        savingFrameId: String?,
        onSaveClick: @escaping (String) -> Void
    ) {
        self.frames = frames
        self.initialPage = initialPage
        self.onDismiss = onDismiss
        self.onNearEnd = onNearEnd
        self.saveTip = saveTip
        self.savingFrameId = savingFrameId
        self.onSaveClick = onSaveClick
        _pageIndex = State(initialValue: min(max(initialPage, 0), max(frames.count - 1, 0)))
    }

    private var currentFrame: FrameItem? {
        frames.indices.contains(pageIndex) ? frames[pageIndex] : nil
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color.black.ignoresSafeArea()

                TabView(selection: $pageIndex) {
                    ForEach(Array(frames.enumerated()), id: \.element.frameId) { index, frame in
                        GalleryRemotePage(frame: frame, onTap: onDismiss)
                            .tag(index)
                    }
                }
                .tabViewStyle(.page(indexDisplayMode: .never))
                .ignoresSafeArea()

                if frames.count > 1 {
                    Text(LocalizedNumber.page(index: pageIndex + 1, total: frames.count))
                        .foregroundColor(MylifePalette.star)
                        .mylifeFont(.button)
                        .padding(.horizontal, AppSpacing.m)
                        .padding(.vertical, AppSpacing.xs)
                        .background(Color.black.opacity(0.35))
                        .clipShape(Capsule())
                        .environment(\.layoutDirection, .leftToRight)
                        .padding(.top, AppSpacing.l)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                        .accessibilityIdentifier("timeline_gallery_page")
                }
            }
            .overlay(alignment: .topTrailing) {
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(MylifePalette.star)
                        .frame(width: 44, height: 44)
                        .background(Color.black.opacity(0.35))
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .padding(AppSpacing.m)
                .accessibilityLabel(L10n.t("common_close"))
                .accessibilityIdentifier("timeline_gallery_viewer_close")
            }
            .overlay(alignment: .bottom) {
                bottomBar
                    .padding(.horizontal, AppSpacing.l)
                    .padding(.bottom, AppSpacing.l)
            }
            .onChange(of: pageIndex) { _ in
                if pageIndex >= frames.count - 3 {
                    onNearEnd()
                }
            }
        }
        .interactiveDismissDisabled()
    }

    @ViewBuilder
    private var bottomBar: some View {
        VStack(spacing: AppSpacing.s) {
            if let saveTip {
                Text(saveTip)
                    .foregroundColor(MylifePalette.star)
                    .mylifeFont(.button)
                    .padding(.horizontal, AppSpacing.m)
                    .padding(.vertical, AppSpacing.s)
                    .background(Color.black.opacity(0.75))
                    .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
                    .accessibilityIdentifier("timeline_gallery_save_tip")
            }

            HStack(spacing: 0) {
                if let caption = nonBlank(currentFrame?.caption) {
                    Button {
                        captionExpanded.toggle()
                    } label: {
                        HStack(spacing: AppSpacing.xs) {
                            Text(caption)
                                .foregroundColor(MylifePalette.star.opacity(0.85))
                                .mylifeFont(.caption)
                                .lineLimit(captionExpanded ? nil : 1)
                                .multilineTextAlignment(.center)
                            Text(captionExpanded ? "⌃" : "⌄")
                                .foregroundColor(MylifePalette.gold)
                                .mylifeFont(.caption)
                        }
                        .padding(.leading, AppSpacing.m)
                        .padding(.vertical, AppSpacing.s)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("timeline_gallery_caption")
                }

                saveButton
            }
            .padding(.horizontal, AppSpacing.xs)
            .background(Color.black.opacity(0.6))
            .clipShape(GalleryBarShape(circular: nonBlank(currentFrame?.caption) == nil))
        }
    }

    @ViewBuilder
    private var saveButton: some View {
        if let frame = currentFrame {
            Button {
                onSaveClick(frame.frameId)
            } label: {
                if savingFrameId == frame.frameId {
                    ProgressView()
                        .progressViewStyle(CircularProgressViewStyle(tint: MylifePalette.star))
                        .scaleEffect(0.8)
                        .frame(width: 44, height: 44)
                } else {
                    Image(systemName: "arrow.down.to.line")
                        .font(.system(size: 18, weight: .regular))
                        .foregroundColor(MylifePalette.star)
                        .frame(width: 44, height: 44)
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L10n.t("common_save_image"))
            .accessibilityIdentifier("timeline_gallery_save")
        }
    }

    private func nonBlank(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

private struct GalleryRemotePage: View {
    let frame: FrameItem
    let onTap: () -> Void

    @Environment(\.displayScale) private var displayScale
    @State private var scale: CGFloat = 1
    @State private var lastScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var lastOffset: CGSize = .zero
    @State private var imageSize: CGSize = .zero
    @State private var viewport: CGSize = .zero

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color.black.ignoresSafeArea()
                NetworkImage(url: frame.mediaUrl, scale: displayScale) { state in
                    switch state {
                    case .empty, .failure:
                        Color.clear
                    case let .success(image, idealSize):
                        image
                            .resizable()
                            .scaledToFit()
                            .onAppear {
                                if imageSize != idealSize { imageSize = idealSize }
                            }
                    }
                }
                .scaleEffect(scale)
                .offset(offset)
                .contentShape(Rectangle())
                .gesture(magnification)
                // At 1x let TabView's page gesture own horizontal drags; the
                // pan gesture only exists while zoomed.
                .simultaneousGesture(
                    pan,
                    including: scale > ImageViewerMath.minScale ? .all : .none
                )
                .onTapGesture(perform: onTap)
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
            .onAppear { viewport = geometry.size }
            .onChange(of: geometry.size) { viewport = $0 }
        }
    }

    private var magnification: some Gesture {
        MagnificationGesture()
            .onChanged { value in
                scale = ImageViewerMath.clampedScale(lastScale * value)
                offset = ImageViewerMath.clampedOffset(
                    offset,
                    imageSize: imageSize,
                    scale: scale,
                    viewport: viewport
                )
            }
            .onEnded { _ in
                lastScale = scale
                if scale <= ImageViewerMath.minScale {
                    reset()
                } else {
                    offset = ImageViewerMath.clampedOffset(
                        offset,
                        imageSize: imageSize,
                        scale: scale,
                        viewport: viewport
                    )
                    lastOffset = offset
                }
            }
    }

    private var pan: some Gesture {
        DragGesture(minimumDistance: 10)
            .onChanged { value in
                guard scale > ImageViewerMath.minScale else { return }
                offset = ImageViewerMath.clampedOffset(
                    CGSize(
                        width: lastOffset.width + value.translation.width,
                        height: lastOffset.height + value.translation.height
                    ),
                    imageSize: imageSize,
                    scale: scale,
                    viewport: viewport
                )
            }
            .onEnded { _ in
                guard scale > ImageViewerMath.minScale else { return }
                lastOffset = offset
            }
    }

    private func reset() {
        scale = 1
        lastScale = 1
        offset = .zero
        lastOffset = .zero
    }
}

private struct GalleryBarShape: Shape {
    let circular: Bool

    func path(in rect: CGRect) -> Path {
        if circular {
            return Capsule().path(in: rect)
        }
        return RoundedRectangle(cornerRadius: AppRadius.m).path(in: rect)
    }
}
