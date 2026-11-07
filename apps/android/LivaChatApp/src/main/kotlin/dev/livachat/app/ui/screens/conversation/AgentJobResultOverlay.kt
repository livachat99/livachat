@file:OptIn(ExperimentalMaterial3Api::class)

package dev.livachat.app.ui.screens.conversation

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import android.widget.TextView
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.displayCutoutPadding
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import dev.livachat.app.ui.components.MarkdownContent
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Surface
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary
import dev.livachat.app.ui.util.localizeDigits
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.flow.first

/**
 * agent job 结果全屏查看覆盖层（docs/android_agent_job_view.md §4.3 + §10 二期）：
 * 底色 = 气泡底色 `Surface`；竖屏带头行（Agent任务 + 到达时间 + 关闭钮），横屏去标题、关闭钮
 * 浮动右上角（§10.3 方案 A，内容最大化）；打开期间解锁旋转（§4.4），并可落到点击处（§10.2）。
 */
@Composable
fun AgentJobResultOverlay(
    /** 结果正文（markdown 原文）。 */
    text: String,
    /** 消息到达时间（头行 `HH:mm`；与气泡头行 "Agent: HH:mm" 同源，非 job 开始时间）。 */
    timestamp: java.time.Instant,
    onDismiss: () -> Unit,
    onLinkClick: (String) -> Unit,
    /** 打开时的落位偏移（气泡内点击处的正文字符偏移；null = 落顶部）。§10.2。 */
    initialCharOffset: Int? = null,
    /** 滚动状态：默认自持；测试注入用。在横竖分支之外持有 → 切换分支不丢位置（§10.3）。 */
    scrollState: ScrollState = rememberScrollState(),
) {
    BackHandler(enabled = true) { onDismiss() }
    FullSensorOrientationWhileComposed()

    val isLandscape = LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE
    var viewportHeight by remember { mutableIntStateOf(0) }
    var bodyTextView by remember { mutableStateOf<TextView?>(null) }

    // 落位（§10.2）：等首测完成再滚——viewportHeight 由 onSizeChanged 给出，晚于布局，
    // 此时 scrollState.maxValue 已定；命中行放到视口 1/3 处。
    // 只在「打开」时执行一次：横竖屏切换、内容重投（同 id upsert）都不重放，避免打断用户滚动。
    // D17：导航去 WebView 往返会销毁并重建组合、effect 必然重跑——placed 随目的地状态保存，
    // 返回/旋转时直接跳过，避免把已恢复的滚动位置顶回初始点击处；关闭覆盖层即注销，再次打开照常落位。
    var placed by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(initialCharOffset, bodyTextView) {
        if (placed) return@LaunchedEffect
        val offset = initialCharOffset ?: return@LaunchedEffect
        val textView = bodyTextView ?: return@LaunchedEffect
        val (viewport, maxScroll) = snapshotFlow { viewportHeight to scrollState.maxValue }
            .first { (height, _) -> height > 0 }
        val layout = textView.layout ?: return@LaunchedEffect
        val line = layout.getLineForOffset(offset.coerceIn(0, layout.text.length))
        scrollState.scrollTo(jobResultScrollTarget(layout.getLineTop(line), viewport, maxScroll))
        placed = true
    }

    Box(modifier = Modifier.fillMaxSize().background(Surface)) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                // 横屏无 TopAppBar，需自行避让状态栏；竖屏由 TopAppBar 的默认 windowInsets 处理。
                .then(if (isLandscape) Modifier.statusBarsPadding() else Modifier)
                .navigationBarsPadding(),
        ) {
            if (!isLandscape) {
                TopAppBar(
                    title = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                imageVector = Icons.Filled.SmartToy,
                                contentDescription = null,
                                tint = BrandIndigo,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(modifier = Modifier.width(Spacing.sm))
                            Text(
                                // 标题文字按实机反馈去掉（2026-09-25）；只留 Agent 图标 + 到达时间
                                // （与气泡头行 "Agent: HH:mm" 同源，非 job 开始时间）；
                                // 数字经 localizeDigits（ar 阿数，§20 缺口⑤）
                                text = localizeDigits(
                                    jobResultTimeFormatter.format(timestamp),
                                    LocalConfiguration.current.locales[0],
                                ),
                                style = MaterialTheme.typography.bodySmall,
                                color = TextSecondary,
                            )
                        }
                    },
                    actions = {
                        IconButton(onClick = onDismiss) {
                            Icon(Icons.Default.Close, contentDescription = stringResource(R.string.common_close), tint = TextPrimary)
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Surface,
                        titleContentColor = TextPrimary,
                    ),
                )
            }
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .onSizeChanged { viewportHeight = it.height }
                    .verticalScroll(scrollState)
                    .padding(horizontal = Spacing.md)
                    // 横屏无 TopAppBar：按设计固定保留 48dp 顶部空间，避免正文压住状态栏/关闭按钮。
                    .padding(top = if (isLandscape) 48.dp else 0.dp)
                    .padding(bottom = Spacing.md),
            ) {
                // 显式整宽：Markwon 表格在 0 宽首测下有列宽坍缩风险（hotslot 真机实证，§2.2）
                MarkdownContent(
                    markdown = text,
                    onLinkClick = onLinkClick,
                    onTextViewReady = { bodyTextView = it },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
        if (isLandscape) {
            // 横屏：✕ 浮在内容之上（半透明黑圆底，对齐图片查看器配方 §2.5）
            IconButton(
                onClick = onDismiss,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .statusBarsPadding()
                    .displayCutoutPadding()
                    .padding(Spacing.sm)
                    .background(Color.Black.copy(alpha = 0.35f), CircleShape),
            ) {
                Icon(Icons.Default.Close, contentDescription = stringResource(R.string.common_close), tint = TextPrimary)
            }
        }
    }
}

/** 落位目标：把命中行放到视口 1/3 处，并夹取到可滚动范围（§10.2，纯函数便于 JVM 单测）。 */
internal fun jobResultScrollTarget(lineTop: Int, viewportHeight: Int, maxScroll: Int): Int =
    (lineTop - viewportHeight / 3).coerceIn(0, maxScroll)

/**
 * 覆盖层组合期间把 Activity 锁到 [ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR]（四向随传感器，
 * 忽略系统「自动旋转」锁），离场恢复进入前的值（正常为清单锁 portrait）。
 * 以 activity 实例为键：未列入 configChanges 的配置变更重建 Activity 时，新实例会重新解锁。
 */
@Composable
private fun FullSensorOrientationWhileComposed() {
    val activity = LocalContext.current.findActivity()
    DisposableEffect(activity) {
        val previous = activity?.requestedOrientation
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR
        onDispose {
            activity?.requestedOrientation = previous ?: ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        }
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

private val jobResultTimeFormatter: DateTimeFormatter =
    DateTimeFormatter.ofPattern("HH:mm").withZone(ZoneId.systemDefault())

@Preview(showBackground = true, heightDp = 640)
@Composable
private fun AgentJobResultOverlayPreview() {
    LivaChatTheme {
        AgentJobResultOverlay(
            text = """
                已为你查询东京 9/28–10/2 的往返机票，结果如下：

                | 航班 | 出发 | 到达 | 含税价格 |
                |---|---|---|---|
                | MU 523 | 09-28 10:15 | 09-28 14:30 | ¥2,150 |
                | NH 920 | 09-28 09:05 | 09-28 13:20 | ¥2,480 |

                **推荐 MU 523**：价格最低，无需凌晨赶往机场。
            """.trimIndent(),
            timestamp = java.time.Instant.now(),
            onDismiss = {},
            onLinkClick = {},
        )
    }
}
