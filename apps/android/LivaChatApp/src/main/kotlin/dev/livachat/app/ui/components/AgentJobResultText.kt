package dev.livachat.app.ui.components

import android.widget.TextView
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.Spacing

/** 折叠行数上限（docs/android_agent_job_view.md §11）。 */
internal const val JOB_RESULT_COLLAPSE_LINES = 24

/** 折叠触发阈值：低于此长度不折叠（§11.9-4 由 400 上调，避免刚过 24 行就折出「还有几十字」的尾巴）。 */
internal const val JOB_RESULT_COLLAPSE_MIN_CHARS = 800

/**
 * 该行是否为 GFM **分隔行**（如 `| --- | --- |`、`--- | ---`、`|:---:|:---:|`）：表格的唯一可靠标记，
 * 有无前导/尾管道都合法。判据 = 整行只由 `|`/`-`/`:`/空白组成，且 `|` 与 `-` **各至少一个**
 * （前者排除水平线 `---` 与 setext 下划线，后者排除正文 `a | b` 与代码块 `| x = 1`）。
 *
 * 用逐字符分类而非正则：早先的分隔行正则（`^\|?…:?-+:?…$`）在「长 `|-` 交替」行上会**回溯到
 * 栈溢出**（16k 字符复现 `StackOverflowError`，§12.9 缺陷 5）——正文来自模型输出，长度/形态不可控，
 * 组合期抛 Error 即崩溃。本实现与行长线性、常量栈、无回溯。
 */
private fun isTableDelimiterRow(line: String): Boolean {
    var hasPipe = false
    var hasDash = false
    for (c in line) {
        when (c) {
            '|' -> hasPipe = true
            '-' -> hasDash = true
            ':', ' ', '\t' -> {}
            else -> return false
        }
    }
    return hasPipe && hasDash
}

/**
 * 正文是否含 GFM 表格 → 决定是否给 Markwon **整宽**（规避「0 宽首测列宽坍缩」，§12.2 真机实证 / O1）。
 *
 * 判据取**分隔行**而非「以 `|` 开头的行」：后者漏掉无前导管道的合法表格（`a | b` 紧跟 `--- | ---`
 * → 表格被挤成 0 宽列），又把代码块里任意 `|` 开头的行误判为表格（把短气泡拉成整宽）。
 * **已知残留**（全部是「误判 → 多占宽度」，不影响内容与列宽）：①围栏代码块内出现分隔行形态的行
 * （如 `--- | ---`）；②冒号未校验位置（`| - : |` 这类非法单元格也命中）；③表头行与分隔行未配对校验。
 * 不为这些长尾引入 Markwon 解析器——此处只需**测量前**的启发式。
 */
internal fun markdownHasTable(markdown: String): Boolean =
    markdown.lineSequence().any(::isTableDelimiterRow)

/**
 * agent job 结果正文块（docs/android_agent_job_view.md §12.3.1）：Markdown 渲染 +
 * 超长时按**行边界**裁剪到 [JOB_RESULT_COLLAPSE_LINES] 行 + 入口行「查看全文 · 还有 N 字 ↗」。
 *
 * 会话气泡（`MessageBubble`）与时间线 agent 行（`HotSlotRowItems`）共用：
 * - 折叠阈值/行数收在本组件内（调用方只传 [collapsible] 开关，"两处一致"由结构保证，§12.3.1）；
 * - [onContentTap] 上报正文按下处的字符偏移（全屏落位用）；**整卡/整行 clickable 与偏移 state 由调用方持有**
 *   （本组件不自建 clickable 与 state）；
 * - 含表格的正文自动给整宽（会话气泡 wrap-content，仅表格场景需要；§12.3.1 约束的"不得无条件整宽"）。
 */
