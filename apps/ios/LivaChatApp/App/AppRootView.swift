import SwiftUI

struct AppRootView: View {
    @ObservedObject var container: AppContainer
    @ObservedObject var router: AppRouter

    init(container: AppContainer) {
        self.container = container
        self.router = container.router
    }

    var body: some View {
        ZStack {
            AppColor.background.ignoresSafeArea()
            content
                .frame(maxWidth: 600)
                .frame(maxWidth: .infinity)
                .transition(.opacity)
            AgentJobResultWindowHost(
                center: container.agentJobResultCenter,
                owner: .conversation,
                onOpenLink: { container.openWebLink($0) }
            )
            .zIndex(1)
        }
        .environmentObject(container.agentJobResultCenter)
        .appLocalizationEnvironment()
        .animation(.easeInOut(duration: 0.25), value: router.phase)
        .preferredColorScheme(.dark)
        .task {
            await container.bootstrap()
        }
        .fullScreenCover(isPresented: $router.showSettings) {
            if let viewModel = container.settingsViewModel {
                SettingsView(viewModel: viewModel)
                    .appLocalizationEnvironment()
            } else {
                AppColor.background.ignoresSafeArea()
            }
        }
        .sheet(item: $container.presentedWebLink) { link in
            SafariView(url: link.url, onDismiss: { container.presentedWebLink = nil })
                .ignoresSafeArea()
        }
        .fullScreenCover(item: $container.presentedWebPage) { page in
            WebPageView(page: page)
                .appLocalizationEnvironment()
        }
        .fullScreenCover(isPresented: $router.showHistory) {
            HistoryCoverView(container: container)
                .appLocalizationEnvironment()
        }
        .fullScreenCover(isPresented: $router.showTimeline) {
            TimelineCoverView(container: container)
                .appLocalizationEnvironment()
        }
    }

    @ViewBuilder
    private var content: some View {
        switch router.phase {
        case .splash:
            SplashView()
        case .permission:
            PermissionView(container: container)
        case .conversation:
            if let viewModel = container.conversationViewModel {
                ConversationView(
                    viewModel: viewModel,
                    jobStatusModel: container.agentJobStatusModel,
                    modeModel: container.modeModel,
                    avatarModel: container.avatarModel,
                    onOpenSettings: { container.openSettings() },
                    onOpenSystemSettings: { container.openSystemSettings() },
                    onOpenHistory: { container.openHistory() },
                    onOpenTimeline: { container.openTimeline() },
                    onOpenLink: { container.openWebLink($0) },
                    showAINotice: container.launchOptions.showAINotice
                )
            } else {
                AppColor.background.ignoresSafeArea()
            }
        }
    }
}

/// History cover with its own link presenter. SwiftUI link presenters
/// mounted at the root make iOS dismiss a presented fullScreenCover when
/// Safari closes, dropping the user's navigation and the agent result viewer;
/// every presentation context must own its link presenters.
private struct HistoryCoverView: View {
    @ObservedObject var container: AppContainer
    @StateObject private var linkPresenter = InAppLinkPresenter()

    var body: some View {
        ZStack {
            HistoryFlowView(
                service: container.historyService,
                serverBaseURL: container.currentServerBaseURL,
                onOpenLink: { container.presentWebLink($0, using: linkPresenter) }
            )
            .frame(maxWidth: 600)
            .frame(maxWidth: .infinity)
            .background(AppColor.background.ignoresSafeArea())
            .preferredColorScheme(.dark)

            AgentJobResultWindowHost(
                center: container.agentJobResultCenter,
                owner: .history,
                onOpenLink: { container.presentWebLink($0, using: linkPresenter) }
            )
            .zIndex(1)
        }
        .background(AppColor.background.ignoresSafeArea())
        .environmentObject(container.agentJobResultCenter)
        .inAppLinkPresenters(linkPresenter)
    }
}

/// Timeline cover with a cover-local link presenter (see HistoryCoverView).
private struct TimelineCoverView: View {
    @ObservedObject var container: AppContainer
    @StateObject private var linkPresenter = InAppLinkPresenter()

    var body: some View {
        if let store = container.mylifeStore,
           let imageLoader = container.mylifeImageLoader {
            ZStack {
                TimelineFlowView(
                    store: store,
                    serverBaseURL: container.currentServerBaseURL,
                    realtimeSpeaking: container.realtimeSpeakingPublisher,
                    mediaEngineProvider: { [weak container] in container?.timelineMediaEngine },
                    imageLoader: imageLoader,
                    onOpenLink: { container.presentWebLink($0, using: linkPresenter) }
                )

                AgentJobResultWindowHost(
                    center: container.agentJobResultCenter,
                    owner: .timeline,
                    onOpenLink: { container.presentWebLink($0, using: linkPresenter) }
                )
                .zIndex(1)
            }
            .environmentObject(container.agentJobResultCenter)
            .inAppLinkPresenters(linkPresenter)
        } else {
            AppColor.background.ignoresSafeArea()
        }
    }
}

/// Renders the agent job result at the host screen's window level, outside
/// the 600pt content column. Conversation mounts it at AppRootView; History
/// and Timeline mount it inside their fullScreenCovers so the viewer stays
/// above those covers while still using the full window width.
private struct AgentJobResultWindowHost: View {
    @ObservedObject var center: AgentJobResultWindowCenter
    let owner: AgentJobResultOwner
    let onOpenLink: (URL) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var presentation: AgentJobResultWindowCenter.Presentation? {
        guard let presentation = center.presentation, presentation.owner == owner else {
            return nil
        }
        return presentation
    }

    var body: some View {
        ZStack {
            if let presentation {
                AgentJobResultOverlay(
                    text: presentation.payload.text,
                    cacheKey: presentation.payload.cacheKey,
                    timestamp: presentation.payload.timestamp,
                    serverBaseURL: presentation.payload.serverBaseURL,
                    onDismiss: {
                        guard center.presentation?.id == presentation.id else { return }
                        center.dismiss(id: presentation.id)
                        presentation.onDismiss()
                    },
                    onOpenLink: onOpenLink
                )
                .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(presentation != nil)
        .animation(
            reduceMotion ? nil : .easeInOut(duration: 0.2),
            value: center.presentation?.id
        )
    }
}
