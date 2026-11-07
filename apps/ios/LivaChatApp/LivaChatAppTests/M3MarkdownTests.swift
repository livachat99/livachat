import LivaChatSDK
import XCTest
@testable import LivaChatApp

@MainActor
private final class StubM3Permission: MicPermissionProviding {
    func current() -> MicPermissionState { .granted }
    func request(_ completion: @escaping @MainActor (MicPermissionState) -> Void) {
        completion(.granted)
    }
}

@MainActor
final class M3MarkdownTests: XCTestCase {
    // MARK: - Table fallback

    func testTableRewriteOnLegacyRendererWrapsCompleteTable() {
        let markdown = """
        Intro

        | City | Days |
        | --- | --- |
        | Paris | 3 |
        | Rome | 2 |

        Outro
        """

        let rewritten = MarkdownTableFallback.rewrite(markdown, tablesSupported: false)

        XCTAssertTrue(rewritten.contains("```text"))
        XCTAssertTrue(rewritten.contains("| City | Days |"))
        XCTAssertTrue(rewritten.contains("| Rome | 2 |"))
        XCTAssertTrue(rewritten.contains("Intro"))
        XCTAssertTrue(rewritten.contains("Outro"))
        XCTAssertEqual(rewritten.components(separatedBy: "```").count, 3)
    }

    func testTableRewriteIsNoOpWhenTablesSupported() {
        let markdown = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        XCTAssertEqual(MarkdownTableFallback.rewrite(markdown, tablesSupported: true), markdown)
    }

    func testTableRewriteIgnoresPipesWithoutDelimiterRow() {
        let markdown = "a | b\nplain text\n---\n| not | a table |"
        XCTAssertEqual(MarkdownTableFallback.rewrite(markdown, tablesSupported: false), markdown)
    }

    func testTableRewriteSkipsTablesInsideFences() {
        let markdown = """
        ```text
        | City | Days |
        | --- | --- |
        | Paris | 3 |
        ```
        """

        XCTAssertEqual(MarkdownTableFallback.rewrite(markdown, tablesSupported: false), markdown)
        XCTAssertFalse(MarkdownTableFallback.containsTable(markdown))
    }

    func testTableRewriteHandlesLongDelimiterRowWithoutCrashing() {
        let delimiter = "|" + String(repeating: "-", count: 50_000) + "|"
        let markdown = "| A |\n\(delimiter)\n| 1 |"
        XCTAssertTrue(MarkdownTableFallback.containsTable(markdown))
        XCTAssertTrue(MarkdownTableFallback.rewrite(markdown, tablesSupported: false).contains("```text"))
    }

    func testContainsTableFindsHeaderAndDelimiter() {
        XCTAssertTrue(MarkdownTableFallback.containsTable("| A | B |\n| --- | --- |\n| 1 | 2 |"))
        XCTAssertFalse(MarkdownTableFallback.containsTable("Just a paragraph"))
        XCTAssertFalse(MarkdownTableFallback.containsTable("a | b\nno delimiter"))
    }

    // MARK: - URL policy

    func testWebLinkPolicyAllowsHTTPAndHTTPS() {
        XCTAssertTrue(WebLinkPolicy.isSafe(URL(string: "https://example.com/path")!))
        XCTAssertTrue(WebLinkPolicy.isSafe(URL(string: "HTTP://EXAMPLE.COM")!))
        XCTAssertTrue(WebLinkPolicy.isSafe(URL(string: "http://127.0.0.1:8080")!))
    }

    func testWebLinkPolicyRejectsUnsafeSchemesAndEmptyHosts() {
        XCTAssertFalse(WebLinkPolicy.isSafe(URL(string: "javascript:alert(1)")!))
        XCTAssertFalse(WebLinkPolicy.isSafe(URL(string: "file:///etc/passwd")!))
        XCTAssertFalse(WebLinkPolicy.isSafe(URL(string: "mailto:user@example.com")!))
        XCTAssertFalse(WebLinkPolicy.isSafe(URL(string: "tel:12345")!))
        XCTAssertFalse(WebLinkPolicy.isSafe(URL(string: "data:text/html,hi")!))
        XCTAssertFalse(WebLinkPolicy.isSafe(URL(string: "https://")!))
    }

    // MARK: - Agent detection

