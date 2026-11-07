import XCTest
@testable import LivaChatApp

final class WebrootSupportTests: XCTestCase {
    private let uuid = "01a0f54b-4fe4-7c4f-80a2-88e711556d69"
    private let uuid2 = "01a0f55c-1f2b-7e8a-8000-112233445566"
    private let base = "https://192.168.1.10:8080"

    // MARK: - Server base

    func testServerBaseFromLANWebSocketURL() {
        let server = ServerBase(
            webSocketURL: URL(string: "wss://192.168.1.10:8080/v1/ws/stream")!,
            allowsSelfSigned: true
        )
        XCTAssertEqual(server?.baseURLString, "https://192.168.1.10:8080")
        XCTAssertEqual(server?.host, "192.168.1.10")
        XCTAssertEqual(server?.port, 8080)
        XCTAssertEqual(server?.allowsSelfSigned, true)
    }

    func testServerBaseUsesDefaultPorts() {
        XCTAssertEqual(
            ServerBase(webSocketURL: URL(string: "wss://example.com/ws")!, allowsSelfSigned: false)?
                .baseURLString,
            "https://example.com:443"
        )
        XCTAssertEqual(
            ServerBase(webSocketURL: URL(string: "ws://example.com/ws")!, allowsSelfSigned: false)?
                .baseURLString,
            "http://example.com:80"
        )
    }

    func testServerBaseRejectsNonWebSocketURLs() {
        XCTAssertNil(ServerBase(webSocketURL: URL(string: "https://example.com")!, allowsSelfSigned: false))
        XCTAssertNil(ServerBase(webSocketURL: URL(string: "wss:/path")!, allowsSelfSigned: false))
    }

    func testServerBaseBracketsIPv6Host() {
        let server = ServerBase(
            webSocketURL: URL(string: "wss://[::1]:8080/ws")!,
            allowsSelfSigned: false
        )
        XCTAssertEqual(server?.baseURLString, "https://[::1]:8080")
        XCTAssertTrue(server?.isWebrootURL(URL(string: "https://[::1]:8080/webroot/\(uuid)/")!) == true)
    }

    func testWebrootURLClassification() {
        let server = ServerBase(
            webSocketURL: URL(string: "wss://192.168.1.10:8080/v1/ws/stream")!,
            allowsSelfSigned: true
        )
        XCTAssertTrue(server?.isWebrootURL(URL(string: "\(base)/webroot/\(uuid)/")!) == true)
        XCTAssertFalse(server?.isWebrootURL(URL(string: "\(base)/other/\(uuid)/")!) == true)
        XCTAssertFalse(server?.isWebrootURL(URL(string: "https://192.168.1.10:9090/webroot/\(uuid)/")!) == true)
        XCTAssertFalse(server?.isWebrootURL(URL(string: "https://example.com:8080/webroot/\(uuid)/")!) == true)
        XCTAssertFalse(server?.isWebrootURL(URL(string: "http://192.168.1.10:8080/webroot/\(uuid)/")!) == true)
    }

    // MARK: - Conversion

    func testConvertsMarkdownLinkContext() {
        XCTAssertEqual(
            LivachatURLConverter.convert("[页面](livachat_url://webroot/\(uuid)/)", serverBaseURL: base),
            "[页面](\(base)/webroot/\(uuid)/)"
        )
    }

    func testConvertsWithoutTrailingSlash() {
        XCTAssertEqual(
            LivachatURLConverter.convert("livachat_url://webroot/\(uuid)", serverBaseURL: base),
            "\(base)/webroot/\(uuid)"
        )
    }

    func testConvertsDeepPath() {
        XCTAssertEqual(
            LivachatURLConverter.convert(
                "livachat_url://webroot/\(uuid)/static/app.js",
                serverBaseURL: base
            ),
            "\(base)/webroot/\(uuid)/static/app.js"
        )
    }

