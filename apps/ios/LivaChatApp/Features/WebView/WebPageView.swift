import SwiftUI
import UIKit
import WebKit

struct WebPage: Identifiable, Equatable {
    let id = UUID()
    let url: URL
    let serverHost: String
    let serverPort: Int
    let allowsSelfSigned: Bool
}

@MainActor
final class WebPageModel: ObservableObject {
    let url: URL

    @Published var progress: Double = 0
    @Published var title: String
    @Published var errorMessage: String?
    @Published var canGoBack = false

    weak var webView: WKWebView?
    var lastRequestedURL: URL

    init(url: URL) {
        self.url = url
        self.lastRequestedURL = url
        self.title = url.host ?? url.absoluteString
    }

    func retry() {
        errorMessage = nil
        progress = 0
        webView?.load(URLRequest(url: lastRequestedURL))
    }

    func goBack() {
        webView?.goBack()
    }

    func openInBrowser() {
        UIApplication.shared.open(url)
    }
}

struct WebPageView: View {
    let page: WebPage

    @StateObject private var model: WebPageModel
    @Environment(\.dismiss) private var dismiss

    init(page: WebPage) {
        self.page = page
        _model = StateObject(wrappedValue: WebPageModel(url: page.url))
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            if model.errorMessage == nil, model.progress > 0, model.progress < 1 {
                ProgressView(value: model.progress)
                    .progressViewStyle(.linear)
                    .tint(AppColor.brandIndigo)
                    .frame(height: 2)
                    .accessibilityHidden(true)
            }
            ZStack {
                WebViewContainer(page: page, model: model)
                if model.errorMessage != nil {
                    errorView
                }
            }
        }
        .background(AppColor.background.ignoresSafeArea())
        .interactiveDismissDisabled()
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            Button(action: { dismiss() }) {
                Image(systemName: "xmark")
                    .foregroundColor(AppColor.textPrimary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("common_close"))
            .accessibilityIdentifier("web_page_close")

            if model.canGoBack {
                Button(action: model.goBack) {
                    Image(systemName: "chevron.backward")
                        .foregroundColor(AppColor.textPrimary)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel(L10n.t("common_back"))
                .accessibilityIdentifier("web_page_back")
            }

            Text(model.title)
                .foregroundColor(AppColor.textPrimary)
                .appFont(.body)
                .lineLimit(2)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("web_page_title")

            Button(action: model.openInBrowser) {
                Image(systemName: "safari")
                    .foregroundColor(AppColor.textPrimary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("webview_open_in_browser"))
            .accessibilityIdentifier("web_page_open_browser")
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 52)
    }

    private var errorView: some View {
        VStack(spacing: AppSpacing.m) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 36, weight: .regular))
                .foregroundColor(AppColor.warning)
                .accessibilityHidden(true)
            Text(L10n.t("webview_cannot_open"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.title)
                .multilineTextAlignment(.center)
            Text(L10n.t("webview_load_failed"))
                .foregroundColor(AppColor.textSecondary)
                .appFont(.label)
                .multilineTextAlignment(.center)
            Button(L10n.t("common_retry"), action: model.retry)
                .foregroundColor(.white)
                .appFont(.body)
                .frame(minWidth: 200, minHeight: 48)
                .background(AppColor.brandIndigo)
                .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
                .accessibilityIdentifier("web_page_retry")
            Button(L10n.t("webview_open_in_browser")) {
                model.openInBrowser()
            }
            .foregroundColor(AppColor.brandIndigo)
            .appFont(.label)
            .frame(minHeight: 44)
            .accessibilityIdentifier("web_page_open_browser_error")
        }
        .padding(AppSpacing.xl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(AppColor.background)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("web_page_error")
    }
}

private struct WebViewContainer: UIViewRepresentable {
    let page: WebPage
    let model: WebPageModel

