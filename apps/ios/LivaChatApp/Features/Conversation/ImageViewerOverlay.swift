import SwiftUI

struct ImageViewerOverlay: View {
    let images: [ChatMessage]
    let initialMessageID: String
    let onDismiss: () -> Void
    private let saver: PhotoLibrarySaving

    @Environment(\.layoutDirection) private var layoutDirection

    @State private var pageIndex: Int
    @State private var decodedImages: [String: CGImage] = [:]
    @State private var loadingIDs: Set<String> = []
    @State private var viewport: CGSize = .zero
    @State private var scale: CGFloat = 1
    @State private var lastScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var lastOffset: CGSize = .zero
    @State private var pageDrag: CGFloat = 0
    @State private var settling = false
    @State private var pinching = false
    @State private var saveTip: String?
    @State private var tipTask: Task<Void, Never>?

    init(
        images: [ChatMessage],
        initialMessageID: String,
        saver: PhotoLibrarySaving = PhotoLibrarySaver(),
        onDismiss: @escaping () -> Void
    ) {
        self.images = images
        self.initialMessageID = initialMessageID
        self.saver = saver
        self.onDismiss = onDismiss
        let index = images.firstIndex(where: { $0.id == initialMessageID }) ?? 0
        _pageIndex = State(initialValue: index)
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color.black.ignoresSafeArea()

                imageContent(width: geometry.size.width)
                    .frame(width: geometry.size.width, height: geometry.size.height)
                    .clipped()
                    .contentShape(Rectangle())
                    .onTapGesture(perform: onDismiss)
                    .gesture(magnification)
                    .simultaneousGesture(pan)
                    .accessibilityAction(.escape, onDismiss)

                if images.count > 1 {
                    pageIndicator
                        .padding(.top, AppSpacing.l)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                }

                saveButton
                    .padding(AppSpacing.l)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)

