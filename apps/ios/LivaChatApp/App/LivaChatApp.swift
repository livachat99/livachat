import SwiftUI

@main
struct LivaChatApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var container = AppContainer()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            #if DEBUG
            if ProcessInfo.processInfo.arguments.contains("-audioDebugHarness") {
                AudioSpikeView()
            } else {
                rootView
            }
            #else
            rootView
            #endif
        }
    }

    private var rootView: some View {
        AppRootView(container: container)
            .onChange(of: scenePhase) { phase in
                container.handleScenePhase(phase)
            }
    }
}