    func makeCoordinator() -> Coordinator {
        Coordinator(page: page, model: model)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.allowsLinkPreview = false
        webView.isOpaque = false
        webView.backgroundColor = UIColor(AppColor.background)
        webView.scrollView.backgroundColor = UIColor(AppColor.background)

        context.coordinator.attach(webView)
        AppLog.info("WebView", "load \(page.url.absoluteString)")
        webView.load(URLRequest(url: page.url))
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    static func dismantleUIView(_ uiView: WKWebView, coordinator: Coordinator) {
        coordinator.dismantle()
        uiView.navigationDelegate = nil
        uiView.uiDelegate = nil
        uiView.stopLoading()
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        private let page: WebPage
        private let model: WebPageModel
        private var observations: [NSKeyValueObservation] = []

        init(page: WebPage, model: WebPageModel) {
            self.page = page
            self.model = model
        }

        func attach(_ webView: WKWebView) {
            model.webView = webView
            observations = [
                webView.observe(\.estimatedProgress, options: [.new]) { [weak model] _, change in
                    guard let value = change.newValue else { return }
                    Task { @MainActor in model?.progress = value }
                },
                webView.observe(\.title, options: [.new]) { [weak model] _, change in
                    guard let title = change.newValue ?? nil, !title.isEmpty else { return }
                    Task { @MainActor in model?.title = title }
                },
                webView.observe(\.canGoBack, options: [.new]) { [weak model] _, change in
                    guard let value = change.newValue else { return }
                    Task { @MainActor in model?.canGoBack = value }
                }
            ]
        }

        func dismantle() {
            observations.forEach { $0.invalidate() }
            observations.removeAll()
            model.webView = nil
        }

        // MARK: - WKNavigationDelegate

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url, Self.isHTTP(url) else {
                AppLog.warn("WebView", "blocked navigation \(navigationAction.request.url?.absoluteString ?? "-")")
                decisionHandler(.cancel)
                return
            }
            if navigationAction.targetFrame?.isMainFrame != false {
                model.lastRequestedURL = url
            }
            decisionHandler(.allow)
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationResponse: WKNavigationResponse,
            decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void
        ) {
            if navigationResponse.isForMainFrame,
               let response = navigationResponse.response as? HTTPURLResponse,
               response.statusCode >= 400 {
                AppLog.error("WebView", "http \(response.statusCode) \(navigationResponse.response.url?.absoluteString ?? "-")")
                Task { @MainActor [weak model] in
                    model?.progress = 1
                    model?.errorMessage = L10n.t("webview_load_failed")
                }
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            Task { @MainActor [weak model] in
                model?.errorMessage = nil
                model?.progress = 0
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            Task { @MainActor [weak model] in
                model?.progress = 1
                model?.canGoBack = webView.canGoBack
            }
            AppLog.info("WebView", "finished \(webView.url?.absoluteString ?? "-")")
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            handle(error)
        }

        func webView(
            _ webView: WKWebView,
            didFail navigation: WKNavigation!,
            withError error: Error
        ) {
            handle(error)
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            AppLog.error("WebView", "content process terminated")
            Task { @MainActor [weak model] in
                model?.errorMessage = L10n.t("webview_load_failed")
            }
        }

        func webView(
            _ webView: WKWebView,
            respondTo challenge: URLAuthenticationChallenge
        ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
            AppLog.info(
                "WebView",
                "challenge method=\(challenge.protectionSpace.authenticationMethod) host=\(challenge.protectionSpace.host) port=\(challenge.protectionSpace.port)"
            )
            guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
                  let trust = challenge.protectionSpace.serverTrust else {
                return (.performDefaultHandling, nil)
            }

            let host = challenge.protectionSpace.host.lowercased()
            let port = challenge.protectionSpace.port
            if page.allowsSelfSigned, host == page.serverHost, port == page.serverPort {
                AppLog.warn("WebView", "accept self-signed \(host):\(port)")
                return (.useCredential, URLCredential(trust: trust))
            }
            return (.performDefaultHandling, nil)
        }

        // MARK: - WKUIDelegate

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if navigationAction.targetFrame == nil,
               let url = navigationAction.request.url,
               Self.isHTTP(url) {
                webView.load(URLRequest(url: url))
            }
            return nil
        }

        // MARK: - Helpers

        private func handle(_ error: Error) {
            let nsError = error as NSError
            guard nsError.code != NSURLErrorCancelled else { return }
            AppLog.error("WebView", "navigation failed \(nsError.code): \(nsError.localizedDescription)")
            Task { @MainActor [weak model] in
                model?.progress = 1
                model?.errorMessage = L10n.t("webview_load_failed")
            }
        }

        private static func isHTTP(_ url: URL) -> Bool {
            guard let scheme = url.scheme?.lowercased(),
                  scheme == "http" || scheme == "https" else {
                return false
            }
            return url.host?.isEmpty == false
        }
    }
}