                if let saveTip {
                    Text(saveTip)
                        .foregroundColor(AppColor.textPrimary)
                        .appFont(.label)
                        .padding(.horizontal, AppSpacing.m)
                        .padding(.vertical, AppSpacing.s)
                        .background(Color.black.opacity(0.75))
                        .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
                        .padding(.bottom, AppSpacing.l)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                        .accessibilityIdentifier("image_viewer_save_tip")
                }
            }
            .onAppear {
                viewport = geometry.size
                loadNearbyImages()
            }
            .onChange(of: geometry.size) { viewport = $0 }
        }
        .interactiveDismissDisabled()
        .onDisappear {
            tipTask?.cancel()
            tipTask = nil
        }
    }

    private var isRTL: Bool { layoutDirection == .rightToLeft }

    private var currentMessage: ChatMessage? {
        images.indices.contains(pageIndex) ? images[pageIndex] : nil
    }

    private var currentImageSize: CGSize {
        guard let message = currentMessage,
              let decoded = decodedImages[message.id] else { return .zero }
        return CGSize(width: decoded.width, height: decoded.height)
    }

    private func imageContent(width: CGFloat) -> some View {
        ZStack {
            if scale <= ImageViewerMath.minScale {
                neighborImage(offset: -1, width: width)
                neighborImage(offset: 1, width: width)
            }
            if let message = currentMessage,
               let decoded = decodedImages[message.id] {
                Image(decorative: decoded, scale: 1)
                    .resizable()
                    .scaledToFit()
                    .scaleEffect(scale)
                    .offset(
                        x: (scale > ImageViewerMath.minScale ? offset.width : pageDrag),
                        y: scale > ImageViewerMath.minScale ? offset.height : 0
                    )
                    .accessibilityLabel(L10n.t("chat_ai_image"))
            }
        }
    }

    @ViewBuilder
    private func neighborImage(offset direction: Int, width: CGFloat) -> some View {
        let index = pageIndex + direction
        if images.indices.contains(index) {
            let message = images[index]
            if let decoded = decodedImages[message.id] {
                Image(decorative: decoded, scale: 1)
                    .resizable()
                    .scaledToFit()
                    // SwiftUI mirrors `offset(x:)` in RTL, which keeps the
                    // neighbour page on the reading-trailing side.
                    .offset(x: ImageViewerMath.neighborOffset(direction: direction, width: width) + pageDrag)
                    .accessibilityHidden(true)
            }
        }
    }

    private var pageIndicator: some View {
        Text(LocalizedNumber.page(index: pageIndex + 1, total: images.count))
            .foregroundColor(AppColor.textPrimary)
            .appFont(.label)
            .padding(.horizontal, AppSpacing.m)
            .padding(.vertical, AppSpacing.xs)
            .background(Color.black.opacity(0.35))
            .clipShape(Capsule())
            .environment(\.layoutDirection, .leftToRight)
            .accessibilityIdentifier("image_viewer_page")
    }

    private var saveButton: some View {
        Button(action: saveCurrent) {
            Image(systemName: "arrow.down.to.line")
                .foregroundColor(AppColor.textPrimary)
                .frame(width: 40, height: 40)
                .background(Color.black.opacity(0.35))
                .clipShape(Circle())
                .frame(width: 44, height: 44)
        }
        .disabled(settling || abs(pageDrag) > 1)
        .accessibilityLabel(L10n.t("common_save_image"))
        .accessibilityIdentifier("image_viewer_save")
    }

    private var magnification: some Gesture {
        MagnificationGesture()
            .onChanged { value in
                pinching = true
                pageDrag = 0
                scale = ImageViewerMath.clampedScale(lastScale * value)
                offset = ImageViewerMath.clampedOffset(
                    offset,
                    imageSize: currentImageSize,
                    scale: scale,
                    viewport: viewport
                )
            }
            .onEnded { _ in
                pinching = false
                lastScale = scale
                if scale <= ImageViewerMath.minScale {
                    resetTransform()
                } else {
                    offset = ImageViewerMath.clampedOffset(
                        offset,
                        imageSize: currentImageSize,
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
                guard !settling, !pinching else { return }
                if scale > ImageViewerMath.minScale {
                    // `offset` is applied through `.offset(x:)`, which SwiftUI
                    // mirrors in RTL; negate the physical drag so the zoomed
                    // image still follows the finger.
                    let dx = isRTL ? -value.translation.width : value.translation.width
                    let proposed = CGSize(
                        width: lastOffset.width + dx,
                        height: lastOffset.height + value.translation.height
                    )
                    offset = ImageViewerMath.clampedOffset(
                        proposed,
                        imageSize: currentImageSize,
                        scale: scale,
                        viewport: viewport
                    )
                } else {
                    pageDrag = ImageViewerMath.dragOffset(
                        translationX: value.translation.width,
                        pageIndex: pageIndex,
                        pageCount: images.count,
                        width: viewport.width,
                        isRTL: isRTL
                    )
                }
            }
            .onEnded { value in
                guard !settling, !pinching else { return }
                if scale > ImageViewerMath.minScale {
                    lastOffset = offset
                    return
                }
                let direction = ImageViewerMath.pageChange(
                    translationX: value.translation.width,
                    pageIndex: pageIndex,
                    pageCount: images.count,
                    isRTL: isRTL
                )
                if direction != 0 {
                    settlePage(direction: direction)
                } else {
                    withAnimation(.easeOut(duration: 0.2)) {
                        pageDrag = 0
                    }
                }
            }
    }

    private func settlePage(direction: Int) {
        settling = true
        withAnimation(.easeOut(duration: 0.2)) {
            pageDrag = ImageViewerMath.settleOffset(direction: direction, width: viewport.width)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                pageIndex += direction
                pageDrag = 0
                resetTransform()
                settling = false
            }
            loadNearbyImages()
        }
    }

    private func resetTransform() {
        scale = 1
        lastScale = 1
        offset = .zero
        lastOffset = .zero
    }

    private func loadNearbyImages() {
        let indices = [pageIndex - 1, pageIndex, pageIndex + 1]
        let nearbyIDs = Set(indices.compactMap { images.indices.contains($0) ? images[$0].id : nil })
        decodedImages = decodedImages.filter { nearbyIDs.contains($0.key) }
        for index in indices where images.indices.contains(index) {
            let message = images[index]
            guard let image = message.image,
                  decodedImages[message.id] == nil,
                  !loadingIDs.contains(message.id) else {
                continue
            }
            loadingIDs.insert(message.id)
            Task.detached(priority: .userInitiated) {
                let decoded = AIImageDecoder.decode(image, cacheKey: message.id)
                await MainActor.run {
                    if let decoded {
                        decodedImages[message.id] = decoded
                    }
                    loadingIDs.remove(message.id)
                }
            }
        }
    }

    private func saveCurrent() {
        guard let message = currentMessage,
              let image = message.image,
              let decoded = decodedImages[message.id]
                ?? AIImageDecoder.decode(image, cacheKey: message.id),
              let jpeg = CameraImageEncoder.jpegData(from: decoded, quality: 0.9) else {
            showTip(L10n.t("common_save_failed"))
            return
        }
        saver.save(jpeg: jpeg) { success in
            showTip(L10n.t(success ? "common_saved_to_gallery" : "common_save_failed"))
        }
    }

    private func showTip(_ text: String) {
        tipTask?.cancel()
        saveTip = text
        tipTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled else { return }
            saveTip = nil
            tipTask = nil
        }
    }
}

