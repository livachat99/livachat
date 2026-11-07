import SwiftUI

/// Wrapping layout for timeline chips.
///
/// iOS 16+ uses `Layout`, so the row height is resolved in the same layout pass
/// the row is created in. The legacy view-based implementation remains for
/// iOS 15: it measures chips through a preference key and updates state, which
/// can change a row's height after it has appeared inside a `LazyVStack`.
struct MylifeFlowLayout<Item: Hashable, Content: View>: View {
    let items: [Item]
    let spacing: CGFloat
    let content: (Item) -> Content

    @Environment(\.layoutDirection) private var layoutDirection

    init(
        items: [Item],
        spacing: CGFloat = AppSpacing.xs,
        @ViewBuilder content: @escaping (Item) -> Content
    ) {
        self.items = items
        self.spacing = spacing
        self.content = content
    }

    var body: some View {
        Group {
            if #available(iOS 16.0, *) {
                MylifeFlowLayout16(
                    items: items,
                    spacing: spacing,
                    layoutDirection: layoutDirection,
                    content: content
                )
            } else {
                MylifeFlowLayoutLegacy(
                    items: items,
                    spacing: spacing,
                    content: content
                )
            }
        }
    }
}

@available(iOS 16.0, *)
private struct MylifeFlowLayout16<Item: Hashable, Content: View>: View {
    let items: [Item]
    let spacing: CGFloat
    let layoutDirection: LayoutDirection
    let content: (Item) -> Content

    var body: some View {
        MylifeFlowLayoutImpl(
            spacing: spacing,
            isRTL: layoutDirection == .rightToLeft
        ) {
            ForEach(items, id: \.self) { item in
                content(item)
                    // Keep each chip's own reading direction; the layout
                    // itself only deals with physical coordinates.
                    .environment(\.layoutDirection, layoutDirection)
            }
        }
        // SwiftUI mirrors some positioning modifiers in RTL; pin the container
        // so FlowLayoutMath owns the mirroring exactly once.
        .environment(\.layoutDirection, .leftToRight)
    }
}

@available(iOS 16.0, *)
private struct MylifeFlowLayoutImpl: Layout {
    let spacing: CGFloat
    let isRTL: Bool

    struct Cache {
        var width: CGFloat = -1
        var sizes: [CGSize] = []
        var frames: [CGRect?] = []
        var height: CGFloat = 0
    }

    func makeCache(subviews: Subviews) -> Cache {
        Cache()
    }

    func updateCache(_ cache: inout Cache, subviews: Subviews) {
        cache = Cache()
    }

    func sizeThatFits(
        proposal: ProposedViewSize,
        subviews: Subviews,
        cache: inout Cache
    ) -> CGSize {
        let width = resolvedWidth(proposal: proposal, subviews: subviews)
        let sizes = measuredSizes(subviews: subviews, width: width)
        let result = FlowLayoutMath.frames(
            sizes: sizes.map(Optional.some),
            width: width,
            spacing: spacing,
            isRTL: isRTL
        )
        cache.width = width
        cache.sizes = sizes
        cache.frames = result.frames
        cache.height = result.height
        return CGSize(width: width, height: result.height)
    }

    func placeSubviews(
        in bounds: CGRect,
        proposal: ProposedViewSize,
        subviews: Subviews,
        cache: inout Cache
    ) {
        let width = proposal.width ?? bounds.width
        let sizes: [CGSize]
        let frames: [CGRect?]
        if abs(cache.width - width) <= 0.5,
           cache.sizes.count == subviews.count,
           cache.frames.count == subviews.count {
            sizes = cache.sizes
            frames = cache.frames
        } else {
            sizes = measuredSizes(subviews: subviews, width: width)
            frames = FlowLayoutMath.frames(
                sizes: sizes.map(Optional.some),
                width: width,
                spacing: spacing,
                isRTL: isRTL
            ).frames
        }

        for (index, subview) in subviews.enumerated() {
            guard index < frames.count, let frame = frames[index] else { continue }
            subview.place(
                at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                anchor: .topLeading,
                proposal: ProposedViewSize(width: frame.width, height: frame.height)
            )
        }
    }

    private func resolvedWidth(proposal: ProposedViewSize, subviews: Subviews) -> CGFloat {
        if let width = proposal.width, width.isFinite, width > 0 {
            return width
        }
        let idealWidths = subviews.map { $0.sizeThatFits(.unspecified).width }
        let total = idealWidths.reduce(0, +)
            + spacing * CGFloat(max(0, idealWidths.count - 1))
        return max(total, 0)
    }