    func testAgentJobResultDetectionUsesIDPrefix() {
        XCTAssertTrue(makeMessage(id: "ai-msg_agent_job_42").isAgentJobResult)
        XCTAssertFalse(makeMessage(id: "ai-msg_agent_prompt").isAgentJobResult)
        XCTAssertFalse(makeMessage(id: "ai-msg_llm_42").isAgentJobResult)
        XCTAssertFalse(makeMessage(id: "user-ai-msg_agent_job_42").isAgentJobResult)
    }

    func testAssistantSourceIsStoredOnMessage() {
        let viewModel = ConversationViewModel(permissionProvider: StubM3Permission())
        viewModel.finishAssistantMessage(
            messageID: "msg_agent_job_1",
            text: "done",
            source: .agent
        )
        XCTAssertEqual(viewModel.messages.first?.source, .agent)
        XCTAssertTrue(viewModel.messages.first?.isAgentJobResult == true)
    }

    func testViewModelOpensOnlyJobResultMessagesAndClearsOnSessionEnd() {
        let viewModel = ConversationViewModel(permissionProvider: StubM3Permission())
        viewModel.appendMessage(makeMessage(id: "ai-msg_agent_job_1"))
        viewModel.appendMessage(makeMessage(id: "ai-msg_agent_prompt"))

        viewModel.openAgentJobResult(messageID: "ai-msg_agent_prompt")
        XCTAssertNil(viewModel.agentJobResultMessageID)

        viewModel.openAgentJobResult(messageID: "ai-msg_agent_job_1")
        XCTAssertEqual(viewModel.agentJobResultMessageID, "ai-msg_agent_job_1")
        XCTAssertEqual(viewModel.agentJobResultMessage?.id, "ai-msg_agent_job_1")

        viewModel.setSessionEnded(true)
        XCTAssertNil(viewModel.agentJobResultMessageID)
    }

    func testViewModelClearsAgentResultOnConversationClear() {
        let viewModel = ConversationViewModel(permissionProvider: StubM3Permission())
        viewModel.appendMessage(makeMessage(id: "ai-msg_agent_job_1"))
        viewModel.openAgentJobResult(messageID: "ai-msg_agent_job_1")
        XCTAssertNotNil(viewModel.agentJobResultMessageID)

        viewModel.clearConversation()
        XCTAssertNil(viewModel.agentJobResultMessageID)
        XCTAssertTrue(viewModel.messages.isEmpty)
    }

    // MARK: - Collapse

    func testShortMarkdownDoesNotCollapse() {
        XCTAssertNil(AgentJobCollapse.collapse(String(repeating: "a", count: 399)))
    }

    func testCollapsePrefersLastSafeBoundaryInVisibleWindow() {
        let markdown = makeParagraphs(count: 20, length: 70)
        let collapsed = AgentJobCollapse.collapse(markdown)

        XCTAssertNotNil(collapsed)
        guard let collapsed else { return }
        XCTAssertGreaterThanOrEqual(collapsed.prefix.count, AgentJobCollapse.minVisibleChars)
        XCTAssertLessThanOrEqual(collapsed.prefix.count, AgentJobCollapse.minChars)
        XCTAssertEqual(
            collapsed.remainingCharacters,
            markdown.count - collapsed.prefix.count
        )
        XCTAssertFalse(collapsed.prefix.hasSuffix("\n"))
    }

    func testCollapseFallsBackToFirstBoundaryAfterThreshold() {
        let early = String(repeating: "x", count: 200)
        let middle = String(repeating: "y", count: 250)
        let late = String(repeating: "z", count: 200)
        let markdown = early + "\n\n" + middle + "\n\n" + late

        let collapsed = AgentJobCollapse.collapse(markdown)

        XCTAssertNotNil(collapsed)
        guard let collapsed else { return }
        XCTAssertEqual(collapsed.prefix, early + "\n\n" + middle)
        XCTAssertEqual(collapsed.remainingCharacters, markdown.count - collapsed.prefix.count)
    }

    func testCollapseSkipsTinyEarlyBoundaryAndDoesNotUseLineEndings() {
        let early = "Short intro"
        let longLine = String(repeating: "x", count: 2_000)
        let markdown = early + "\n\n" + longLine

        XCTAssertNil(AgentJobCollapse.collapse(markdown))
    }