    func testConvertsQuery() {
        XCTAssertEqual(
            LivachatURLConverter.convert("livachat_url://webroot/\(uuid)/?a=1", serverBaseURL: base),
            "\(base)/webroot/\(uuid)/?a=1"
        )
    }

    func testConvertsMultipleOccurrences() {
        XCTAssertEqual(
            LivachatURLConverter.convert(
                "livachat_url://webroot/\(uuid)/ 与 livachat_url://webroot/\(uuid2)/",
                serverBaseURL: base
            ),
            "\(base)/webroot/\(uuid)/ 与 \(base)/webroot/\(uuid2)/"
        )
    }

    func testSchemeAndHostMatchingIsCaseInsensitive() {
        XCTAssertEqual(
            LivachatURLConverter.convert("Livachat_Url://WebRoot/\(uuid)/", serverBaseURL: base),
            "\(base)/webroot/\(uuid)/"
        )
    }

    func testUppercaseUUIDIsPreserved() {
        XCTAssertEqual(
            LivachatURLConverter.convert(
                "livachat_url://webroot/\(uuid.uppercased())/",
                serverBaseURL: base
            ),
            "\(base)/webroot/\(uuid.uppercased())/"
        )
    }

    func testTrailingChinesePunctuationStaysOutside() {
        XCTAssertEqual(
            LivachatURLConverter.convert("见 livachat_url://webroot/\(uuid)/。", serverBaseURL: base),
            "见 \(base)/webroot/\(uuid)/。"
        )
    }

    func testTrailingASCIIPunctuationIsRestored() {
        XCTAssertEqual(
            LivachatURLConverter.convert("livachat_url://webroot/\(uuid)/,", serverBaseURL: base),
            "\(base)/webroot/\(uuid)/,"
        )
    }

    func testQuoteAndAngleBracketBoundaries() {
        XCTAssertEqual(
            LivachatURLConverter.convert("\"livachat_url://webroot/\(uuid)/\"", serverBaseURL: base),
            "\"\(base)/webroot/\(uuid)/\""
        )
        XCTAssertEqual(
            LivachatURLConverter.convert("<livachat_url://webroot/\(uuid)/>", serverBaseURL: base),
            "<\(base)/webroot/\(uuid)/>"
        )
    }

    func testInvalidTokensAreKeptAsIs() {
        let cases = [
            "livachat_url://webroot/not-a-uuid/x",
            "livachat_url://webroot/01a0f54b-4fe4-4c4f-80a2-88e711556d69/",
            "livachat_url://webroot/\(uuid)/../x",
            "livachat_url://webroot/",
            "livachat_url://audio/\(uuid)/",
            "livachat_url://webroot",
            "x livachat_url://webroot/bad/y, z"
        ]
        for text in cases {
            XCTAssertEqual(
                LivachatURLConverter.convert(text, serverBaseURL: base),
                text,
                "unexpected conversion for \(text)"
            )
        }
    }

    func testInvalidOccurrenceDoesNotBlockFollowingValidOne() {
        XCTAssertEqual(
            LivachatURLConverter.convert(
                "livachat_url://webroot/bad/x 与 livachat_url://webroot/\(uuid)/",
                serverBaseURL: base
            ),
            "livachat_url://webroot/bad/x 与 \(base)/webroot/\(uuid)/"
        )
    }

    func testEmptyBaseOrNoOccurrenceReturnsInput() {
        let text = "livachat_url://webroot/\(uuid)/"
        XCTAssertEqual(LivachatURLConverter.convert(text, serverBaseURL: ""), text)
        XCTAssertEqual(
            LivachatURLConverter.convert("普通文本 https://example.com/x", serverBaseURL: base),
            "普通文本 https://example.com/x"
        )
    }

    func testConversionIsIdempotent() {
        let once = LivachatURLConverter.convert(
            "[t](livachat_url://webroot/\(uuid)/)",
            serverBaseURL: base
        )
        XCTAssertEqual(LivachatURLConverter.convert(once, serverBaseURL: base), once)
    }
}