    private func measuredSizes(subviews: Subviews, width: CGFloat) -> [CGSize] {
        guard width > 0 else {
            return Array(repeating: .zero, count: subviews.count)
        }
        return subviews.map {
            $0.sizeThatFits(ProposedViewSize(width: width, height: nil))
        }
    }
}

/// Minimal wrapping layout for iOS 15 (no Layout protocol). It measures each
/// chip, then places them left-to-right with a wrapping row.
private struct MylifeFlowLayoutLegacy<Item: Hashable, Content: View>: View {
    let items: [Item]
    let spacing: CGFloat
    let content: (Item) -> Content

    @Environment(\.layoutDirection) private var layoutDirection
    @State private var sizes: [Item: CGSize] = [:]
    @State private var frames: [Item: CGRect] = [:]
    @State private var totalHeight: CGFloat = 0

    private var isRTL: Bool { layoutDirection == .rightToLeft }

    init(
        items: [Item],
        spacing: CGFloat = AppSpacing.xs,
        @ViewBuilder content: @escaping (Item) -> Content
    ) {
        self.items = items
        self.spacing = spacing
        self.content = content
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .topLeading) {
                ForEach(items, id: \.self) { item in
                    content(item)
                        // Keep each chip's own reading direction; only the
                        // container is pinned LTR for physical coordinates.
                        .environment(\.layoutDirection, layoutDirection)
                        .background {
                            GeometryReader { proxy in
                                Color.clear.preference(
                                    key: MylifeFlowSizeKey<Item>.self,
                                    value: [item: proxy.size]
                                )
                            }
                        }
                        .frame(
                            width: sizes[item]?.width,
                            height: sizes[item]?.height,
                            alignment: .topLeading
                        )
                        .offset(
                            x: frames[item]?.minX ?? 0,
                            y: frames[item]?.minY ?? 0
                        )
                        .opacity(sizes[item] == nil ? 0 : 1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
            // SwiftUI mirrors offset(x:) and .topLeading; pin the container
            // so FlowLayoutMath owns the RTL mirroring exactly once.
            .environment(\.layoutDirection, .leftToRight)
            .onPreferenceChange(MylifeFlowSizeKey<Item>.self) { newSizes in
                sizes = newSizes
                layout(width: geometry.size.width)
            }
            .onChange(of: geometry.size.width) { width in
                layout(width: width)
            }
            .onChange(of: layoutDirection) { _ in
                layout(width: geometry.size.width)
            }
        }
        .frame(height: totalHeight)
    }

    private func layout(width: CGFloat) {
        guard width > 0 else { return }
        let orderedSizes = items.map { sizes[$0] }
        let result = FlowLayoutMath.frames(
            sizes: orderedSizes,
            width: width,
            spacing: spacing,
            isRTL: isRTL
        )
        var nextFrames: [Item: CGRect] = [:]
        for (index, item) in items.enumerated() {
            if let frame = result.frames[index] {
                nextFrames[item] = frame
            }
        }
        frames = nextFrames
        totalHeight = result.height
    }
}

/// Row packing in logical order; RTL mirrors each item's origin while keeping
/// the data order (first item at the trailing edge).
enum FlowLayoutMath {
    static func frames(
        sizes: [CGSize?],
        width: CGFloat,
        spacing: CGFloat,
        isRTL: Bool
    ) -> (frames: [CGRect?], height: CGFloat) {
        guard width > 0 else {
            return (Array(repeating: nil, count: sizes.count), 0)
        }

        var frames: [CGRect?] = Array(repeating: nil, count: sizes.count)
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0

        for (index, size) in sizes.enumerated() {
            guard let size else { continue }
            if x > 0, x + size.width > width {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            let originX = isRTL ? width - x - size.width : x
            frames[index] = CGRect(origin: CGPoint(x: originX, y: y), size: size)
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }

        return (frames, y + rowHeight)
    }
}

private struct MylifeFlowSizeKey<Item: Hashable>: PreferenceKey {
    static var defaultValue: [Item: CGSize] { [:] }

    static func reduce(value: inout [Item: CGSize], nextValue: () -> [Item: CGSize]) {
        value.merge(nextValue(), uniquingKeysWith: { $1 })
    }
}
