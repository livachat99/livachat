package dev.livachat.app.ui.components

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.click
import androidx.test.espresso.Espresso
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.R
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.sdk.model.AgentJob
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * AgentJobStatusBadge / AgentJobListPanel 交互（docs/android_agent_job_status.md §10 测试）。
 */
@RunWith(AndroidJUnit4::class)
class AgentJobStatusUiTest {

    @get:Rule
    val composeRule = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun badgeDesc(count: Int) =
        context.resources.getQuantityString(R.plurals.conv_agent_jobs_running, count, count)
    private fun panelTitle(count: Int) =
        context.resources.getQuantityString(R.plurals.conv_agent_jobs_title, count, count)
    private fun startedPrefix() = context.getString(R.string.conv_agent_job_started, "").trim()

    private fun job(id: String, summary: String, startedAtMs: Long, runningTimeMs: Long) =
        AgentJob(jobId = id, agentName = "hermes", summary = summary, startedAtMs = startedAtMs, runningTimeMs = runningTimeMs)

    // ── badge ─────────────────────────────────────────────────────

    @Test
    fun badge_visible_when_count_positive() {
        composeRule.setContent {
            LivaChatTheme {
                AgentJobStatusBadge(count = 3, onClick = {})
            }
        }
        composeRule.onNodeWithText("3").assertExists()
        composeRule.onNodeWithContentDescription(badgeDesc(3)).assertExists()
    }

    @Test
    fun badge_hidden_when_count_zero() {
        composeRule.setContent {
            LivaChatTheme {
                AgentJobStatusBadge(count = 0, onClick = {})
            }
        }
        composeRule.onNodeWithText("0").assertDoesNotExist()
        composeRule.onNodeWithContentDescription(badgeDesc(0)).assertDoesNotExist()
    }

    @Test
    fun badge_count_clamped_to_99_plus() {
        composeRule.setContent {
            LivaChatTheme {
                AgentJobStatusBadge(count = 123, onClick = {})
            }
        }
        composeRule.onNodeWithText("99+").assertExists()
        composeRule.onNodeWithText("123").assertDoesNotExist()
    }

    @Test
    fun badge_touch_target_wider_than_visual_capsule() {
        composeRule.setContent {
            LivaChatTheme {
                var open by remember { mutableStateOf(false) }
                Box(modifier = Modifier.fillMaxSize()) {
                    AgentJobStatusBadge(
                        count = 1,
                        onClick = { open = !open },
                        modifier = Modifier.testTag("badge"),
                    )
                    if (open) {
                        AgentJobListPanel(
                            jobs = listOf(job("job-1", "查询机票", System.currentTimeMillis() - 2_000, 2_000)),
                            onDismiss = { open = false },
                            cancelState = null,
                            onRequestCancel = {},
                            onDismissCancel = {},
                            onConfirmCancel = {},
                            maxHeight = 480.dp,
                        )
                    }
                }
            }
        }

        // Click 20dp below the badge center: outside the 28dp visual capsule
        // (half-height 14dp) but inside the 48dp touch target (half 24dp).
        composeRule.onNodeWithTag("badge").performTouchInput {
            click(center + Offset(0f, with(density) { 20.dp.toPx() }))
        }
        composeRule.waitForIdle()
        composeRule.onNodeWithText(panelTitle(1)).assertExists()
    }

    // ── panel ─────────────────────────────────────────────────────

    @Test
    fun click_badge_opens_panel_with_rows() {
        composeRule.setContent {
            LivaChatTheme {
                var open by remember { mutableStateOf(false) }
                Box(modifier = Modifier.fillMaxSize()) {
                    AgentJobStatusBadge(count = 2, onClick = { open = !open })
                    if (open) {
                        AgentJobListPanel(
                            jobs = listOf(
                                job("job-1", "正在查询东京机票", System.currentTimeMillis() - 59_000, 59_000),
                                job("job-2", "汇总上周会议纪要", System.currentTimeMillis() - 2_000, 2_000),
                            ),
                            onDismiss = { open = false },
                            cancelState = null,
                            onRequestCancel = {},
                            onDismissCancel = {},
                            onConfirmCancel = {},
                            maxHeight = 480.dp,
                        )
                    }
                }
            }
        }

        composeRule.onNodeWithText("2").performClick()
        composeRule.waitForIdle()

        composeRule.onNodeWithText(panelTitle(2)).assertExists()
        // Row three elements: summary / started time / running time.
        // Running time exact value depends on composition timing on slow emulators;
        // format boundaries are covered by unit tests, here assert the element exists.
        composeRule.onNodeWithText("正在查询东京机票").assertExists()
        composeRule.onAllNodesWithText(startedPrefix(), substring = true).assertCountEquals(2)
        composeRule.onAllNodes(hasElapsedText).assertCountEquals(2)
    }

