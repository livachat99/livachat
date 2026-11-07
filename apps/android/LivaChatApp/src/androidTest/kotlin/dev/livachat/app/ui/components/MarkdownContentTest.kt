package dev.livachat.app.ui.components

import android.text.method.LinkMovementMethod
import android.view.View
import android.widget.TextView
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.UiController
import androidx.test.espresso.ViewAction
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.ui.theme.LivaChatTheme
import org.hamcrest.CoreMatchers.containsString
import org.hamcrest.CoreMatchers.not
import org.hamcrest.Matcher
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNotSame
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * AndroidView interop 冒烟 + 真实触摸链路测试（Espresso 直接操作 View 层级，
 * 因为 AndroidView 内文本不进 compose 语义树）。
 * 渲染/链接逻辑断言见本地 Robolectric 测试 MarkdownRenderTest。
 */
@RunWith(AndroidJUnit4::class)
class MarkdownContentTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun setContent(markdown: String, onLinkClick: (String) -> Unit = {}) {
        composeRule.setContent {
            LivaChatTheme {
                // 内容贴顶时正文中心落在状态栏/挖孔区域（本机 LIO-AN00：cutout insets=99px，
                // Espresso 点击点 y≈131px），注入会被系统按「注入到其它应用」拒绝（INJECT_EVENTS）
                // ——顶部留白把点击点下移进应用窗口内。
                Box(modifier = Modifier.fillMaxSize().padding(top = 96.dp)) {
                    MarkdownContent(markdown = markdown, onLinkClick = onLinkClick)
                }
            }
        }
    }

    @Test
    fun markdownRendersWithoutLiteralMarkers() {
        setContent("**粗体** 与 [链接](https://example.com)")
        composeRule.waitForIdle()
        onView(withText(containsString("粗体")))
            .check(matches(not(withText(containsString("**")))))
    }

    @Test
    fun linkClickTriggersCallbackThroughRealTouchPath() {
        val links = mutableListOf<String>()
        setContent("[文档](https://example.com)") { links.add(it) }
        composeRule.waitForIdle()

        onView(withText("文档")).perform(click())
        composeRule.waitForIdle()
        assertEquals(listOf("https://example.com"), links)
    }

    @Test
    fun bareUrlClickTriggersCallback() {
        val links = mutableListOf<String>()
        setContent("https://example.com") { links.add(it) }
        composeRule.waitForIdle()

        onView(withText("https://example.com")).perform(click())
        composeRule.waitForIdle()
        assertEquals(listOf("https://example.com"), links)
    }

    @Test
    fun nonHttpSchemeLinkDoesNotTriggerCallback() {
        var clicked = false
        setContent("[x](javascript:alert(1))") { clicked = true }
        composeRule.waitForIdle()

        onView(withText("x")).perform(click())
        composeRule.waitForIdle()
        assertFalse("javascript: link must not be opened", clicked)
    }

    @Test
    fun tableAfterParagraphRendersWithoutLiteralPipes() {
        // adapter 空行折叠已停用，段落与表格间保留空行；表格渲染为 TableRowSpan，
        // 字面管道符不应出现在 TextView 文本中
        setContent("说明如下\n\n| A | B |\n|---|---|\n| 1 | 2 |")
        composeRule.waitForIdle()
        onView(withText(not(containsString("| A | B |"))))
            .check(matches(not(withText(containsString("| A | B |")))))
    }

    @Test
    fun textViewNotClickable_andNotPlatformMovementMethod() {
        // D13（docs/android_agent_job_view.md §2.2/§2.7）：非链接触摸必须不被 TextView 吞掉——
        // isClickable/isLongClickable 关掉（切断 View 层消费），movement method 换掉（切断
        // Touch.onTouchEvent 对 DOWN 的无条件消费，链接仍可点）。行为级验证见
        // MessageBubbleJobResultClickTest。
        setContent("普通文本 [链接](https://example.com)")
        composeRule.waitForIdle()
        onView(withText(containsString("普通文本"))).perform(
            object : ViewAction {
                override fun getConstraints(): Matcher<View> =
                    ViewMatchers.isAssignableFrom(TextView::class.java)

                override fun getDescription(): String = "assert TextView touch config (D13)"

                override fun perform(uiController: UiController?, view: View?) {
                    val textView = view as TextView
                    assertFalse("isClickable 必须为 false", textView.isClickable)
                    assertFalse("isLongClickable 必须为 false", textView.isLongClickable)
                    assertNotNull("movement method 必须在位（链接可点）", textView.movementMethod)
                    assertNotSame(
                        "不可用平台单例：其 Touch.onTouchEvent 对 ACTION_DOWN 无条件消费",
                        LinkMovementMethod.getInstance(),
                        textView.movementMethod,
                    )
                }
            },
        )
    }
}
