package dev.livachat.app.ui.screens.conversation

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.TouchInjectionScope
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.test.platform.app.InstrumentationRegistry
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.R
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.sdk.model.AiImage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant

/** 查看器 UI 行为：单击退出 / 页码指示器 / fit 态滑动翻页（手势消费次序的机械化验证）。 */
@RunWith(AndroidJUnit4::class)
class ImageViewerOverlayTest {

    /** 1×1 灰度 baseline JPEG（sips 核实 1x1，可被 BitmapFactory 解码）。 */
    private val tinyJpegBase64 =
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n" +
            "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/E" +
            "ABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="

    @get:Rule
    val composeRule = createComposeRule()

    private fun imageMessage(id: String, seq: Int) = ChatMessage(
        id = id, sessionId = "s1", role = MessageRole.AI, text = "图片 $id",
        isFinal = true, seq = seq, timestamp = Instant.now(),
        image = AiImage(data = tinyJpegBase64, mime = "image/jpeg", width = 1, height = 1),
    )

    /** base64("hello")：Base64 可解但非 JPEG → BitmapFactory 解码失败。 */
    private fun undecodableMessage(id: String, seq: Int) = ChatMessage(
        id = id, sessionId = "s1", role = MessageRole.AI, text = "坏图 $id",
        isFinal = true, seq = seq, timestamp = Instant.now(),
        image = AiImage(data = "aGVsbG8=", mime = "image/jpeg", width = 1, height = 1),
    )

    /**
     * 截断的 JPEG base64（数据段长度 ≡ 1 mod 4）：Base64.decode 抛 IllegalArgumentException。
     * 注：非字母表字符（如 '@'）在 android.util.Base64 中是 SKIP（静默忽略）不抛异常，
     * 真正抛的是尾状态非法（数据段 ≡ 1 mod 4、'=' 错位）——探针须用后者。
     */
    private fun invalidBase64Message(id: String, seq: Int) = ChatMessage(
        id = id, sessionId = "s1", role = MessageRole.AI, text = "非法图 $id",
        isFinal = true, seq = seq, timestamp = Instant.now(),
        image = AiImage(data = "/9j/4", mime = "image/jpeg", width = 1, height = 1),
    )

