import Combine
import Foundation
import SafariServices
import SwiftUI
import UIKit

struct WebLink: Identifiable, Equatable {
    let id = UUID()
    let url: URL
}

enum WebLinkPolicy {
    static func isSafe(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = url.host,
              !host.isEmpty else {
            return false
        }
        return !url.absoluteString.contains(where: \.isWhitespace)
    }
}

/// Web-page presentation state owned by one presentation context (the History
/// or Timeline cover). Safari links open through `SafariLinkPresenter`; only
/// webroot links still need a SwiftUI fullScreenCover. Presenting a root-level
/// sheet while a fullScreenCover is up makes iOS dismiss the cover, so each
/// context must own its presenters instead.
@MainActor
final class InAppLinkPresenter: ObservableObject {
    @Published var webPage: WebPage?

    func showWebPage(_ page: WebPage) {
        webPage = page
    }
}

struct InAppLinkPresenters: ViewModifier {
    @ObservedObject var presenter: InAppLinkPresenter

    func body(content: Content) -> some View {
        content
            .fullScreenCover(item: $presenter.webPage) { page in
                WebPageView(page: page)
            }
    }
}

extension View {
    func inAppLinkPresenters(_ presenter: InAppLinkPresenter) -> some View {
        modifier(InAppLinkPresenters(presenter: presenter))
    }
}

/// Presents SFSafariViewController from the topmost UIKit controller.
///
/// SwiftUI's `.sheet`/`.fullScreenCover` for Safari on top of a presented
/// `fullScreenCover` makes UIKit tear the cover down when Safari closes,
/// losing the screen's navigation state. A direct UIKit presentation from the
/// cover's own controller keeps the cover mounted.
@MainActor
final class SafariLinkPresenter: NSObject, SFSafariViewControllerDelegate {
    static let shared = SafariLinkPresenter()

    func present(_ url: URL) {
        guard WebLinkPolicy.isSafe(url) else { return }
        guard let top = topViewController() else { return }
        guard !(top is SFSafariViewController) else { return }
        let safari = SFSafariViewController(url: url)
        safari.delegate = self
        top.present(safari, animated: true)
    }

    nonisolated func safariViewControllerDidFinish(_ controller: SFSafariViewController) {
        AppLog.info("WebLink", "safari did finish")
    }

    private func topViewController() -> UIViewController? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        guard let root = scene?.windows.first(where: \.isKeyWindow)?.rootViewController else {
            return nil
        }
        var top = root
        while let presented = top.presentedViewController, !presented.isBeingDismissed {
            top = presented
        }
        return top
    }
}

struct SafariView: UIViewControllerRepresentable {
    let url: URL
    var onDismiss: () -> Void = {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onDismiss: onDismiss)
    }

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let controller = SFSafariViewController(url: url)
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ uiViewController: SFSafariViewController, context: Context) {}

    @MainActor
    final class Coordinator: NSObject, SFSafariViewControllerDelegate {
        private let onDismiss: () -> Void

        init(onDismiss: @escaping () -> Void) {
            self.onDismiss = onDismiss
        }

        nonisolated func safariViewControllerDidFinish(_ controller: SFSafariViewController) {
            AppLog.info("WebLink", "safari did finish")
            MainActor.assumeIsolated {
                onDismiss()
            }
        }
    }
}
