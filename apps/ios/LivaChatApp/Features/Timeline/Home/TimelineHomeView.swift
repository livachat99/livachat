import Foundation
import SwiftUI
import UIKit

private enum TimelineHomeListItem: Identifiable {
    case header(String)
    case card(PeriodDigest)

    var id: String {
        switch self {
        case let .header(key): return "header-\(key)"
        case let .card(digest): return "period-\(digest.key)"
        }
    }
}

struct TimelineHomeView: View {
    @ObservedObject var viewModel: TimelineHomeViewModel
    let isActive: Bool
    let onBack: () -> Void
    let onOpenEventList: (TimelineEventListNav) -> Void
    let onOpenGallery: (TimelineGalleryNav) -> Void
    let onOpenHotSlot: (TimelineHotSlotNav) -> Void
    let onOpenAudio: (TimelineAudioNav) -> Void

    @FocusState private var searchFocused: Bool
    @State private var listResetToken = 0
    @State private var selfHealAttempted = false
    @State private var selfHealTask: Task<Void, Never>?
    @State private var lastSelfHealAt = Date.distantPast
    @State private var scrollProbe = TimelineScrollProbe()

    var body: some View {
        ZStack(alignment: .bottom) {
            MylifePalette.abyss.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                MylifeSearchBar(
                    query: Binding(
                        get: { viewModel.query },
                        set: { viewModel.onQueryChange($0) }
                    ),
                    focus: $searchFocused,
                    onSubmit: { viewModel.onSearchImmediate() }
                )
                .padding(.bottom, AppSpacing.s)

                ZStack(alignment: .top) {
                    VStack(spacing: 0) {
                        browseContent
                    }
                    .opacity(viewModel.isSearchMode ? 0 : 1)
                    .allowsHitTesting(!viewModel.isSearchMode)
                    .accessibilityHidden(viewModel.isSearchMode)
                    if viewModel.isSearchMode {
                        searchContent
                    }
                }
            }

            if let scopeKey = viewModel.scopeKey {
                ScopePill(scopeKey: scopeKey) {
                    viewModel.onClearScope()
                }
                .padding(.bottom, AppSpacing.m)
            }
        }
        .preferredColorScheme(.dark)
        .simultaneousGesture(
            TapGesture().onEnded {
                searchFocused = false
            }
        )
        .accessibilityHidden(!isActive)
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            Button {
                searchFocused = false
                if viewModel.isSearchMode {
                    viewModel.clearQuery()
                } else {
                    onBack()
                }
            } label: {
                Image(systemName: viewModel.isSearchMode ? "xmark" : "chevron.backward")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundColor(MylifePalette.dust)
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(viewModel.isSearchMode ? L10n.t("common_clear") : L10n.t("common_back"))
            .accessibilityIdentifier("timeline_back")

            Text(L10n.t("common_time_machine"))
                .mylifeFont(.pageTitle)
                .foregroundColor(MylifePalette.gold)
                .accessibilityIdentifier("timeline_title")

            Spacer()
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 52)
    }

    @ViewBuilder
    private var browseContent: some View {
        GranularitySelector(selected: viewModel.granularity) { granularity in
            viewModel.onGranularityChange(granularity)
        }
        Spacer().frame(height: AppSpacing.s)

        if viewModel.overviewLoading, viewModel.overviewWindow == nil {
            MylifeLoadingHint(text: L10n.t("mylife_loading"))
        } else if let error = viewModel.overviewError, viewModel.overviewWindow == nil {
            MylifeErrorHint(message: error) { viewModel.refresh() }
        } else if viewModel.periods.isEmpty,
                  viewModel.scopeKey != nil || !viewModel.hasMore {
            MylifeEmptyState(
                title: emptyTitle,
                icon: "info.circle"
            )
        } else {
            browseList
        }
    }

    private var emptyTitle: String {
        if let scopeLabel = viewModel.scopeLabel {
            return L10n.format("mylife_no_records_in_scope", scopeLabel)
        }
        return L10n.t("mylife_no_records_yet")
    }

    private var browseList: some View {
        let items = buildItems()
        return ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    Color.clear
                        .frame(height: 0)
                        .id("top")
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        Group {
                            switch item {
                            case let .header(key):
                                groupHeader(key)
                            case let .card(digest):
                                PeriodCard(
                                    digest: digest,
                                    onBodyClick: cardBodyClick(digest),
                                    onHotSlotClick: { slot in
                                        searchFocused = false
                                        onOpenHotSlot(viewModel.hotSlotNavigation(slot, pending: digest.hasPending))
                                    },
                                    onImageClick: { image in
                                        searchFocused = false
                                        if let nav = viewModel.imageNavigation(image, digest: digest) {
                                            onOpenGallery(nav)
                                        }
                                    },
                                    onTopicClick: { topic in
                                        searchFocused = false
                                        viewModel.onTopicSearch(topic)
                                    },
                                    onAudioClick: {
                                        searchFocused = false
                                        if let nav = viewModel.audioNavigation(digest) {
                                            onOpenAudio(nav)
                                        }
                                    }
                                )
                                .onAppear {
                                    if index >= items.count - 3 {
                                        viewModel.onLoadMore()
                                    }
                                }
                            }
                        }
                        .background {
                            if index == 0 {
                                GeometryReader { proxy in
                                    Color.clear.preference(
                                        key: TimelineFirstItemMinYKey.self,
                                        value: proxy.frame(in: .named(TimelineScrollSpace.name)).minY
                                    )
                                }
                            }
                        }
                        .onDisappear {
                            if index == 0 {
                                // Do not let a recycled first row leave a stale
                                // position behind; the next appearance republishes it.
                                scrollProbe.firstItemMinY = nil
                                selfHealTask?.cancel()
                            }
                        }
                    }
                    if viewModel.scopeKey == nil {
                        footer
                    }
                }
                .background {
                    GeometryReader { proxy in
                        Color.clear.preference(
                            key: TimelineContentFrameKey.self,
                            value: proxy.frame(in: .named(TimelineScrollSpace.name))
                        )
                    }
                }
                .background {
                    ScrollInteractionObserver { dragging, scrollingUp in
                        scrollProbe.isDragging = dragging
                        scrollProbe.isScrollingUp = scrollingUp
                        if dragging {
                            selfHealTask?.cancel()
                            if selfHealAttempted { selfHealAttempted = false }
                        }
                        scheduleSelfHealIfNeeded()
                    }
                }
                .padding(.bottom, AppSpacing.xxl)
                .accessibilityIdentifier("timeline_list")
            }
            // A granularity change or a self-heal rebuilds the ScrollView with
            // a fresh identity, which also starts it at offset zero.
            .id(scrollIdentity)
            .coordinateSpace(name: TimelineScrollSpace.name)
            .background {
                GeometryReader { proxy in
                    Color.clear.preference(
                        key: TimelineViewportHeightKey.self,
                        value: proxy.size.height
                    )
                }
            }
            .onPreferenceChange(TimelineViewportHeightKey.self) { height in
                guard height > 0 else { return }
                scrollProbe.viewportHeight = height
            }
            .onPreferenceChange(TimelineContentFrameKey.self) { frame in
                // Fires whenever the lazy stack lays out or scrolls; the
                // self-heal re-checks the first row's position.
                guard let frame else { return }
                scrollProbe.contentMinY = frame.minY
                scheduleSelfHealIfNeeded()
            }
            .onPreferenceChange(TimelineFirstItemMinYKey.self) { minY in
                guard let minY else { return }
                scrollProbe.firstItemMinY = minY
                scheduleSelfHealIfNeeded()
            }
            .onChange(of: viewModel.scrollResetToken) { _ in
                scrollProbe.firstItemMinY = nil
                if selfHealAttempted { selfHealAttempted = false }
                proxy.scrollTo("top", anchor: .top)
            }
            .onChange(of: listResetToken) { _ in
                scrollProbe.firstItemMinY = nil
                proxy.scrollTo("top", anchor: .top)
            }
            .onChange(of: viewModel.periods) { _ in
                scrollProbe.firstItemMinY = nil
                if selfHealAttempted { selfHealAttempted = false }
            }
            .onChange(of: viewModel.granularity) { _ in
                scrollProbe.firstItemMinY = nil
                if selfHealAttempted { selfHealAttempted = false }
            }
        }
    }

    // MARK: - LazyVStack self-heal (iOS 15–26)

    /// A recycled top row can leave an estimated leading gap in `LazyVStack`
    /// on iOS 15–17. Only recover when all of these hold:
    /// - the user was scrolling back toward the top (never while scrolling down);
    /// - the finger is off the screen;
    /// - the visible black gap above the first row is larger than 100pt, and
    ///   the row is actually offset inside the content. A transient top
    ///   overscroll moves the whole content down, so both the content and the
    ///   first row are positive; requiring the difference between them avoids
    ///   rebuilding the list just because a fling bounced past the top.
    /// Rebuilding the ScrollView identity restores offset zero and a fresh
    /// layout. iOS 27 keeps the anchor realized, so it normally never fires.
    private static var selfHealEnabled: Bool {
        // iOS 27 corrects the lazy-stack estimate itself; running the
        // workaround there would only add unnecessary rebuilds.
        if #available(iOS 27.0, *) { return false }
        return true
    }

    private static let selfHealGapThreshold: CGFloat = 100

    private static func hasLeadingGap(
        firstItemMinY: CGFloat,
        contentMinY: CGFloat,
        viewportHeight: CGFloat
    ) -> Bool {
        guard viewportHeight > 0 else { return false }
        let visibleGap = firstItemMinY >= 0
            ? min(firstItemMinY, viewportHeight)
            : 0
        let gapInsideContent = firstItemMinY - contentMinY
        return visibleGap > selfHealGapThreshold
            && gapInsideContent > selfHealGapThreshold
    }

    private func scheduleSelfHealIfNeeded() {
        guard Self.selfHealEnabled, isActive, !viewModel.isSearchMode else { return }
        guard !scrollProbe.isDragging, scrollProbe.isScrollingUp else {
            selfHealTask?.cancel()
            return
        }
        guard let firstItemMinY = scrollProbe.firstItemMinY,
              let contentMinY = scrollProbe.contentMinY,
              scrollProbe.viewportHeight > 0 else {
            selfHealTask?.cancel()
            return
        }
        guard Self.hasLeadingGap(
            firstItemMinY: firstItemMinY,
            contentMinY: contentMinY,
            viewportHeight: scrollProbe.viewportHeight
        ) else {
            selfHealTask?.cancel()
            return
        }
        guard !selfHealAttempted,
              Date().timeIntervalSince(lastSelfHealAt) > 1.0 else { return }
        selfHealTask?.cancel()
        selfHealTask = Task { @MainActor in
            // Re-check on the next main-actor turn so the state mutation is
            // outside the current preference/observer callback. The finger is
            // already off the screen, so no extra settle delay is needed.
            guard !Task.isCancelled else { return }
            guard Self.selfHealEnabled,
                  isActive,
                  !viewModel.isSearchMode,
                  !scrollProbe.isDragging,
                  scrollProbe.isScrollingUp,
                  !selfHealAttempted,
                  Date().timeIntervalSince(lastSelfHealAt) > 1.0,
                  let firstItemMinY = scrollProbe.firstItemMinY,
                  let contentMinY = scrollProbe.contentMinY,
                  scrollProbe.viewportHeight > 0 else { return }
            guard Self.hasLeadingGap(
                firstItemMinY: firstItemMinY,
                contentMinY: contentMinY,
                viewportHeight: scrollProbe.viewportHeight
            ) else { return }
            selfHealAttempted = true
            lastSelfHealAt = Date()
            // The ScrollView gets a new identity below, so the first row's
            // old frame must not leak into the next detection pass.
            scrollProbe.firstItemMinY = nil
            listResetToken += 1
        }
    }

    private var scrollIdentity: String {
        "\(viewModel.scrollResetToken)-\(listResetToken)"
    }

    private func cardBodyClick(_ digest: PeriodDigest) -> (() -> Void)? {
        switch digest.granularity {
        case .month, .year:
            return { viewModel.onDrillDown(digest) }
        case .day, .week:
            return nil
        }
    }

    @ViewBuilder
    private var footer: some View {
        if footerActionable {
            Button {
                viewModel.onLoadMore(manual: true)
            } label: {
                footerLabel(footerText, color: footerColor)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("timeline_footer")
        } else {
            footerLabel(footerText, color: footerColor)
                .accessibilityIdentifier("timeline_footer")
        }
    }

    private var footerText: String {
        if viewModel.loadMoreError != nil { return L10n.t("mylife_load_failed_retry") }
        if viewModel.loadingMore { return L10n.t("mylife_loading_earlier") }
        if viewModel.hasMore { return L10n.t("mylife_load_earlier") }
        return L10n.t("mylife_reached_start")
    }

    private var footerColor: Color {
        viewModel.loadMoreError != nil ? MylifePalette.red : MylifePalette.dust
    }

    private var footerActionable: Bool {
        viewModel.loadMoreError != nil || (viewModel.hasMore && !viewModel.loadingMore)
    }

    private func footerLabel(_ text: String, color: Color) -> some View {
        Text(text)
            .mylifeFont(.label)
            .foregroundColor(color)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, AppSpacing.m)
            .padding(.vertical, AppSpacing.m)
            .multilineTextAlignment(.center)
    }

    private func groupHeader(_ key: String) -> some View {
        Text(groupHeaderText(key))
            .mylifeFont(.sectionHeader)
            .foregroundColor(MylifePalette.gold.opacity(0.7))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, AppSpacing.m)
            .padding(.vertical, AppSpacing.s)
            .accessibilityAddTraits(.isHeader)
    }

    private func groupHeaderText(_ key: String) -> String {
        if key.count == 7 {
            let parts = key.split(separator: "-")
            if parts.count == 2, let year = Int(parts[0]), let month = Int(parts[1]) {
                return MylifeDateLabels.monthHeader(year: year, month: month)
            }
            return key
        }
        return MylifeDateLabels.yearText(Int(key) ?? 0)
    }

    @ViewBuilder
    private var searchContent: some View {
        switch viewModel.search {
        case .idle:
            if viewModel.query.trimmingCharacters(in: .whitespacesAndNewlines).count < 2 {
                MylifeLoadingHint(text: L10n.t("mylife_search_min_chars"))
            } else {
                MylifeLoadingHint(text: L10n.t("mylife_searching"))
            }
        case .loading:
            MylifeLoadingHint(text: L10n.t("mylife_searching"))
        case .empty:
            MylifeEmptyState(title: L10n.t("mylife_search_no_results"), icon: "magnifyingglass")
        case let .error(message):
            MylifeErrorHint(message: message) { viewModel.onSearchRetry() }
        case let .data(hits, total, _, _):
            SearchResultsView(
                hits: hits,
                total: total,
                query: viewModel.query,
                onHitClick: { hit in
                    searchFocused = false
                    if let nav = viewModel.onSearchHitClick(hit) {
                        onOpenEventList(nav)
                    }
                },
                onLoadMore: { viewModel.onSearchLoadMore() }
            )
        }
    }

    private func buildItems() -> [TimelineHomeListItem] {
        var items: [TimelineHomeListItem] = []
        var lastHeader: String?
        for digest in viewModel.periods {
            if let header = headerKey(digest), header != lastHeader {
                items.append(.header(header))
                lastHeader = header
            }
            items.append(.card(digest))
        }
        return items
    }

    private func headerKey(_ digest: PeriodDigest) -> String? {
        switch digest.granularity {
        case .day:
            return String(digest.key.prefix(7))
        case .week:
            guard let monday = MylifeDateLabels.mondayOfIsoWeek(digest.key) else { return nil }
            return PosixFormat.string("%04d-%02d", monday.year, monday.month)
        case .month:
            return String(digest.key.prefix(4))
        case .year:
            return nil
        }
    }
}