enum ImageViewerMath {
    static let minScale: CGFloat = 1
    static let maxScale: CGFloat = 5

    static func clampedScale(_ scale: CGFloat) -> CGFloat {
        min(max(scale, minScale), maxScale)
    }

    static func fitSize(imageSize: CGSize, viewport: CGSize) -> CGSize {
        guard imageSize.width > 0, imageSize.height > 0,
              viewport.width > 0, viewport.height > 0 else {
            return .zero
        }
        let scale = min(viewport.width / imageSize.width, viewport.height / imageSize.height)
        return CGSize(width: imageSize.width * scale, height: imageSize.height * scale)
    }

    static func clampedOffset(
        _ offset: CGSize,
        imageSize: CGSize,
        scale: CGFloat,
        viewport: CGSize
    ) -> CGSize {
        guard scale > minScale else { return .zero }
        let fit = fitSize(imageSize: imageSize, viewport: viewport)
        guard fit.width > 0, fit.height > 0 else { return .zero }
        let maxX = max(0, (fit.width * scale - viewport.width) / 2)
        let maxY = max(0, (fit.height * scale - viewport.height) / 2)
        return CGSize(
            width: min(max(offset.width, -maxX), maxX),
            height: min(max(offset.height, -maxY), maxY)
        )
    }

    static let pageThreshold: CGFloat = 60

    /// Returns +1 for the next page, -1 for the previous page, 0 for no change.
    /// Logical page order follows the reading direction.
    static func pageChange(
        translationX: CGFloat,
        pageIndex: Int,
        pageCount: Int,
        isRTL: Bool
    ) -> Int {
        let logical = isRTL ? -translationX : translationX
        if logical < -pageThreshold, pageIndex < pageCount - 1 { return 1 }
        if logical > pageThreshold, pageIndex > 0 { return -1 }
        return 0
    }

    /// Logical offset for `offset(x:)`. SwiftUI mirrors the modifier in RTL,
    /// so both directions share one value.
    static func neighborOffset(direction: Int, width: CGFloat) -> CGFloat {
        CGFloat(direction) * width
    }

    static func settleOffset(direction: Int, width: CGFloat) -> CGFloat {
        -CGFloat(direction) * width
    }

    /// Logical drag offset for `offset(x:)`; the RTL sign is inverted so the
    /// image follows the finger physically.
    static func dragOffset(
        translationX: CGFloat,
        pageIndex: Int,
        pageCount: Int,
        width: CGFloat,
        isRTL: Bool
    ) -> CGFloat {
        let maxDrag = max(width, 1)
        let logical = isRTL ? -translationX : translationX
        if logical < 0, pageIndex < pageCount - 1 {
            return max(logical, -maxDrag)
        }
        if logical > 0, pageIndex > 0 {
            return min(logical, maxDrag)
        }
        return max(min(logical * 0.25, maxDrag * 0.25), -maxDrag * 0.25)
    }
}
