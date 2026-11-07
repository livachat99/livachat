package dev.livachat.app.ui.screens.webview

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.test.espresso.Espresso
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.ui.theme.LivaChatTheme
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.R
import org.junit.runner.RunWith

/** WebViewScreen 冒烟：真实加载本地 mock 页面、非法入参、404 错误占位、返回键。 */
@RunWith(AndroidJUnit4::class)
class WebViewScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun setContent(url: String, onNavigateBack: () -> Unit = {}) {
        composeRule.setContent {
            LivaChatTheme {
                WebViewScreen(url = url, onNavigateBack = onNavigateBack)
            }
        }
    }

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)

    @Test
    fun loadsPageAndShowsTitleFromServer() {
        server.enqueue(
            MockResponse().setBody("<html><head><title>测试页标题</title></head><body>hello</body></html>"),
        )
        setContent(server.url("/page").toString())
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText("测试页标题").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText("测试页标题").assertExists()
    }

    @Test
    fun invalidUrlShowsErrorPlaceholder() {
        setContent("javascript:alert(1)")
        composeRule.onNodeWithText(str(R.string.webview_cannot_open)).assertExists()
    }

    @Test
    fun http404ShowsErrorPlaceholderWithRetry() {
        server.enqueue(MockResponse().setResponseCode(404).setBody("not found"))
        setContent(server.url("/missing").toString())
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(str(R.string.webview_cannot_open)).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText(str(R.string.common_retry)).assertExists()
        composeRule.onNodeWithText(str(R.string.webview_open_in_browser)).assertExists()
    }

    @Test
    fun backKeyAtFirstPageNavigatesBack() {
        server.enqueue(
            MockResponse().setBody("<html><head><title>返回页</title></head><body>x</body></html>"),
        )
        var backCount = 0
        setContent(server.url("/back").toString()) { backCount++ }
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText("返回页").fetchSemanticsNodes().isNotEmpty()
        }
        Espresso.pressBack()
        composeRule.waitForIdle()
        assertEquals(1, backCount)
    }
}
