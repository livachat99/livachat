import LivaChatSDK
import SwiftUI
import UIKit

enum AIGeneratedNotice {
    static let shownKey = "settings.ai_generated_notice_shown"
}

struct ConversationView: View {
    @ObservedObject var viewModel: ConversationViewModel
    @ObservedObject var jobStatusModel: AgentJobStatusModel
    @ObservedObject var modeModel: ConversationModeModel
    @ObservedObject var avatarModel: AvatarCustomizationModel
    let onOpenSettings: () -> Void
    let onOpenSystemSettings: () -> Void
    let onOpenHistory: () -> Void
    let onOpenTimeline: () -> Void
    let onOpenLink: (URL) -> Void
    let showAINotice: Bool

    @AppStorage(AIGeneratedNotice.shownKey) private var aiNoticeShown = false
    @State private var aiNoticeVisible = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.layoutDirection) private var layoutDirection

    private var showsToolbarCaptions: Bool { !dynamicTypeSize.isAccessibilitySize }

    /// Shared by the mic/speaker caption labels and the gallery row so all
    /// three toolbar captions sit on the same baseline.
    private static let toolbarCaptionRowHeight: CGFloat = 24

    @AccessibilityFocusState private var badgeFocused: Bool
    @AccessibilityFocusState private var avatarFocused: Bool
    @State private var scrollPolicy = ScrollFollowPolicy()
    @State private var viewerMessageID: String?
    @State private var toolbarHeight: CGFloat = 0
    @State private var streamingNeedsMaxWidth = false
    @State private var lastAutoScrollAt = Date.distantPast
    @ScaledMetric(relativeTo: .body) private var streamingBodyFontSize: CGFloat = 16
    @ScaledMetric(relativeTo: .body) private var messageBodyFontSize: CGFloat = 16

    var body: some View {
        GeometryReader { root in
            ZStack(alignment: .top) {
                VStack(spacing: 0) {
                    header(width: root.size.width)
                    if viewModel.requiresServerConfig {
                        serverConfigRequired
                    } else {
                        // Keep the message area mounted while the camera is on:
                        // destroying it would reset the conversation scroll offset.
                        ZStack(alignment: .top) {
                            VStack(spacing: 0) {
                                if avatarModel.avatarVisible {
                                    avatar
                                        // The parent VStack is already hidden
                                        // in camera mode, but keep the row
                                        // itself out of the accessibility tree
                                        // too; its badge is not rendered while
                                        // the camera is on.
                                        .accessibilityHidden(viewModel.isCameraOn)
                                }
                                messageArea
                                    .fullScreenCover(
                                        isPresented: Binding(
                                            get: { viewerMessageID != nil },
                                            set: { if !$0 { viewerMessageID = nil } }
                                        )
                                    ) {
                                        if let viewerMessageID {
                                            ImageViewerOverlay(
                                                images: imageMessages,
                                                initialMessageID: viewerMessageID,
                                                onDismiss: { self.viewerMessageID = nil }
                                            )
                                        }
                                    }
                            }
                            .opacity(viewModel.isCameraOn ? 0 : 1)
                            .allowsHitTesting(!viewModel.isCameraOn)
                            .accessibilityHidden(viewModel.isCameraOn)

                            if viewModel.isCameraOn {
                                CameraModeView(viewModel: viewModel)
                                    .transition(reduceMotion ? .identity : .opacity)
                            }
                        }
                        toolbar
                    }
                }
                .allowsHitTesting(viewModel.agentJobResultMessageID == nil)
                .accessibilityElement(
                    children: modalPresented ? .ignore : .contain
                )
                .accessibilityHidden(modalPresented)

                topOverlay

                if avatarModel.isSheetPresented {
                    AvatarCustomizationSheet(
                        model: avatarModel,
                        modeModel: modeModel,
                        isDialogPresented: modeDialogPresented
                    )
                    .zIndex(0.8)
                    .transition(reduceMotion ? .identity : .opacity)
                }

                if modeModel.isLanguagePickerPresented,
                   let target = modeModel.languagePickerTarget {
                    LanguagePickerDialog(
                        target: target,
                        onCancel: { modeModel.dismissLanguagePicker() },
                        onConfirm: { language in
                            modeModel.confirmLanguage(language)
                            avatarModel.dismissSheet()
                        }
                    )
                    .zIndex(0.9)
                }

                if modeModel.isTranscribeSourcePickerPresented {
                    TranscribeSourceDialog(
                        initialSource: modeModel.transcribeSource,
                        onCancel: { modeModel.dismissTranscribeSourcePicker() },
                        onConfirm: { source in
                            modeModel.confirmTranscribeSource(source)
                            avatarModel.dismissSheet()
                        }
                    )
                    .zIndex(0.9)
                }

                if modeModel.isOmniIntroPresented {
                    OmniIntroDialog(
                        onCancel: { modeModel.dismissOmniIntro() },
                        onEnable: {
                            modeModel.confirmOmni()
                            avatarModel.dismissSheet()
                        }
                    )
                    .zIndex(0.9)
                }

                avatarGalleryPresenter
                avatarCameraPresenter
                avatarCropPresenter

                if jobStatusModel.isPanelPresented, !jobStatusModel.jobs.isEmpty {
                    panelOverlay(rootSize: root.size)
                        .zIndex(0.5)
                        .transition(reduceMotion ? .identity : .opacity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(AppColor.background.ignoresSafeArea())
            .onPreferenceChange(ToolbarHeightKey.self) { toolbarHeight = $0 }
            .onChange(of: jobStatusModel.isPanelPresented) { presented in
                guard !presented, !jobStatusModel.jobs.isEmpty else { return }
                Task { @MainActor in
                    badgeFocused = true
                }
            }
            .animation(.easeInOut(duration: 0.2), value: viewModel.banner)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.1), value: viewModel.isCameraOn)
            .animation(
                reduceMotion ? nil : .easeInOut(duration: 0.2),
                value: avatarModel.isSheetPresented
            )
            .onChange(of: avatarModel.isSheetPresented) { presented in
                guard !presented else { return }
                Task { @MainActor in
                    avatarFocused = true
                }
            }
            .animation(
                reduceMotion ? nil : .easeInOut(duration: 0.15),
                value: jobStatusModel.isPanelPresented
            )
            .animation(
                reduceMotion ? nil : .easeInOut(duration: 0.2),
                value: jobStatusModel.jobs.isEmpty
            )
            .alert(
                L10n.t("conv_camera_unavailable"),
                isPresented: Binding(
                    get: { viewModel.cameraPermissionAlert },
                    set: { if !$0 { viewModel.dismissCameraPermissionAlert() } }
                )
            ) {
                Button(L10n.t("common_cancel"), role: .cancel) {
                    viewModel.dismissCameraPermissionAlert()
                }
                Button(L10n.t("perm_go_to_settings")) {
                    viewModel.dismissCameraPermissionAlert()
                    SystemSettingsOpener.openAppSettings()
                }
            } message: {
                Text(L10n.t("conv_camera_permission_guide"))
            }
            .sheet(
                isPresented: Binding(
                    get: { viewModel.galleryPickerPresented },
                    set: { shown in
                        if !shown, viewModel.galleryPickerPresented {
                            viewModel.handleGalleryPickerResult(nil)
                        }
                    }
                ),
                onDismiss: { viewModel.galleryPickerDismissed() }
            ) {
                PHPickerView { data in
                    viewModel.handleGalleryPickerResult(data)
                }
            }
            .fullScreenCover(
                isPresented: Binding(
                    get: { viewModel.galleryPreviewData != nil },
                    set: { _ in }
                ),
                onDismiss: { viewModel.galleryPreviewDismissed() }
            ) {
                if let data = viewModel.galleryPreviewData {
                    GalleryPreviewView(
                        data: data,
                        onBack: viewModel.galleryPreviewBack,
                        onCancel: viewModel.galleryPreviewCancel,
                        onSend: viewModel.galleryPreviewSend
                    )
                }
            }
            .agentJobResultWindow(
                owner: .conversation,
                payload: agentJobResultWindowPayload,
                onDismiss: viewModel.closeAgentJobResult
            )
            .task {
                guard showAINotice, !aiNoticeShown else { return }
                withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) {
                    aiNoticeVisible = true
                }
                UIAccessibility.post(
                    notification: .announcement,
                    argument: L10n.t("ai_generated_notice")
                )
            }
            }
    }

    private var agentJobResultWindowPayload: AgentJobResultWindowPayload? {
        guard let message = viewModel.agentJobResultMessage else { return nil }
        return AgentJobResultWindowPayload(
            text: message.text,
            cacheKey: "\(message.id):full",
            timestamp: message.timestamp,
            serverBaseURL: viewModel.serverBaseURL
        )
    }

    private var modeDialogPresented: Bool {
        modeModel.isLanguagePickerPresented
            || modeModel.isTranscribeSourcePickerPresented
            || modeModel.isOmniIntroPresented
    }

    private var modalPresented: Bool {
        jobStatusModel.isPanelPresented
            || avatarModel.isSheetPresented
            || modeDialogPresented
            || viewModel.agentJobResultMessageID != nil
    }

    private var avatarGalleryPresenter: some View {
        Color.clear
            .allowsHitTesting(false)
            .sheet(
                isPresented: Binding(
                    get: { avatarModel.route == .galleryPicker },
                    set: { shown in
                        if !shown { avatarModel.pickerDismissStarted() }
                    }
                ),
                onDismiss: { avatarModel.pickerDismissed() }
            ) {
                PHPickerView { data in
                    avatarModel.handleGalleryData(data)
                }
            }
    }

    private var avatarCameraPresenter: some View {
        Color.clear
            .allowsHitTesting(false)
            .fullScreenCover(
                isPresented: Binding(
                    get: { avatarModel.route == .camera },
                    set: { shown in
                        if !shown { avatarModel.pickerDismissStarted() }
                    }
                ),
                onDismiss: { avatarModel.pickerDismissed() }
            ) {
                CameraPhotoPicker { data in
                    avatarModel.handleCameraPhoto(data)
                }
            }
    }

    private var avatarCropPresenter: some View {
        Color.clear
            .allowsHitTesting(false)
            .fullScreenCover(
                isPresented: Binding(
                    get: { avatarModel.route == .crop },
                    set: { shown in
                        if !shown { avatarModel.cancelCrop() }
                    }
                ),
                onDismiss: { avatarModel.cancelCrop() }
            ) {
                if let data = avatarModel.cropSourceData {
                    AvatarCropView(
                        sourceData: data,
                        onCancel: { avatarModel.cancelCrop() },
                        onConfirm: { cropped in
                            Task { @MainActor in
                                await avatarModel.confirmCrop(cropped)
                            }
                        }
                    )
                } else {
                    Color.clear
                }
            }
    }

    // MARK: - Header

    private func header(width: CGFloat) -> some View {
        let hiddenAvatar = !avatarModel.avatarVisible
        let clusterLeft = width / 2 - 22
        let statusButtonMax = max(0, clusterLeft - AppSpacing.m - AppSpacing.s)
        let statusTextMax = max(0, statusButtonMax - 8 - AppSpacing.s)

        return HStack(spacing: AppSpacing.s) {
            Button(action: statusTapped) {
                HStack(spacing: AppSpacing.s) {
                    Circle()
                        .fill(statusColor)
                        .frame(width: 8, height: 8)
                    if !statusText.isEmpty {
                        Text(statusText)
                            .foregroundColor(AppColor.textSecondary)
                            .appFont(.label)
                            .lineLimit(2)
                            .frame(
                                maxWidth: hiddenAvatar ? statusTextMax : nil,
                                alignment: .leading
                            )
                    } else {
                        Text(verbatim: " ")
                            .appFont(.label)
                    }
                }
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(statusAccessibilityLabel)
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("conversation_status")

            Spacer()

            if viewModel.isCameraOn && avatarModel.avatarVisible {
                AgentJobStatusBadge(count: jobStatusModel.jobs.count) {
                    jobStatusModel.togglePanel()
                }
                .accessibilityFocused($badgeFocused)
            }

            Menu {
                Button(L10n.t("common_time_machine")) {
                    onOpenTimeline()
                }
                Button(L10n.t("conv_menu_chats")) {
                    onOpenHistory()
                }
                Button(L10n.t("conv_menu_settings")) {
                    onOpenSettings()
                }
            } label: {
                Image(systemName: "ellipsis")
                    .foregroundColor(AppColor.textPrimary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("conv_menu_more"))
            .accessibilityIdentifier("conversation_settings")
        }
        .padding(.horizontal, AppSpacing.m)
        .frame(minHeight: 44)
        .overlay {
            if hiddenAvatar {
                HStack(spacing: AppSpacing.s) {
                    AvatarTitleBarButton(
                        image: avatarModel.avatarImage,
                        mode: modeModel.activeMode,
                        onTap: { avatarModel.presentSheet() }
                    )
                    .accessibilityFocused($avatarFocused)

                    ZStack(alignment: .leading) {
                        AgentJobStatusBadge(count: jobStatusModel.jobs.count) {
                            jobStatusModel.togglePanel()
                        }
                        .accessibilityFocused($badgeFocused)
                    }
                    .frame(width: 44, height: 44, alignment: .leading)
                }
                // SwiftUI mirrors `offset(x:)` for RTL, so this centers the
                // hidden avatar in both directions.
                .offset(x: (44 + AppSpacing.s) / 2)
            }
        }
    }

    private var statusText: String {
        if viewModel.requiresServerConfig {
            return L10n.t("conv_server_config_required")
        }
        switch viewModel.connectionState {
        case .disconnected:
            return L10n.t("conv_status_disconnected")
        case .connecting, .reconnecting:
            return L10n.t("conv_status_connecting")
        case .connected:
            switch viewModel.sessionState {
            case .idle:
                return L10n.t("conv_status_idle")
            case .speaking:
                return ""
            case .listening:
                return L10n.t("conv_status_listening")
            case .thinking:
                return L10n.t("conv_status_thinking")
            }
        }
    }

    private var statusAccessibilityLabel: String {
        if viewModel.requiresServerConfig {
            return L10n.t("conv_server_config_required")
        }
        switch viewModel.connectionState {
        case .disconnected:
            return L10n.t("conv_status_disconnected")
        case .connecting, .reconnecting:
            return L10n.t("conv_status_connecting")
        case .connected:
            switch viewModel.sessionState {
            case .idle:
                return L10n.t("conv_status_idle")
            case .listening:
                return L10n.t("conv_status_listening")
            case .thinking:
                return L10n.t("conv_status_thinking")
            case .speaking:
                return L10n.t("conv_status_speaking")
            }
        }
    }

    private var statusColor: Color {
        if viewModel.requiresServerConfig { return AppColor.warning }
        switch viewModel.connectionState {
        case .disconnected:
            return AppColor.error
        case .connecting, .reconnecting:
            return AppColor.warning
        case .connected:
            switch viewModel.sessionState {
            case .idle, .speaking:
                return AppColor.success
            case .listening:
                return AppColor.info
            case .thinking:
                return AppColor.warning
            }
        }
    }

    private func statusTapped() {
        if viewModel.requiresServerConfig {
            onOpenSettings()
        } else if viewModel.connectionState == .disconnected {
            viewModel.retry()
        }
    }

    // MARK: - Avatar

    // Lift the avatar and its job badge off the message area without changing
    // layout: the row still occupies 96 pt, so the message area stays in place.
    private static let avatarYOffset: CGFloat = -AppSpacing.xs

    private var avatar: some View {
        HStack(spacing: AppSpacing.s) {
            Color.clear
                .frame(width: 64, height: 44)
                .accessibilityHidden(true)
            AvatarView(
                image: avatarModel.avatarImage,
                mode: modeModel.activeMode,
                onTap: { avatarModel.presentSheet() }
            )
            .accessibilityFocused($avatarFocused)
            ZStack(alignment: .leading) {
                // Camera mode already exposes the header badge; rendering
                // this row's badge while hidden leaves a duplicate
                // accessibility node that VoiceOver/XCUITest can focus.
                if !viewModel.isCameraOn {
                    AgentJobStatusBadge(count: jobStatusModel.jobs.count) {
                        jobStatusModel.togglePanel()
                    }
                    .accessibilityFocused($badgeFocused)
                }
            }
            .frame(width: 64, height: 44, alignment: .leading)
        }
        .frame(maxWidth: .infinity)
        .offset(y: Self.avatarYOffset)
    }

    // MARK: - Agent job panel

    private func panelOverlay(rootSize: CGSize) -> some View {
        let reserve = toolbarHeight > 0 ? toolbarHeight + AppSpacing.l : 140
        let maxHeight = max(0, rootSize.height - 180 - reserve)
        return ZStack(alignment: .top) {
            Color.clear
                .contentShape(Rectangle())
                .onTapGesture {
                    jobStatusModel.dismissPanel()
                }
                .ignoresSafeArea()
                .accessibilityHidden(true)
                .accessibilityIdentifier("agent_job_scrim")

            AgentJobListPanel(model: jobStatusModel, maxHeight: maxHeight)
                .frame(width: max(0, rootSize.width * 0.85))
                .padding(.top, 180)
        }
    }

    // MARK: - Messages

    private var imageMessages: [ChatMessage] {
        viewModel.messages.filter { $0.role == .assistant && $0.image != nil }
    }

    private func openImageViewer(messageID: String) {
        guard imageMessages.contains(where: { $0.id == messageID }) else { return }
        viewerMessageID = messageID
    }

    private var messageArea: some View {
        GeometryReader { geometry in
            let maxBubbleWidth = AppLayout.messageBubbleMaxWidth(containerWidth: geometry.size.width)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: AppSpacing.m) {
                        if showsWelcome {
                            welcome
                        }

                        ForEach(viewModel.messages) { message in
                            MessageBubbleView(
                                message: message,
                                maxBubbleWidth: maxBubbleWidth,
                                serverBaseURL: viewModel.serverBaseURL,
                                layoutDirection: layoutDirection,
                                bodyFontSize: messageBodyFontSize,
                                onOpenLink: onOpenLink,
                                onImageTap: { openImageViewer(messageID: message.id) },
                                onJobResultTap: {
                                    viewModel.openAgentJobResult(messageID: message.id)
                                }
                            )
                            .equatable()
                            .id(message.id)
                        }

                        if !modeModel.lectureActive,
                           let streaming = viewModel.streamingAIText,
                           !streaming.text.isEmpty {
                            streamingBubble(streaming.text, maxBubbleWidth: maxBubbleWidth)
                                .id("streaming")
                        }

                        Color.clear
                            .frame(height: 1)
                            .id("bottom")
                            .background {
                                GeometryReader { marker in
                                    Color.clear.preference(
                                        key: BottomOffsetKey.self,
                                        value: marker.frame(in: .named("scroll")).maxY
                                    )
                                }
                            }
                    }
                    .padding(.horizontal, AppSpacing.m)
                    .padding(.vertical, AppSpacing.s)
                }
                .coordinateSpace(name: "scroll")
                .onPreferenceChange(BottomOffsetKey.self) { maxY in
                    scrollPolicy.bottomUpdated(
                        maxY: maxY,
                        viewportHeight: geometry.size.height
                    )
                }
                .simultaneousGesture(
                    DragGesture(minimumDistance: 10).onChanged { value in
                        scrollPolicy.dragChanged(translationY: value.translation.height)
                    }
                )
                .onChange(of: viewModel.messages.count) { count in
                    if count == 0 {
                        scrollPolicy.reset()
                        AIImageDecoder.clearCache()
                        MarkdownMessageView.clearCache()
                        AgentJobCollapse.clearCache()
                    }
                    if viewModel.agentJobResultMessageID != nil,
                       viewModel.agentJobResultMessage == nil {
                        viewModel.closeAgentJobResult()
                    }
                    scrollToBottomIfNeeded(proxy, force: true)
                }
                .onChange(of: viewModel.streamingAIText?.text) { _ in
                    scrollToBottomIfNeeded(proxy)
                }
                .overlay(alignment: .bottomLeading) {
                    if !viewModel.currentTranscription.isEmpty {
                        transcriptionOverlay
                            .padding(.horizontal, AppSpacing.m)
                            .padding(.bottom, AppSpacing.s)
                    }
                }
            }
        }
    }

    private var showsWelcome: Bool {
        viewModel.messages.isEmpty
            && viewModel.currentTranscription.isEmpty
            && (viewModel.streamingAIText?.text.isEmpty ?? true)
    }

    private var welcome: some View {
        VStack(spacing: AppSpacing.s) {
            Text(L10n.t("conv_welcome_title"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.title)
                .multilineTextAlignment(.center)
            Text(L10n.t("conv_welcome_subtitle"))
                .foregroundColor(AppColor.textSecondary)
                .appFont(.label)
                .multilineTextAlignment(.center)
        }
        .padding(AppSpacing.l)
        .frame(maxWidth: .infinity)
        .background(AppColor.surface)
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
        .padding(.horizontal, AppSpacing.l)
        .padding(.top, AppSpacing.l)
        .accessibilityIdentifier("conversation_welcome")
    }

    private func streamingBubble(_ text: String, maxBubbleWidth: CGFloat) -> some View {
        let contentMaxWidth = max(0, maxBubbleWidth - AppSpacing.m * 2)
        return HStack {
            HStack(alignment: .lastTextBaseline, spacing: 2) {
                Text(text)
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.body)
                    .background {
                        GeometryReader { proxy in
                            Color.clear.preference(
                                key: MessageTextHeightKey.self,
                                value: proxy.size.height
                            )
                        }
                    }
                BlinkingCursor()
            }
            .onPreferenceChange(MessageTextHeightKey.self) { height in
                // Wrapped streaming text uses the same max width as settled
                // multi-line AI bubbles; one line keeps hugging its text.
                streamingNeedsMaxWidth = height > streamingBodyFontSize * 2.4
            }
            .frame(
                width: streamingNeedsMaxWidth ? contentMaxWidth : nil,
                alignment: .leading
            )
            .padding(.horizontal, AppSpacing.m)
            .padding(.vertical, AppSpacing.s + 2)
            .background(AppColor.surface)
            .clipShape(BubbleShape(isUser: false, isRTL: layoutDirection == .rightToLeft))
            .overlay {
                BubbleShape(isUser: false, isRTL: layoutDirection == .rightToLeft)
                    .stroke(AppColor.surfaceVariant, lineWidth: 1)
            }
            .accessibilityIdentifier("conversation_streaming_ai")
            .frame(maxWidth: maxBubbleWidth, alignment: .leading)
            Spacer(minLength: 0)
        }
    }

    private var transcriptionOverlay: some View {
        VStack(alignment: .leading, spacing: 4) {
            if modeModel.lectureActive,
               let aiText = viewModel.streamingAIText?.text,
               !aiText.isEmpty {
                Text(aiText)
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.body)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color.black.opacity(0.85))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .accessibilityIdentifier("conversation_lecture_ai")
            }
            HStack(alignment: .center, spacing: AppSpacing.s) {
                Text(viewModel.currentTranscription)
                    .foregroundColor(.white)
                    .appFont(.body)
                if !viewModel.isTranscriptionFinal {
                    TypingDots()
                }
            }
        }
        .padding(.horizontal, AppSpacing.m)
        .padding(.vertical, AppSpacing.m - 4)
        .background(AppColor.brandIndigo.opacity(0.88))
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("conversation_transcription")
    }

    private func scrollToBottomIfNeeded(_ proxy: ScrollViewProxy, force: Bool = false) {
        guard scrollPolicy.shouldAutoScroll else { return }
        let now = Date()
        if !force, now.timeIntervalSince(lastAutoScrollAt) < 0.15 { return }
        lastAutoScrollAt = now
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo("bottom", anchor: .bottom)
        }
    }

    // MARK: - Toolbar

    private var toolbar: some View {
        // Top alignment keeps the three icons on one line when a localized
        // caption wraps at larger (non-accessibility) Dynamic Type sizes.
        HStack(alignment: .top, spacing: AppSpacing.xxl) {
            micToolbarButton
            toolbarButton(
                systemImage: viewModel.isSpeakerMuted ? "speaker.slash.fill" : "speaker.wave.2.fill",
                caption: viewModel.isSpeakerMuted
                    ? L10n.t("conv_toolbar_speaker_off")
                    : L10n.t("conv_toolbar_speaker_on"),
                shortCaption: L10n.t("conv_toolbar_speaker_short"),
                active: !viewModel.isSpeakerMuted,
                identifier: "conversation_speaker",
                action: viewModel.toggleSpeaker
            )
            cameraToolbarButton
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.s)
        .padding(.bottom, AppSpacing.s)
        .background {
            GeometryReader { proxy in
                Color.clear.preference(
                    key: ToolbarHeightKey.self,
                    value: proxy.size.height
                )
            }
        }
    }

    private var micToolbarButton: some View {
        let caption = viewModel.isMicMuted
            ? L10n.t("conv_toolbar_mic_off")
            : L10n.t("conv_toolbar_mic_on")
        let shortCaption = L10n.t("conv_toolbar_mic_short")
        return Button(action: viewModel.toggleMic) {
            VStack(spacing: AppSpacing.xs) {
                ZStack {
                    Circle()
                        .fill(Color.clear)
                        .overlay {
                            Circle()
                                .stroke(
                                    viewModel.isMicMuted ? AppColor.surfaceVariant : Color.clear,
                                    lineWidth: 1
                                )
                        }
                        .frame(width: 48, height: 48)
                        .transaction { $0.animation = nil }

                    // Keep the on/off transaction off the glyph so its bar
                    // color animation is not suppressed.
                    if viewModel.isMicMuted {
                        Image(systemName: "mic.slash.fill")
                            .foregroundColor(AppColor.textSecondary)
                            .transaction { $0.animation = nil }
                    } else {
                        MicVolumeGlyph(model: viewModel.micMeter)
                    }
                }
                if showsToolbarCaptions {
                    Text(shortCaption)
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.caption)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        // Match the gallery row's min height so all three
                        // captions share one text baseline.
                        .frame(minWidth: 0, maxWidth: 76, minHeight: Self.toolbarCaptionRowHeight)
                        .minimumScaleFactor(0.8)
                }
            }
            .frame(minWidth: 64, minHeight: 44)
            // The animated glyph has transparent gaps; without an explicit
            // content shape only the drawn strokes are tappable.
            .contentShape(Rectangle())
        }
        .buttonStyle(ToolbarPressStyle())
        .accessibilityIdentifier("conversation_mic")
        .accessibilityLabel(caption)
    }

    private var cameraToolbarButton: some View {
        let caption = viewModel.isCameraOn
            ? L10n.t("conv_toolbar_camera_on")
            : L10n.t("conv_toolbar_camera_off")
        return VStack(spacing: AppSpacing.xs) {
            Button(action: viewModel.toggleCamera) {
                ZStack {
                    // Android parity: active state is conveyed by the icon
                    // tint, not a filled circle background.
                    Circle()
                        .fill(Color.clear)
                        .overlay {
                            Circle()
                                .stroke(
                                    viewModel.isCameraOn ? Color.clear : AppColor.surfaceVariant,
                                    lineWidth: 1
                                )
                        }
                        .frame(width: 48, height: 48)
                    CameraToolbarIcon(isOn: viewModel.isCameraOn)
                }
                // State changes are instant; only press scale animates.
                .transaction { $0.animation = nil }
                .frame(minWidth: 64, minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(ToolbarPressStyle())
            .accessibilityIdentifier("conversation_camera")
            .accessibilityLabel(caption)

            Button(action: { viewModel.openGalleryPicker() }) {
                HStack(spacing: 2) {
                    if !viewModel.isCameraOn {
                        Image(systemName: "plus")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(AppColor.textSecondary)
                    }
                    if showsToolbarCaptions {
                        Text(L10n.t("conv_toolbar_camera_short"))
                            .foregroundColor(AppColor.textSecondary)
                            .appFont(.caption)
                    }
                }
                .frame(minHeight: Self.toolbarCaptionRowHeight)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(viewModel.isCameraOn)
            .accessibilityIdentifier("conversation_gallery")
            .accessibilityLabel(L10n.t("conv_avatar_from_gallery"))
            .accessibilityHidden(viewModel.isCameraOn)
        }
    }

    private func toolbarButton(
        systemImage: String,
        caption: String,
        shortCaption: String,
        active: Bool,
        identifier: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(spacing: AppSpacing.xs) {
                ZStack {
                    // Android parity: active state tints the icon; no filled
                    // circle background.
                    Circle()
                        .fill(Color.clear)
                        .overlay {
                            Circle()
                                .stroke(
                                    active ? Color.clear : AppColor.surfaceVariant,
                                    lineWidth: 1
                                )
                        }
                        .frame(width: 48, height: 48)
                    Image(systemName: systemImage)
                        .foregroundColor(active ? AppColor.brandIndigo : AppColor.textSecondary)
                }
                // State changes are instant; only press scale animates.
                .transaction { $0.animation = nil }
                if showsToolbarCaptions {
                    Text(shortCaption)
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.caption)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        // Match the gallery row's min height so all three
                        // captions share one text baseline.
                        .frame(minWidth: 0, maxWidth: 76, minHeight: Self.toolbarCaptionRowHeight)
                        .minimumScaleFactor(0.8)
                }
            }
            .frame(minWidth: 64, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(ToolbarPressStyle())
        .accessibilityIdentifier(identifier)
        .accessibilityLabel(caption)
    }

    // MARK: - Banners / blocking state

    private var topOverlay: some View {
        VStack(spacing: AppSpacing.s) {
            if let banner = viewModel.banner {
                bannerView(banner)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
            if let error = avatarModel.errorMessage {
                avatarErrorView(error)
            }
            if aiNoticeVisible {
                aiNoticeView
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .padding(.top, 52)
        .zIndex(0.7)
        .opacity(modalPresented ? 0 : 1)
        .allowsHitTesting(!modalPresented && viewModel.agentJobResultMessageID == nil)
        .accessibilityElement(children: modalPresented ? .ignore : .contain)
        .accessibilityHidden(modalPresented)
    }

    private var aiNoticeView: some View {
        HStack(alignment: .top, spacing: AppSpacing.m) {
            Text(L10n.t("ai_generated_notice"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.label)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("ai_generated_notice")

            Button(action: dismissAINotice) {
                Image(systemName: "xmark")
                    .foregroundColor(AppColor.textSecondary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("common_close"))
            .accessibilityIdentifier("ai_generated_notice_close")
        }
        .padding(.leading, AppSpacing.m)
        .padding(.vertical, AppSpacing.xs)
        .background(AppColor.surface)
        .overlay {
            RoundedRectangle(cornerRadius: AppRadius.m)
                .stroke(AppColor.surfaceVariant, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .padding(.horizontal, AppSpacing.m)
        .accessibilityElement(children: .contain)
    }

    private func dismissAINotice() {
        aiNoticeShown = true
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) {
            aiNoticeVisible = false
        }
    }

    private var serverConfigRequired: some View {
        VStack(spacing: AppSpacing.m) {
            Spacer()
            Image(systemName: "server.rack")
                .font(.system(size: 40, weight: .regular))
                .foregroundColor(AppColor.warning)
            Text(L10n.t("conv_server_config_required"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.title)
            Text(L10n.t("conv_server_config_hint"))
                .foregroundColor(AppColor.textSecondary)
                .appFont(.label)
                .multilineTextAlignment(.center)
            Button(action: onOpenSettings) {
                Text(L10n.t("conv_menu_settings"))
                    .foregroundColor(.white)
                    .appFont(.body)
                    .frame(minWidth: 200, minHeight: 52)
                    .background(AppColor.brandIndigo)
                    .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
            }
            Spacer()
        }
        .padding(AppSpacing.l)
        .accessibilityIdentifier("conversation_server_config_required")
    }

    private func bannerView(_ banner: ConversationBanner) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.s) {
            Text(L10n.bannerTitle(banner.message))
                .foregroundColor(.white)
                .appFont(.label)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: AppSpacing.m) {
                if banner.action == .retry {
                    Button(L10n.t("common_retry")) {
                        viewModel.retry()
                    }
                    .foregroundColor(.white)
                    .appFont(.label)
                    .frame(minHeight: 44)
                }
                if banner.action == .openSettings {
                    Button(L10n.t("conv_menu_settings")) {
                        onOpenSettings()
                    }
                    .foregroundColor(.white)
                    .appFont(.label)
                    .frame(minHeight: 44)
                }
                if banner.action == .openSystemSettings {
                    Button(L10n.t("perm_go_to_settings")) {
                        onOpenSystemSettings()
                    }
                    .foregroundColor(.white)
                    .appFont(.label)
                    .frame(minHeight: 44)
                }
                Spacer()
                Button(action: viewModel.dismissBanner) {
                    Image(systemName: "xmark")
                        .foregroundColor(.white)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel(L10n.t("common_close"))
            }
        }
        .padding(.horizontal, AppSpacing.m)
        .padding(.vertical, AppSpacing.s)
        .background(banner.kind == .warning ? AppColor.warning : AppColor.error)
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .padding(.horizontal, AppSpacing.m)
        .accessibilityIdentifier("conversation_banner")
    }

    private func avatarErrorView(_ message: String) -> some View {
        HStack(spacing: AppSpacing.m) {
            Text(message)
                .foregroundColor(.white)
                .appFont(.label)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: { avatarModel.clearError() }) {
                Image(systemName: "xmark")
                    .foregroundColor(.white)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("common_close"))
        }
        .padding(.leading, AppSpacing.m)
        .padding(.vertical, AppSpacing.xs)
        .background(AppColor.warning)
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .padding(.horizontal, AppSpacing.m)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("avatar_error_banner")
    }
}

private struct ToolbarHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private struct BottomOffsetKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct ToolbarPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.92 : 1.0)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

private struct CameraToolbarIcon: View {
    let isOn: Bool

    var body: some View {
        ZStack {
            Image(systemName: "camera.fill")
                .foregroundColor(isOn ? AppColor.brandIndigo : AppColor.textSecondary)

            if !isOn {
                ZStack {
                    Capsule()
                        .fill(AppColor.background)
                        .frame(width: 28, height: 5)
                    Capsule()
                        .fill(AppColor.textSecondary)
                        .frame(width: 28, height: 1.5)
                }
                .rotationEffect(.degrees(45))
            }
        }
        .accessibilityHidden(true)
    }
}

private struct TypingDots: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var animating = false

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(Color.white)
                    .frame(width: 4, height: 4)
                    .opacity(animating ? 1.0 : 0.3)
                    .animation(
                        reduceMotion
                            ? nil
                            : .easeInOut(duration: 0.6)
                                .repeatForever(autoreverses: true)
                                .delay(Double(index) * 0.15),
                        value: animating
                    )
            }
        }
        .onAppear {
            guard !reduceMotion else { return }
            animating = true
        }
    }
}

private struct BlinkingCursor: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var visible = true

    var body: some View {
        Rectangle()
            .fill(AppColor.textPrimary)
            .frame(width: 2, height: 18)
            .opacity(visible ? 1 : 0)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) {
                    visible = false
                }
            }
    }
}