    private val hasElapsedText = androidx.compose.ui.test.SemanticsMatcher(
        "elapsed running time (Xs / Xm Ys / Xh Ym)",
    ) { node ->
        try {
            node.config[androidx.compose.ui.semantics.SemanticsProperties.Text]
                .any { it.text.matches(Regex("^\\d.*[sm]$")) }
        } catch (e: IllegalStateException) {
            false
        }
    }

    @Test
    fun panel_dismisses_on_outside_click() {
        composeRule.setContent {
            LivaChatTheme {
                var open by remember { mutableStateOf(false) }
                Box(modifier = Modifier.fillMaxSize().testTag("root")) {
                    AgentJobStatusBadge(count = 1, onClick = { open = !open })
                    if (open) {
                        AgentJobListPanel(
                            jobs = listOf(job("job-1", "查询机票", System.currentTimeMillis() - 2_000, 2_000)),
                            onDismiss = { open = false },
                            cancelState = null,
                            onRequestCancel = {},
                            onDismissCancel = {},
                            onConfirmCancel = {},
                            maxHeight = 480.dp,
                        )
                    }
                }
            }
        }

        composeRule.onNodeWithText("1").performClick()
        composeRule.waitForIdle()
        composeRule.onNodeWithText(panelTitle(1)).assertExists()

        // Real touch outside the popup via the system input pipeline (compose
        // test injection targets the main window only, which never reaches the
        // Popup's outside-tap listener; shell `input tap` goes through WM and
        // delivers the outside event to the focusable Popup).
        val metrics = InstrumentationRegistry.getInstrumentation().targetContext.resources.displayMetrics
        // Left edge, 25% from the top: below the status bar, above the
        // top-anchored popup (offset 180dp ≈ 33% of a 540dp screen), and well
        // clear of the bottom navigation bar.
        InstrumentationRegistry.getInstrumentation().uiAutomation
            .executeShellCommand("input tap 20 ${(metrics.heightPixels * 0.25).toInt()}")
        Thread.sleep(500)
        composeRule.waitForIdle()
        composeRule.onNodeWithText(panelTitle(1)).assertDoesNotExist()
    }

    @Test
    fun panel_dismisses_on_back_press() {
        composeRule.setContent {
            LivaChatTheme {
                var open by remember { mutableStateOf(false) }
                Box(modifier = Modifier.fillMaxSize()) {
                    AgentJobStatusBadge(count = 1, onClick = { open = !open })
                    if (open) {
                        AgentJobListPanel(
                            jobs = listOf(job("job-1", "查询机票", System.currentTimeMillis() - 2_000, 2_000)),
                            onDismiss = { open = false },
                            cancelState = null,
                            onRequestCancel = {},
                            onDismissCancel = {},
                            onConfirmCancel = {},
                            maxHeight = 480.dp,
                        )
                    }
                }
            }
        }

        composeRule.onNodeWithText("1").performClick()
        composeRule.waitForIdle()
        composeRule.onNodeWithText(panelTitle(1)).assertExists()

        Espresso.pressBack()
        composeRule.waitForIdle()
        composeRule.onNodeWithText(panelTitle(1)).assertDoesNotExist()
    }

    // ── 行内取消（docs/android_agent_job_status.md §6.4）────────────