    func testCollapseIgnoresBlankLinesInsideFence() {
        let intro = String(repeating: "a", count: 100)
        var codeLines: [String] = ["```"]
        for index in 0..<40 {
            codeLines.append("let value\(index) = \(index)")
            codeLines.append("")
        }
        codeLines.append("```")
        let tail = String(repeating: "b", count: 200)
        let markdown = intro + "\n\n" + codeLines.joined(separator: "\n") + "\n" + tail

        XCTAssertGreaterThanOrEqual(markdown.count, AgentJobCollapse.minChars)
        XCTAssertNil(AgentJobCollapse.collapse(markdown))
    }

    func testCollapseDoesNotUseRowsInsideTableAsBoundaries() {
        let intro = String(repeating: "a", count: 100)
        var tableLines = ["| A | B |", "| --- | --- |"]
        for index in 0..<60 {
            tableLines.append("| \(index) | value |")
        }
        let markdown = intro + "\n\n" + tableLines.joined(separator: "\n")

        XCTAssertGreaterThanOrEqual(markdown.count, AgentJobCollapse.minChars)
        XCTAssertNil(AgentJobCollapse.collapse(markdown))
    }

    func testCollapseReturnsNilWithoutSafeBoundary() {
        let markdown = String(repeating: "line without blank separators\n", count: 60)
        XCTAssertGreaterThanOrEqual(markdown.count, AgentJobCollapse.minChars)
        XCTAssertNil(AgentJobCollapse.collapse(markdown))
    }

    func testCollapseIgnoresTrailingNewlineAsBoundary() {
        let markdown = String(repeating: "a", count: 500) + "\n"
        XCTAssertNil(AgentJobCollapse.collapse(markdown))
    }

    func testCollapseHandlesCRLFParagraphs() {
        let paragraphs = (1...12).map { index in
            "Paragraph \(index) " + String(repeating: "x", count: 50)
        }
        let markdown = paragraphs.joined(separator: "\r\n\r\n")

        guard let collapsed = AgentJobCollapse.collapse(markdown) else {
            return XCTFail("expected CRLF markdown to collapse")
        }
        XCTAssertFalse(collapsed.prefix.hasSuffix("\r"))
        XCTAssertEqual(collapsed.remainingCharacters, markdown.count - collapsed.prefix.count)
    }

    func testCollapsePrefixRemainsValidMarkdownAndFullTextPreserved() {
        let markdown = "# Heading\n\n" + makeParagraphs(count: 20, length: 80)
        guard let collapsed = AgentJobCollapse.collapse(markdown) else {
            return XCTFail("expected collapse")
        }
        XCTAssertTrue(collapsed.prefix.hasPrefix("# Heading"))
        XCTAssertEqual(collapsed.remainingCharacters, markdown.count - collapsed.prefix.count)
        XCTAssertFalse(collapsed.prefix.contains("\u{2026}"))
    }

    func testCollapseCacheReturnsSameResult() {
        let markdown = makeParagraphs(count: 20, length: 70)
        let first = AgentJobCollapse.collapsed(markdown: markdown, key: "message-1")
        let second = AgentJobCollapse.collapsed(markdown: markdown, key: "message-1")
        XCTAssertEqual(first, second)
    }

    // MARK: - Mode suffix

    func testModeSuffixKeyByModeAndRole() {
        XCTAssertNil(MessageModeSuffix.key(mode: nil, role: .user))
        XCTAssertEqual(
            MessageModeSuffix.key(mode: .transcribe, role: .user),
            "conv_msg_suffix_transcript"
        )
        XCTAssertEqual(
            MessageModeSuffix.key(mode: .translate, role: .user),
            "conv_msg_suffix_original"
        )
        XCTAssertEqual(
            MessageModeSuffix.key(mode: .translate, role: .assistant),
            "conv_msg_suffix_translation"
        )
    }

    func testModeSuffixAppend() {
        XCTAssertEqual(
            MessageModeSuffix.appended("hello", suffix: "(transcript)"),
            "hello (transcript)"
        )
        XCTAssertEqual(MessageModeSuffix.appended("hello", suffix: nil), "hello")
        XCTAssertEqual(MessageModeSuffix.appended("", suffix: "(transcript)"), "")
    }

    private func makeMessage(id: String) -> ChatMessage {
        ChatMessage(
            id: id,
            role: .assistant,
            text: "done",
            isFinal: true,
            timestamp: Date(),
            source: .agent
        )
    }

    private func makeParagraphs(count: Int, length: Int) -> String {
        (1...count)
            .map { index in
                let prefix = "Paragraph \(index) "
                return prefix + String(repeating: "x", count: max(1, length - prefix.count))
            }
            .joined(separator: "\n\n")
    }
}
