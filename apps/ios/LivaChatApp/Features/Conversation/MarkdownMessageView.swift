import MarkdownUI
import SwiftUI

struct MarkdownMessageView: View {
    let markdown: String
    let cacheKey: String
    let serverBaseURL: String
    let onOpenLink: (URL) -> Void

    @State private var content: MarkdownContent
    @ScaledMetric(relativeTo: .body) private var bodyFontSize: CGFloat = 16

    private final class ContentBox {
        let content: MarkdownContent

        init(_ content: MarkdownContent) {
            self.content = content
        }
    }

    nonisolated(unsafe) private static let cache: NSCache<NSString, ContentBox> = {
        let cache = NSCache<NSString, ContentBox>()
        cache.countLimit = 50
        return cache
    }()

    /// Caches the URL conversion + table rewriting by raw markdown. SwiftUI
    /// re-initializes this view on every parent body evaluation; without this
    /// cache each settled bubble would redo the conversion for every
    /// streaming token, even though the content is unchanged.
    nonisolated(unsafe) private static let preparedCache: NSCache<NSString, NSString> = {
        let cache = NSCache<NSString, NSString>()
        cache.countLimit = 64
        return cache
    }()

    private static let tablesSupported: Bool = {
        if #available(iOS 16.0, *) { return true }
        return false
    }()

    init(
        markdown: String,
        cacheKey: String,
        serverBaseURL: String,
        onOpenLink: @escaping (URL) -> Void
    ) {
        self.markdown = markdown
        self.cacheKey = cacheKey
        self.serverBaseURL = serverBaseURL
        self.onOpenLink = onOpenLink
        _content = State(
            initialValue: Self.content(
                for: markdown,
                cacheKey: cacheKey,
                serverBaseURL: serverBaseURL
            )
        )
    }

    var body: some View {
        Markdown(content)
            .markdownTheme(MarkdownThemeFactory.theme(bodyFontSize: bodyFontSize))
            .markdownSoftBreakMode(.lineBreak)
            .markdownImageProvider(NoNetworkImageProvider())
            .markdownInlineImageProvider(NoNetworkInlineImageProvider())
            .environment(\.openURL, OpenURLAction { url in
                guard WebLinkPolicy.isSafe(url) else { return .discarded }
                onOpenLink(url)
                return .handled
            })
            .onChange(of: markdown) { newValue in
                content = Self.content(
                    for: newValue,
                    cacheKey: cacheKey,
                    serverBaseURL: serverBaseURL
                )
            }
            .onChange(of: serverBaseURL) { newValue in
                content = Self.content(
                    for: markdown,
                    cacheKey: cacheKey,
                    serverBaseURL: newValue
                )
            }
    }

    static func clearCache() {
        cache.removeAllObjects()
        preparedCache.removeAllObjects()
    }

    private static func content(
        for markdown: String,
        cacheKey: String,
        serverBaseURL: String
    ) -> MarkdownContent {
        let preparedKey = "\(serverBaseURL)\u{1F}\(markdown)" as NSString
        let prepared: String
        if let cached = preparedCache.object(forKey: preparedKey) {
            prepared = cached as String
        } else {
            let converted = LivachatURLConverter.convert(markdown, serverBaseURL: serverBaseURL)
            prepared = MarkdownTableFallback.rewrite(converted, tablesSupported: tablesSupported)
            preparedCache.setObject(prepared as NSString, forKey: preparedKey)
        }

        let key = "\(cacheKey):\(prepared.hashValue)" as NSString
        if let cached = cache.object(forKey: key) {
            return cached.content
        }
        let content = MarkdownContent(prepared)
        cache.setObject(ContentBox(content), forKey: key)
        return content
    }
}

private struct NoNetworkImageProvider: ImageProvider {
    func makeImage(url: URL?) -> some View {
        EmptyView()
    }
}

private struct NoNetworkInlineImageProvider: InlineImageProvider {
    func image(with url: URL, label: String) async throws -> Image {
        throw URLError(.unsupportedURL)
    }
}
