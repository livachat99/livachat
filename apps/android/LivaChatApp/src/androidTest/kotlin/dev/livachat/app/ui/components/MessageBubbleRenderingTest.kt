package dev.livachat.app.ui.components

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.ui.theme.LivaChatTheme
import org.hamcrest.CoreMatchers.containsString
import org.hamcrest.CoreMatchers.not
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant

/**
 * MessageBubble 分支行为（设计 §4.4）。
 * 纯文本分支是 Compose Text（compose 语义匹配）；Markdown 分支是 AndroidView 内 TextView（espresso 视图匹配）。
 */
@RunWith(AndroidJUnit4::class)
class MessageBubbleRenderingTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun chatMessage(
        id: String,
        role: MessageRole,
        text: String,
        isFinal: Boolean,
    ) = ChatMessage(
        id = id, sessionId = "s1", role = role, text = text,
        isFinal = isFinal, seq = 0, timestamp = Instant.now(),
    )

    private fun setContent(message: ChatMessage, onLinkClick: ((String) -> Unit)? = {}) {
        composeRule.setContent {
            LivaChatTheme {
                MessageBubble(message = message, onLinkClick = onLinkClick)
            }
        }
    }

    @Test
    fun userBubbleKeepsPlainText() {
        setContent(chatMessage("u1", MessageRole.USER, "价格是 **100** 元 #标题", isFinal = true))
        composeRule.onNodeWithText("**100**", substring = true).assertExists()
        composeRule.onNodeWithText("#标题", substring = true).assertExists()
    }

    @Test
    fun aiFinalWithCallbackRendersMarkdown() {
        setContent(chatMessage("a1", MessageRole.AI, "**加粗** 与 [链接](https://example.com)", isFinal = true))
        composeRule.waitForIdle()
        onView(withText(containsString("加粗")))
            .check(matches(not(withText(containsString("**")))))
    }

    @Test
    fun aiNonFinalKeepsPlainTextWithCursor() {
        composeRule.setContent {
            LivaChatTheme {
                MessageBubble(
                    message = chatMessage("a2", MessageRole.AI, "还在说 **半截**", isFinal = false),
                    showCursor = true,
                    onLinkClick = {},
                )
            }
        }
        composeRule.onNodeWithText("还在说 **半截**", substring = true).assertExists()
        composeRule.onNodeWithText("▎").assertExists()
    }

    @Test
    fun aiFinalWithoutCallbackKeepsPlainText() {
        setContent(chatMessage("a3", MessageRole.AI, "没有回调 **字面**", isFinal = true), onLinkClick = null)
        composeRule.onNodeWithText("没有回调 **字面**", substring = true).assertExists()
    }
}