    private fun setOverlay(
        images: List<ChatMessage>,
        initialPage: Int = 0,
        onDismiss: () -> Unit = {},
        saveTip: String? = null,
        onSaveClick: (String) -> Unit = {},
    ) {
        composeRule.setContent {
            LivaChatTheme {
                ImageViewerOverlay(
                    images = images,
                    initialPage = initialPage,
                    onDismiss = onDismiss,
                    saveTip = saveTip,
                    onSaveClick = onSaveClick,
                )
            }
        }
    }

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)

    @Test
    fun singleImage_tap_dismisses() {
        var dismissed = false
        setOverlay(listOf(imageMessage("ai-1", 0)), onDismiss = { dismissed = true })

        composeRule.onNodeWithContentDescription(str(R.string.chat_ai_image)).performClick()
        composeRule.waitForIdle()

        assertTrue(dismissed)
    }

    @Test
    fun singleImage_hidesPageIndicator() {
        setOverlay(listOf(imageMessage("ai-1", 0)))

        composeRule.onNodeWithText("/ 1", substring = true).assertDoesNotExist()
    }

    @Test
    fun twoImages_swipeLeft_showsSecondPage() {
        var dismissed = false
        setOverlay(
            listOf(imageMessage("ai-1", 0), imageMessage("ai-2", 1)),
            onDismiss = { dismissed = true },
        )

        composeRule.onNodeWithText("1 / 2").assertExists()

        composeRule.onNodeWithContentDescription(str(R.string.chat_ai_image)).performTouchInput { swipeLeft() }
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText("2 / 2").fetchSemanticsNodes().isNotEmpty()
        }

        composeRule.onNodeWithText("2 / 2").assertExists()
        assertFalse("翻页滑动不得误触发单击退出", dismissed)
    }

    /**
     * 双指捏合张开（逐步外移，与真机手速量级一致）。
     * 缩放量值不进语义树，故用「捏合后 Pager 是否锁滚」做行为判别断言。
     */
    private fun TouchInjectionScope.pinchOut(steps: Int = 10) {
        val c = center
        val startPx = 60f
        down(0, c + Offset(-startPx, 0f))
        down(1, c + Offset(startPx, 0f))
        repeat(steps) { i ->
            val half = startPx + (i + 1) * 30f
            moveTo(0, c + Offset(-half, 0f))
            moveTo(1, c + Offset(half, 0f))
        }
        up(0)
        up(1)
    }

    @Test
    fun pinchZoom_locksPagerScroll() {
        setOverlay(listOf(imageMessage("ai-1", 0), imageMessage("ai-2", 1)))

        composeRule.onRoot().performTouchInput { pinchOut() }
        composeRule.waitForIdle()

        // scale > 1 → userScrollEnabled = false：横滑不翻页（若捏合未生效则会翻到 2 / 2）
        composeRule.onRoot().performTouchInput { swipeLeft() }
        composeRule.waitForIdle()

        composeRule.onNodeWithText("1 / 2").assertExists()
    }

    @Test
    fun zoomedState_singleTapDismisses() {
        var dismissed = false
        setOverlay(listOf(imageMessage("ai-1", 0)), onDismiss = { dismissed = true })

        composeRule.onRoot().performTouchInput { pinchOut() }
        composeRule.waitForIdle()
        assertFalse("双指手势不应触发单击退出", dismissed)

        composeRule.onRoot().performTouchInput { click(center) }
        composeRule.waitForIdle()

        assertTrue("放大态单击应退出", dismissed)
    }

    @Test
    fun twoFingerTap_doesNotDismiss() {
        var dismissed = false
        setOverlay(listOf(imageMessage("ai-1", 0)), onDismiss = { dismissed = true })

        composeRule.onRoot().performTouchInput {
            down(0, center + Offset(-40f, 0f))
            down(1, center + Offset(40f, 0f))
            up(0)
            up(1)
        }
        composeRule.waitForIdle()

        assertFalse("双指轻点不应退出", dismissed)
    }

    @Test
    fun invalidBase64Image_doesNotCrashAndStillDismisses() {
        var dismissed = false
        setOverlay(listOf(invalidBase64Message("ai-bad", 0)), onDismiss = { dismissed = true })

        composeRule.onRoot().performTouchInput { click(center) }
        composeRule.waitForIdle()

        assertTrue("非法 Base64 不应炸组合，且仍可单击退出", dismissed)
    }

    @Test
    fun undecodableImage_stillDismissesOnTap() {
        var dismissed = false
        setOverlay(listOf(undecodableMessage("ai-broken", 0)), onDismiss = { dismissed = true })

        // 无 Image 节点（解码失败不渲染图片），点按页面任意处
        composeRule.onRoot().performTouchInput { click(center) }
        composeRule.waitForIdle()

        assertTrue("解码失败页仍应可单击退出", dismissed)
    }

    @Test
    fun undecodableImage_pageStillSwipable() {
        setOverlay(listOf(undecodableMessage("ai-broken", 0), imageMessage("ai-2", 1)))

        composeRule.onRoot().performTouchInput { swipeLeft() }
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText("2 / 2").fetchSemanticsNodes().isNotEmpty()
        }

        composeRule.onNodeWithText("2 / 2").assertExists()
    }

    /**
     * 同窗口覆盖层不天然遮挡下层：覆盖层必须消费手势起手，否则点击会穿透到
     * 底层工具栏按钮（点按退出时误触麦克风/相机）。
     */
    @Test
    fun overlay_blocksUnderlyingContentTaps() {
        var underlyingClicked = false
        var dismissed = false
        composeRule.setContent {
            LivaChatTheme {
                Box(modifier = Modifier.fillMaxSize()) {
                    Box(
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .fillMaxWidth()
                            .height(96.dp)
                            .testTag("underlying")
                            .clickable { underlyingClicked = true },
                    )
                    ImageViewerOverlay(
                        images = listOf(imageMessage("ai-1", 0)),
                        initialPage = 0,
                        onDismiss = { dismissed = true },
                    )
                }
            }
        }

        composeRule.onNodeWithTag("underlying").performClick()
        composeRule.waitForIdle()

        assertTrue("覆盖层上的点按应触发退出", dismissed)
        assertFalse("覆盖层打开时底层控件不得收到点击", underlyingClicked)
    }

    // ── 保存到相册（§9.6 androidTest 增例）──────────────────────────

    @Test
    fun saveButton_exists() {
        setOverlay(listOf(imageMessage("ai-1", 0)))

        composeRule.onNodeWithContentDescription(str(R.string.common_save_image)).assertExists()
    }

    /** 按钮是 Pager 兄弟且绘制其上：消费 down → 页内 tap 取消，保存点击不得误退出。 */
    @Test
    fun saveButton_click_carriesCurrentPageId_andDoesNotDismiss() {
        var savedId: String? = null
        var dismissed = false
        setOverlay(
            listOf(imageMessage("ai-1", 0), imageMessage("ai-2", 1)),
            initialPage = 1,
            onDismiss = { dismissed = true },
            onSaveClick = { savedId = it },
        )

        composeRule.onNodeWithContentDescription(str(R.string.common_save_image)).performClick()
        composeRule.waitForIdle()

        assertEquals("回调须携带当前页消息 id", "ai-2", savedId)
        assertFalse("保存点击不得误退出查看器", dismissed)
    }

    @Test
    fun saveButton_afterSwipe_savesTargetPage() {
        var savedId: String? = null
        setOverlay(
            listOf(imageMessage("ai-1", 0), imageMessage("ai-2", 1)),
            onSaveClick = { savedId = it },
        )

        composeRule.onNodeWithContentDescription(str(R.string.chat_ai_image)).performTouchInput { swipeLeft() }
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText("2 / 2").fetchSemanticsNodes().isNotEmpty()
        }

        composeRule.onNodeWithContentDescription(str(R.string.common_save_image)).performClick()
        composeRule.waitForIdle()

        assertEquals("翻页后保存目标应随当前页", "ai-2", savedId)
    }

    @Test
    fun saveTip_visibleWhenSet() {
        // 组件原样展示调用方传入的文案；用资源文案做参数与断言，避免测试被设备语言（en/zh）左右
        setOverlay(
            listOf(imageMessage("ai-1", 0)),
            saveTip = str(R.string.common_saved_to_gallery),
        )

        composeRule.onNodeWithText(str(R.string.common_saved_to_gallery)).assertExists()
    }

    @Test
    fun saveTip_null_isNotShown() {
        setOverlay(listOf(imageMessage("ai-1", 0)))

        composeRule.onNodeWithText(str(R.string.common_saved_to_gallery)).assertDoesNotExist()
    }
}
