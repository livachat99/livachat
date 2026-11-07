package dev.livachat.app.ui.screens.conversation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import java.time.Instant

/** 覆盖层载荷：正文 + 展示时间（docs/android_agent_job_view.md §12.3.3）。 */
internal data class AgentJobPayload(
    val text: String,
    val timestamp: Instant,
)

/**
 * agent job 结果覆盖层宿主（docs/android_agent_job_view.md §12.3.3，O22）：把「身份解析 + 未命中自关窗」
 * 从各调用点收成一处，会话页 / History 详情页 / 时间线两屏共用。
 *
 * [viewId] 为身份（会话/History 用消息 id，时间线用行 key）；[resolve] 按身份解析**当前**载荷
 * （未命中 → null：重取后事件消失、消息被删等）→ 触发一次 [onDismiss] 自动关窗，防状态悬空。
 *
 * [keepLastPayloadOnMiss]（§12.9）用于**分页窗口**数据源（时间线两屏）：Q11 轮询重取会把 rows 截回首页
 * （`accept(replace=true)`），此时「当前未命中」≠「事件已消失」——若照常关窗，正在阅读的用户会被后台轮询
 * 踢出。置 true 时以「最近一次解析到的载荷」兜底继续展示（身份仍在时仍优先现取，不被兜底覆盖）。
 */
@Composable
internal fun AgentJobResultHost(
    viewId: String?,
    initialCharOffset: Int?,
    resolve: (String) -> AgentJobPayload?,
    onDismiss: () -> Unit,
    onLinkClick: (String) -> Unit,
    keepLastPayloadOnMiss: Boolean = false,
) {
    val id = viewId ?: return
    // 以 id 为键：换身份即丢弃兜底，绝不把上一条结果的正文留给下一条
    var lastPayload by remember(id) { mutableStateOf<AgentJobPayload?>(null) }
    val resolved = resolve(id)
    LaunchedEffect(id, resolved) {
        if (resolved != null) lastPayload = resolved
    }
    val payload = resolved ?: if (keepLastPayloadOnMiss) lastPayload else null
    if (payload == null) {
        LaunchedEffect(id) { onDismiss() }
        return
    }
    AgentJobResultOverlay(
        text = payload.text,
        timestamp = payload.timestamp,
        initialCharOffset = initialCharOffset,
        onDismiss = onDismiss,
        onLinkClick = onLinkClick,
    )
}