    private fun question() = context.getString(R.string.conv_agent_job_cancel_question)
    private fun yes() = context.getString(R.string.conv_agent_job_cancel_yes)
    private fun no() = context.getString(R.string.conv_agent_job_cancel_no)
    private fun inProgress() = context.getString(R.string.conv_agent_job_cancel_in_progress)
    private fun failed() = context.getString(R.string.conv_agent_job_cancel_failed)

    @Test
    fun click_x_opensInlineConfirm_and_no_staysInPanel() {
        var requests = 0
        composeRule.setContent {
            LivaChatTheme {
                var state by remember { mutableStateOf<AgentJobCancelUiState?>(null) }
                Box(Modifier.fillMaxSize()) {
                    AgentJobListPanel(
                        jobs = listOf(job("job-1", "查询机票", System.currentTimeMillis() - 2_000, 2_000)),
                        onDismiss = {},
                        cancelState = state,
                        onRequestCancel = {
                            requests += 1
                            state = AgentJobCancelUiState(it)
                        },
                        onDismissCancel = { state = null },
                        onConfirmCancel = { state = state?.copy(inFlight = true) },
                        maxHeight = 480.dp,
                    )
                }
            }
        }

        composeRule.onNodeWithTag("agent-job-cancel-action-job-1").performClick()
        composeRule.waitForIdle()
        assertEquals(1, requests)
        composeRule.onNodeWithText(question()).assertExists()
        // 卡片不再重复目标摘要（chat 气泡里已有），遮罩行语义也已清空
        composeRule.onNodeWithText("查询机票").assertDoesNotExist()
        composeRule.onNodeWithTag("agent-job-cancel-confirm").assertExists()
        // 面板未被弹窗顶掉：标题仍在
        composeRule.onNodeWithText(panelTitle(1)).assertExists()

        composeRule.onNodeWithText(no()).performClick()
        composeRule.waitForIdle()
        composeRule.onNodeWithText(question()).assertDoesNotExist()

        // 再开一次，点「是」→ 进度态
        composeRule.onNodeWithTag("agent-job-cancel-action-job-1").performClick()
        composeRule.waitForIdle()
        composeRule.onNodeWithText(yes()).performClick()
        composeRule.waitForIdle()
        composeRule.onNodeWithText(inProgress()).assertExists()
        composeRule.onNodeWithText(yes()).assertDoesNotExist()
    }

    @Test
    fun cancelError_showsInlineMessage() {
        val message = failed()
        composeRule.setContent {
            LivaChatTheme {
                val job = job("job-1", "查询机票", System.currentTimeMillis() - 2_000, 2_000)
                AgentJobListPanel(
                    jobs = listOf(job),
                    onDismiss = {},
                    cancelState = AgentJobCancelUiState(job = job, error = message),
                    onRequestCancel = {},
                    onDismissCancel = {},
                    onConfirmCancel = {},
                    maxHeight = 480.dp,
                )
            }
        }
        composeRule.onNodeWithText(question()).assertExists()
        composeRule.onNodeWithText(message).assertExists()
    }

    @Test
    fun confirm_question_and_actions_are_on_separate_lines() {
        composeRule.setContent {
            LivaChatTheme {
                val job = job("job-1", "查询机票", System.currentTimeMillis() - 2_000, 2_000)
                AgentJobListPanel(
                    jobs = listOf(job),
                    onDismiss = {},
                    cancelState = AgentJobCancelUiState(job = job),
                    onRequestCancel = {},
                    onDismissCancel = {},
                    onConfirmCancel = {},
                    maxHeight = 480.dp,
                )
            }
        }
        val questionBounds = composeRule.onNodeWithText(question())
            .fetchSemanticsNode().boundsInRoot
        val yesBounds = composeRule.onNodeWithText(yes())
            .fetchSemanticsNode().boundsInRoot
        assertTrue(
            "问题底边 ${questionBounds.bottom} 应高于操作行顶边 ${yesBounds.top}",
            questionBounds.bottom <= yesBounds.top,
        )
    }

