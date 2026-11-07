package dev.livachat.app.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupProperties
import dev.livachat.app.R
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.Elevation
import dev.livachat.app.ui.theme.Gold
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Success
import dev.livachat.app.ui.theme.Surface
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary
import dev.livachat.app.ui.util.localizeDigits
import dev.livachat.sdk.model.AgentJob
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** 面板顶边锚点：Popup 偏移与屏幕高度计算共用（docs/android_agent_job_status.md §6.5）。 */
internal val AgentJobPanelTopOffset: Dp = 180.dp

/**
 * Floating translucent panel listing the device's running background jobs (C4).
 * Window-level [Popup] with focusable=true: tapping outside or pressing back
 * dismisses for free (D4). All rows share one 1s ticker while visible (D11).
 *
 * maxHeight 由调用方按底部工具栏位置计算；面板内容驱动高度，超过后 LazyColumn
 * 滚动（§3.5.3-E）。取消确认是行内 subview，不再使用 AlertDialog。
 */
@Composable
fun AgentJobListPanel(
    jobs: List<AgentJob>,
    onDismiss: () -> Unit,
    cancelState: AgentJobCancelUiState?,
    onRequestCancel: (AgentJob) -> Unit,
    onDismissCancel: () -> Unit,
    onConfirmCancel: () -> Unit,
    maxHeight: Dp,
    /** edge-to-edge 下窗口从屏幕顶部开始，需补状态栏高度以保持原视觉位置。 */
    topInset: Dp = 0.dp,
) {
    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1_000)
            nowMs = System.currentTimeMillis()
        }
    }

    Popup(
        alignment = Alignment.TopCenter,
        offset = IntOffset(
            x = 0,
            y = with(LocalDensity.current) {
                (AgentJobPanelTopOffset + topInset).roundToPx()
            },
        ),
        // Below the avatar region (top padding + 120dp avatar); visually anchored
        // to directly under the avatar.
        onDismissRequest = onDismiss,
        properties = PopupProperties(focusable = true),
    ) {
        Surface(
            modifier = Modifier
                .fillMaxWidth(0.85f)
                .heightIn(max = maxHeight)
                .testTag("agent-job-panel"),
            shape = RoundedCornerShape(16.dp),
            color = Surface,
            border = BorderStroke(1.dp, Gold),
            shadowElevation = Elevation.Level3,
        ) {
            Column(modifier = Modifier.padding(Spacing.md)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        imageVector = Icons.Filled.SmartToy,
                        contentDescription = null,
                        tint = BrandIndigo,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(modifier = Modifier.width(Spacing.sm))
                    Text(
                        text = pluralStringResource(R.plurals.conv_agent_jobs_title, jobs.size, jobs.size),
                        style = MaterialTheme.typography.titleSmall,
                        color = TextPrimary,
                    )
                }
                Spacer(modifier = Modifier.height(Spacing.sm))
                LazyColumn(modifier = Modifier.testTag("agent-job-list")) {
                    items(
                        jobs.sortedBy { it.startedAtMs },
                        key = { it.jobId },
                    ) { job ->
                        AgentJobRow(
                            job = job,
                            nowMs = nowMs,
                            cancelState = cancelState,
                            cancelInFlight = cancelState?.inFlight == true,
                            onRequestCancel = onRequestCancel,
                            onDismissCancel = onDismissCancel,
                            onConfirmCancel = onConfirmCancel,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun AgentJobRow(
    job: AgentJob,
    nowMs: Long,
    cancelState: AgentJobCancelUiState?,
    cancelInFlight: Boolean,
    onRequestCancel: (AgentJob) -> Unit,
    onDismissCancel: () -> Unit,
    onConfirmCancel: () -> Unit,
) {
    val locale = LocalConfiguration.current.locales[0]
    val runningMs = (nowMs - job.startedAtMs).let {
        if (it >= 0) it else job.runningTimeMs   // 时钟未校准/负值回退
    }
    val confirming = cancelState?.job?.jobId == job.jobId

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("agent-job-row-${job.jobId}"),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .then(if (confirming) Modifier.clearAndSetSemantics {} else Modifier)
                .padding(vertical = Spacing.sm),
        ) {
            Text(
                text = job.summary,
                style = MaterialTheme.typography.bodyLarge,
                color = TextPrimary,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Row(
                    modifier = Modifier.weight(1f),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = stringResource(
                            R.string.conv_agent_job_started,
                            formatStartTime(job.startedAtMs, locale),
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    Spacer(modifier = Modifier.width(Spacing.sm))
                    Text(
                        text = formatElapsed(runningMs, locale),
                        style = MaterialTheme.typography.labelLarge,
                        color = Success,
                        maxLines = 1,
                    )
                }
                IconButton(
                    onClick = { onRequestCancel(job) },
                    enabled = !cancelInFlight,
                    modifier = Modifier.testTag("agent-job-cancel-action-${job.jobId}"),
                ) {
                    Icon(
                        imageVector = Icons.Filled.Close,
                        contentDescription = stringResource(R.string.conv_agent_job_cancel_action),
                        tint = TextSecondary,
                        modifier = Modifier.size(16.dp),
                    )
                }
            }
        }
        if (confirming) {
            Box(
                Modifier
                    .matchParentSize()
                    .background(Background.copy(alpha = 0.82f))
                    // 吞掉点击，防穿透到被遮罩的 ×；clear semantics 避免多余可点节点
                    .clickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                        onClick = {},
                    )
                    .clearAndSetSemantics {}
                    .testTag("agent-job-cancel-scrim"),
            )
            AgentJobCancelConfirm(
                state = cancelState!!,
                onDismiss = onDismissCancel,
                onConfirm = onConfirmCancel,
                modifier = Modifier
                    .align(Alignment.Center)
                    .fillMaxWidth()
                    .padding(horizontal = Spacing.xs)
                    .testTag("agent-job-cancel-confirm"),
            )
        }
    }
}

private val startTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss")
    .withZone(ZoneId.systemDefault())

/** Formats a device-local epoch ms as HH:mm:ss in the system timezone (D2); 数字经 [localizeDigits]（§20 缺口⑤）。 */
internal fun formatStartTime(epochMs: Long, locale: Locale): String =
    localizeDigits(startTimeFormatter.format(Instant.ofEpochMilli(epochMs)), locale)

/** Human-readable elapsed time: Xs / Xm Ys / Xh Ym；数字经 [localizeDigits]（§20 缺口⑤ 同面板混排修复，单位保持 h/m/s）。 */
internal fun formatElapsed(ms: Long, locale: Locale): String {
    val totalSec = ms / 1000
    val h = totalSec / 3600
    val m = totalSec % 3600 / 60
    val s = totalSec % 60
    return localizeDigits(
        when {
            h > 0 -> "${h}h ${m}m"
            totalSec >= 60 -> "${m}m ${s}s"
            else -> "${s}s"
        },
        locale,
    )
}

@Preview(showBackground = true)
@Composable
private fun AgentJobListPanelPreview() {
    LivaChatTheme {
        Surface(color = Background) {
            AgentJobListPanel(
                jobs = listOf(
                    AgentJob(
                        jobId = "job-1",
                        agentName = "hermes",
                        summary = "正在查询东京机票",
                        startedAtMs = System.currentTimeMillis() - 85_000,
                        runningTimeMs = 85_000,
                    ),
                    AgentJob(
                        jobId = "job-2",
                        agentName = "hermes",
                        summary = "汇总上周会议纪要并生成待办清单",
                        startedAtMs = System.currentTimeMillis() - 72_000,
                        runningTimeMs = 72_000,
                    ),
                ),
                onDismiss = {},
                cancelState = null,
                onRequestCancel = {},
                onDismissCancel = {},
                onConfirmCancel = {},
                maxHeight = 480.dp,
            )
        }
    }
}