// MARK: - Timeline scroll self-heal support

/// Observes the UIScrollView backing the SwiftUI ScrollView so the self-heal
/// can wait until the finger is off the screen. The view itself is a zero-size,
/// invisible probe and does not affect layout.
struct ScrollInteractionObserver: UIViewRepresentable {
    let onChange: @MainActor (Bool, Bool) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onChange: onChange)
    }

    func makeUIView(context: Context) -> ProbeView {
        let view = ProbeView()
        view.onScrollViewFound = { scrollView in
            context.coordinator.attach(to: scrollView)
        }
        return view
    }

    func updateUIView(_ uiView: ProbeView, context: Context) {
        // The gesture callback is installed once, so refresh it with the
        // latest view value (notably `isActive`) and retry attaching in case
        // the scroll view was not an ancestor yet during `makeUIView`.
        context.coordinator.onChange = onChange
        uiView.attachToEnclosingScrollView()
    }

    static func dismantleUIView(_ uiView: ProbeView, coordinator: Coordinator) {
        coordinator.detach()
    }

    @MainActor
    final class Coordinator: NSObject {
        var onChange: @MainActor (Bool, Bool) -> Void
        private weak var scrollView: UIScrollView?
        private weak var observingPan: UIPanGestureRecognizer?
        private var lastDragging: Bool?
        private var lastEmittedScrollingUp: Bool?
        private var lastOffsetY: CGFloat?
        private var lastScrollingUp = false

        init(onChange: @escaping @MainActor (Bool, Bool) -> Void) {
            self.onChange = onChange
        }

        func attach(to scrollView: UIScrollView) {
            guard self.scrollView !== scrollView else { return }
            detach()
            self.scrollView = scrollView
            lastOffsetY = scrollView.contentOffset.y
            let pan = scrollView.panGestureRecognizer
            pan.addTarget(self, action: #selector(panStateChanged(_:)))
            observingPan = pan
            // Seed the current state; the probe starts conservatively as
            // "dragging" until the real state is known.
            emit(dragging: scrollView.isDragging, scrollingUp: lastScrollingUp)
        }

        func detach() {
            observingPan?.removeTarget(self, action: #selector(panStateChanged(_:)))
            observingPan = nil
            scrollView = nil
            lastDragging = nil
            lastEmittedScrollingUp = nil
            lastOffsetY = nil
            lastScrollingUp = false
        }

        @objc private func panStateChanged(_ recognizer: UIPanGestureRecognizer) {
            let dragging = recognizer.state == .began || recognizer.state == .changed
            if let scrollView {
                let offsetY = scrollView.contentOffset.y
                if recognizer.state == .began {
                    lastOffsetY = offsetY
                } else if let last = lastOffsetY {
                    let delta = offsetY - last
                    if abs(delta) > 0.5 {
                        // Offset shrinking means the content is moving back
                        // toward the top.
                        lastScrollingUp = delta < 0
                    }
                    lastOffsetY = offsetY
                }
            }
            emit(dragging: dragging, scrollingUp: lastScrollingUp)
        }

        private func emit(dragging: Bool, scrollingUp: Bool) {
            if lastDragging == dragging,
               lastEmittedScrollingUp == scrollingUp {
                return
            }
            lastDragging = dragging
            lastEmittedScrollingUp = scrollingUp
            onChange(dragging, scrollingUp)
        }
    }

    final class ProbeView: UIView {
        var onScrollViewFound: (@MainActor (UIScrollView) -> Void)?

        override func didMoveToWindow() {
            super.didMoveToWindow()
            guard window != nil else { return }
            attachToEnclosingScrollView()
        }

        func attachToEnclosingScrollView() {
            var candidate: UIView? = superview
            while let view = candidate {
                if let scrollView = view as? UIScrollView {
                    onScrollViewFound?(scrollView)
                    return
                }
                candidate = view.superview
            }
        }
    }
}

/// Main-thread-only values used by the self-heal. A reference box keeps
/// per-frame geometry updates from invalidating the SwiftUI view.
final class TimelineScrollProbe: @unchecked Sendable {
    var firstItemMinY: CGFloat?
    /// The lazy stack's origin in the scroll viewport. Negative while the
    /// user has scrolled down; positive only during a top overscroll.
    var contentMinY: CGFloat?
    var viewportHeight: CGFloat = 0
    /// Conservative default: do not self-heal until the UIKit observer has
    /// reported that the finger is off the screen.
    var isDragging = true
    /// Last significant scroll direction: true = content offset decreasing
    /// (scrolling back toward the top), false = increasing (toward bottom).
    var isScrollingUp = false
}

/// The viewport height, published by the ScrollView's background reader.
struct TimelineViewportHeightKey: PreferenceKey {
    static var defaultValue: CGFloat { 0 }

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        let next = nextValue()
        if next > 0 {
            value = next
        }
    }
}

struct TimelineContentFrameKey: PreferenceKey {
    static var defaultValue: CGRect? { nil }

    static func reduce(value: inout CGRect?, nextValue: () -> CGRect?) {
        if let next = nextValue() {
            value = next
        }
    }
}

struct TimelineFirstItemMinYKey: PreferenceKey {
    static var defaultValue: CGFloat? { nil }

    static func reduce(value: inout CGFloat?, nextValue: () -> CGFloat?) {
        if let next = nextValue() {
            value = next
        }
    }
}

/// Name of the coordinate space used to measure the list content.
enum TimelineScrollSpace {
    static let name = "timeline_scroll"
}