    @Test
    fun confirm_question_and_actions_are_centered() {
        composeRule.setContent {
            LivaChatTheme {
                val job = job("job-1", "查询机票", System.currentTimeMillis() - 2_000, 2_000)
                AgentJobListPanel(
                    jobs = listOf(job),
                    onDismiss = {},
                    cancelState = AgentJobCancelUiState(job = job),
                    onRequestCancel = {},
                    onDismissCancel = {},
                    onConfirmCancel = {},
                    maxHeight = 480.dp,
                )
            }
        }
        val card = composeRule.onNodeWithTag("agent-job-cancel-confirm")
            .fetchSemanticsNode().boundsInRoot
        val questionBounds = composeRule.onNodeWithText(question())
            .fetchSemanticsNode().boundsInRoot
        val noBounds = composeRule.onNodeWithText(no())
            .fetchSemanticsNode().boundsInRoot
        val yesBounds = composeRule.onNodeWithText(yes())
            .fetchSemanticsNode().boundsInRoot
        val actionsCenter = (minOf(noBounds.left, yesBounds.left) +
            maxOf(noBounds.right, yesBounds.right)) / 2f

        assertEquals("问题应水平居中", card.center.x, questionBounds.center.x, 2f)
        assertEquals("否/是操作行应水平居中", card.center.x, actionsCenter, 4f)
    }

    @Test
    fun scrim_blocks_clickThrough_to_x() {
        var requests = 0
        composeRule.setContent {
            LivaChatTheme {
                var state by remember { mutableStateOf<AgentJobCancelUiState?>(null) }
                Box(Modifier.fillMaxSize()) {
                    AgentJobListPanel(
                        jobs = listOf(job("job-1", "查询机票", System.currentTimeMillis() - 2_000, 2_000)),
                        onDismiss = {},
                        cancelState = state,
                        onRequestCancel = {
                            requests += 1
                            state = AgentJobCancelUiState(it)
                        },
                        onDismissCancel = { state = null },
                        onConfirmCancel = {},
                        maxHeight = 480.dp,
                    )
                }
            }
        }
        composeRule.onNodeWithTag("agent-job-cancel-action-job-1").performClick()
        composeRule.waitForIdle()
        assertEquals(1, requests)

        // 点原 × 所在的最右侧区域（被遮罩覆盖）→ 不应再次触发
        composeRule.onNodeWithTag("agent-job-row-job-1").performTouchInput {
            click(Offset(width - 10f, height / 2f))
        }
        composeRule.waitForIdle()
        assertEquals(1, requests)
    }

    @Test
    fun panel_height_grows_with_rows_and_caps_at_max() {
        var count by mutableStateOf(1)
        composeRule.setContent {
            LivaChatTheme {
                AgentJobListPanel(
                    jobs = (1..count).map {
                        job("job-$it", "任务 $it", System.currentTimeMillis() - 2_000, 2_000)
                    },
                    onDismiss = {},
                    cancelState = null,
                    onRequestCancel = {},
                    onDismissCancel = {},
                    onConfirmCancel = {},
                    maxHeight = 220.dp,
                )
            }
        }
        val one = composeRule.onNodeWithTag("agent-job-panel")
            .fetchSemanticsNode().boundsInRoot.height
        composeRule.runOnIdle { count = 8 }
        composeRule.waitForIdle()
        val many = composeRule.onNodeWithTag("agent-job-panel")
            .fetchSemanticsNode().boundsInRoot.height
        val maxPx = with(composeRule.density) { 220.dp.toPx() }

        assertTrue("panel should grow: $one -> $many", many > one)
        assertTrue("panel height $many must be capped at $maxPx", many <= maxPx + 1f)
    }

    @Test
    fun panel_list_scrolls_to_last_row_when_capped() {
        composeRule.setContent {
            LivaChatTheme {
                AgentJobListPanel(
                    jobs = (1..8).map {
                        job("job-$it", "任务 $it", System.currentTimeMillis() - 2_000, 2_000)
                    },
                    onDismiss = {},
                    cancelState = null,
                    onRequestCancel = {},
                    onDismissCancel = {},
                    onConfirmCancel = {},
                    maxHeight = 220.dp,
                )
            }
        }
        composeRule.onNodeWithTag("agent-job-list")
            .performScrollToNode(hasText("任务 8"))
        composeRule.onNodeWithText("任务 8").assertIsDisplayed()
    }
}
