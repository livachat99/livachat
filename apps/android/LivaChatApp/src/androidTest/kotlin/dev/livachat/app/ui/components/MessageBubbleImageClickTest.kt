package dev.livachat.app.ui.components

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.R
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.sdk.model.AiImage
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant

/** 气泡图片点击入口：onImageClick 透传到 ChatImageView.clickable。 */
@RunWith(AndroidJUnit4::class)
class MessageBubbleImageClickTest {

    /** 1×1 灰度 baseline JPEG（sips 核实 1x1，可被 BitmapFactory 解码）。 */
    private val tinyJpegBase64 =
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n" +
            "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/E" +
            "ABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="

    @get:Rule
    val composeRule = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    private fun aiMessageWithImage() = ChatMessage(
        id = "ai-1", sessionId = "s1", role = MessageRole.AI, text = "看这张图",
        isFinal = true, seq = 0, timestamp = Instant.now(),
        image = AiImage(data = tinyJpegBase64, mime = "image/jpeg", width = 1, height = 1),
    )

    @Test
    fun imageClick_invokesCallback() {
        var clicked = false
        composeRule.setContent {
            LivaChatTheme {
                MessageBubble(message = aiMessageWithImage(), onImageClick = { clicked = true })
            }
        }

        composeRule.onNodeWithContentDescription(context.getString(R.string.chat_ai_image)).performClick()
        composeRule.waitForIdle()

        assertTrue(clicked)
    }

    /** 截断的 JPEG base64（数据段长度 ≡ 1 mod 4）：Base64.decode 抛 IllegalArgumentException。 */
    @Test
    fun invalidBase64Image_rendersWithoutCrash() {
        composeRule.setContent {
            LivaChatTheme {
                MessageBubble(
                    message = ChatMessage(
                        id = "ai-bad", sessionId = "s1", role = MessageRole.AI, text = "非法图",
                        isFinal = true, seq = 0, timestamp = Instant.now(),
                        image = AiImage(
                            data = "/9j/4", mime = "image/jpeg",
                            width = 1, height = 1,
                        ),
                    ),
                    onImageClick = {},
                )
            }
        }

        composeRule.onNodeWithText("非法图", substring = true).assertExists()
        composeRule.onNodeWithContentDescription(context.getString(R.string.chat_ai_image)).assertDoesNotExist()
    }

    @Test
    fun imageClick_withoutCallback_doesNotFire() {
        var clicked = false
        composeRule.setContent {
            LivaChatTheme {
                MessageBubble(message = aiMessageWithImage(), onImageClick = null)
            }
        }

        composeRule.onNodeWithContentDescription(context.getString(R.string.chat_ai_image)).performClick()
        composeRule.waitForIdle()

        assertFalse(clicked)
    }
}