@Composable
internal fun AgentJobResultText(
    markdown: String,
    onLinkClick: (String) -> Unit,
    /** 是否允许折叠（false = 整段平铺，如无全屏出口的历史页形态） */
    collapsible: Boolean,
    onContentTap: ((charOffset: Int) -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val collapsibleByLength = collapsible && markdown.length >= JOB_RESULT_COLLAPSE_MIN_CHARS
    val hasTable = remember(markdown) { markdownHasTable(markdown) }
    val density = LocalDensity.current
    var bodyTextView by remember { mutableStateOf<TextView?>(null) }
    var collapse by remember { mutableStateOf<CollapseMetrics?>(null) }
    var collapseMeasured by remember { mutableStateOf(false) }

    LaunchedEffect(bodyTextView, markdown, collapsibleByLength) {
        collapse = null
        collapseMeasured = false
        val textView = bodyTextView
        // textView 未回调时保持「未测」= 估算裁剪；MarkdownContent 的 factory/update 回调一到
        // （bodyTextView 变化 → 本 effect 重跑）即自愈，故无需在此置位
        if (textView == null || !collapsibleByLength) return@LaunchedEffect
        // LaunchedEffect 在组合后启动，首测可能尚未完成（layout 为 null）→ 逐帧等待（正常 1~2 帧）
        var layout = textView.layout
        var waitedFrames = 0
        while (layout == null && waitedFrames < 4) {
            withFrameNanos { }
            layout = textView.layout
            waitedFrames++
        }
        // 测不到布局（4 帧内 layout 恒 null）也要落「已测」→ fail-open：不裁剪、不出入口行
        // （宁可长，不静默截断，§11.10-2）。此处若提前 return 而不置位，clipActive 会因
        // `!collapseMeasured` 恒真：气泡被永久按估算高度截断，且因 collapse == null 永远没有入口行
        collapseMeasured = true
        collapse = layout?.let { collapseMetrics(it, textView.text.length, JOB_RESULT_COLLAPSE_LINES) }
    }

    // 首帧（未测）用估算高度避免"先铺满再收起"；估算按 sp→dp 换算（随系统字号缩放，勿用 .value 直乘）；
    // 测完仅在确实超行数时裁剪（fail-open）
    val estimateCollapseHeight = with(density) {
        (MaterialTheme.typography.bodyLarge.lineHeight * JOB_RESULT_COLLAPSE_LINES).toDp()
    }
    val clipActive = collapsibleByLength && (!collapseMeasured || collapse != null)
    val collapseHeight = collapse?.clipHeightPx?.let { with(density) { it.toDp() } } ?: estimateCollapseHeight

    Column(modifier = modifier) {
        // 不用 TextView.maxLines——带 movement method 的 TextView 在本机不采纳该上限（真机三配置对照
        // 实测 §11.8-1），故走 Compose 侧行边界裁剪，确定性更强
        Box(
            modifier = if (clipActive) {
                Modifier.heightIn(max = collapseHeight).clipToBounds()
            } else {
                Modifier
            },
        ) {
            MarkdownContent(
                markdown = markdown,
                onLinkClick = onLinkClick,
                onContentTap = onContentTap,
                onTextViewReady = if (collapsibleByLength) {
                    { textView -> bodyTextView = textView }
                } else {
                    null
                },
                modifier = if (hasTable) Modifier.fillMaxWidth() else Modifier,
            )
        }

        // 入口行：「查看全文 · 还有 N 字」（仅确实被截断时出现；点击整卡/整行即进全屏）
        collapse?.remainingChars?.let { remaining ->
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(top = Spacing.xs),
            ) {
                Text(
                    text = pluralStringResource(R.plurals.conv_job_remaining_chars, remaining, formatRemainingChars(remaining)),
                    style = MaterialTheme.typography.labelLarge,
                    color = BrandIndigo,
                )
                Spacer(modifier = Modifier.width(Spacing.xs))
                Icon(
                    imageVector = Icons.Filled.OpenInFull,
                    contentDescription = null,
                    tint = BrandIndigo,
                    modifier = Modifier.size(14.dp),
                )
            }
        }
    }
}

/** 折叠度量：裁剪高度（px）与剩余字数。 */
internal data class CollapseMetrics(
    val clipHeightPx: Int,
    val remainingChars: Int,
)

/**
 * 折叠态剩余字数（§11）：仅当确实被截断（行数超上限）时返回剩余字数，否则返回 null（不显示入口行）。
 * 计数口径为**渲染后文本**（表格管道符等 markdown 记号不计）。
 */
internal fun jobResultRemainingChars(
    textLength: Int,
    lineCount: Int,
    lastVisibleLineEnd: Int,
    maxLines: Int,
): Int? = if (lineCount > maxLines) {
    (textLength - lastVisibleLineEnd).coerceAtLeast(0)
} else {
    null
}

/** 剩余字数展示格式（千分位，如 1240 → "1,240"）。 */
internal fun formatRemainingChars(count: Int): String = "%,d".format(count)

/**
 * 由**全量 Layout**（不做任何截断）算出折叠度量：未超行数上限时返回 null（不折叠）。
 * 裁剪高度取第 [maxLines] 行的上沿 → 切在行边界（不会切在行中）。
 */
internal fun collapseMetrics(layout: android.text.Layout, textLength: Int, maxLines: Int): CollapseMetrics? {
    // 先判行数上限：不超则直接不折叠（也避免对越界行取偏移——Layout 会 clamp 到文本末尾，但不应依赖）
    if (layout.lineCount <= maxLines) return null
    val remaining = jobResultRemainingChars(
        textLength = textLength,
        lineCount = layout.lineCount,
        lastVisibleLineEnd = layout.getLineEnd(maxLines - 1),
        maxLines = maxLines,
    ) ?: return null
    return CollapseMetrics(
        clipHeightPx = layout.getLineTop(maxLines),
        remainingChars = remaining,
    )
}
