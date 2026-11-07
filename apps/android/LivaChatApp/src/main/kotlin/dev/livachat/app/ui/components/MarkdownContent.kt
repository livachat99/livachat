package dev.livachat.app.ui.components

import android.content.Context
import android.net.Uri
import android.text.Spannable
import android.text.method.LinkMovementMethod
import android.text.style.ClickableSpan
import android.text.util.Linkify
import android.view.MotionEvent
import android.widget.TextView
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import dev.livachat.app.ui.navigation.LocalServerBaseUrl
import dev.livachat.app.ui.navigation.convertLivachatUrls
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.SurfaceVariant
import io.noties.markwon.AbstractMarkwonPlugin
import io.noties.markwon.Markwon
import io.noties.markwon.MarkwonConfiguration
import io.noties.markwon.SoftBreakAddsNewLinePlugin
import io.noties.markwon.core.MarkwonTheme
import io.noties.markwon.ext.tables.TablePlugin
import io.noties.markwon.ext.tasklist.TaskListPlugin
import io.noties.markwon.linkify.LinkifyPlugin

/** 仅放行 http/https 且带 host 的 URL；拒绝 javascript:/file:/data:/intent: 等 scheme 与畸形输入。 */
internal fun String.isHttpUrl(): Boolean {
    if (any { it.isWhitespace() }) return false
    val uri = Uri.parse(this)
    return uri.scheme?.lowercase() in setOf("http", "https") && uri.host != null
}

internal data class MarkdownColors(
    val text: Int,
    val link: Int,
    val codeBg: Int,
)

/** 构建 Markwon 实例（含插件与链接白名单）。独立出来供本地单测直接验证渲染与链接行为。 */
internal fun buildMarkwon(
    context: Context,
    colors: MarkdownColors,
    onLinkClick: (String) -> Unit,
): Markwon = Markwon.builder(context)
    // 仅 URL 自动成链（docs/android_timeline_hotslot.md §13）：库默认掩码 = URL|邮箱|电话，
    // 其中 PHONE_NUMBERS 会把「7,764.70」「9/21 15:17」「(9/14-9/18)」这类数字串误判成电话
    // 并染成链接色（agent 新闻/摘要输出数字密集时整屏蓝字）；邮箱亦无消费场景，一并收掉
    .usePlugin(LinkifyPlugin.create(Linkify.WEB_URLS))
    .usePlugin(TablePlugin.create(context))
    .usePlugin(TaskListPlugin.create(context))
    // 段落间空行原样保留（Markwon 解析表格要求表头前有空行）；段内单个 \n 默认渲染为空格会粘连，这里改为换行
    .usePlugin(SoftBreakAddsNewLinePlugin.create())
    .usePlugin(object : AbstractMarkwonPlugin() {
        override fun configureTheme(builder: MarkwonTheme.Builder) {
            builder
                .linkColor(colors.link)
                .blockQuoteColor(colors.codeBg)
                .codeTextColor(colors.text)
                .codeBlockTextColor(colors.text)
                .codeBlockBackgroundColor(colors.codeBg)
                .listItemColor(colors.text)
                // 聊天场景标题不与正文区分大小：全部按 base 字号渲染，
                // 层级仅靠加粗保留（Markwon 默认 h1 放大到 2.0x）
                .headingTextSizeMultipliers(floatArrayOf(1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f))
        }

        override fun configureConfiguration(builder: MarkwonConfiguration.Builder) {
            builder.linkResolver { _, link ->
                if (link.isHttpUrl()) onLinkClick(link)
            }
        }
    })
    .build()

/**
 * AI 消息 Markdown 渲染隔离层。全 App 唯一 import Markwon 的文件；
 * 未来切换 mikepenz 仅替换本文件内部实现（对外签名不变）。
 */
@Composable
fun MarkdownContent(
    markdown: String,
    onLinkClick: (String) -> Unit,
    modifier: Modifier = Modifier,
    /** 正文非链接区被按下（DOWN）时回调该处的字符偏移——供调用方做位置映射（如全屏定位，§10.2）。 */
    onContentTap: ((charOffset: Int) -> Unit)? = null,
    /** 内部 TextView 创建/每次更新时回调——供调用方读 Layout 做偏移→y 映射或测量（§10.2）。 */
    onTextViewReady: ((TextView) -> Unit)? = null,
) {
    val context = LocalContext.current
    val scaledDensity = LocalContext.current.resources.displayMetrics.scaledDensity
    val textColor = MaterialTheme.colorScheme.onSurface
    val linkColor = BrandIndigo
    val codeBg = SurfaceVariant
    val bodySize = MaterialTheme.typography.bodyLarge.fontSize.value
    val lineHeight = MaterialTheme.typography.bodyLarge.lineHeight.value

    // 经 rememberUpdatedState 转发，回调身份变化不触发 Markwon 重建，且点击时总能拿到最新回调
    val currentOnLinkClick by rememberUpdatedState(onLinkClick)
    val currentOnContentTap by rememberUpdatedState(onContentTap)
    val currentOnTextViewReady by rememberUpdatedState(onTextViewReady)

    val markwon = remember(textColor, linkColor, codeBg) {
        buildMarkwon(
            context = context,
            colors = MarkdownColors(
                text = textColor.toArgb(),
                link = linkColor.toArgb(),
                codeBg = codeBg.toArgb(),
            ),
            onLinkClick = { link -> currentOnLinkClick(link) },
        )
    }

    // 渲染期转换（docs/webroot_feature.md §3.4 D4'）：交 Markwon 前把 livachat_url://webroot/… 转成
    // 实际地址；存储/传输全链路保持 raw。服务端基址经 Local 注入（默认 "" = 不转换）。
    val serverBaseUrl = LocalServerBaseUrl.current
    val renderedMarkdown = remember(markdown, serverBaseUrl) {
        convertLivachatUrls(markdown, serverBaseUrl)
    }

    // 主题变化时 markwon 重建；tag 必须同时绑定 markwon 身份与 markdown，
    // 否则 markdown 未变时会跳过 setMarkdown，烘焙进 span 的配色（代码块底色/引用边线/listItem）不更新
    val renderKey = remember(markwon, renderedMarkdown) { markwon to renderedMarkdown }

    AndroidView(
        factory = { ctx ->
            TextView(ctx).apply {
                setTextColor(textColor.toArgb())
                setLinkTextColor(linkColor.toArgb())
                textSize = bodySize
                // 与 Compose bodyLarge 行高对齐（add 为像素，须按 sp 换算用的 scaledDensity）
                setLineSpacing((lineHeight - bodySize) * scaledDensity, 1f)
                // D13（docs/android_agent_job_view.md §2.2/§4.1）：链接可用 + 非链接触摸冒泡给外层
                // Compose clickable，两处缺一不可——movement method 换成不消费非链接触摸的实现；
                // 且 setMovementMethod 会把 TextView 置 clickable（fixFocusableAndClickableSettings），
                // 必须在其后关掉
                movementMethod = ClickThroughLinkMovementMethod { offset ->
                    currentOnContentTap?.invoke(offset)
                }
                isClickable = false
                isLongClickable = false
                currentOnTextViewReady?.invoke(this)
            }
        },
        update = { view ->
            view.setTextColor(textColor.toArgb())
            view.setLinkTextColor(linkColor.toArgb())
            // 系统字体缩放/主题字号变化时同步
            view.textSize = bodySize
            view.setLineSpacing((lineHeight - bodySize) * scaledDensity, 1f)
            if (view.tag != renderKey) {
                markwon.setMarkdown(view, renderedMarkdown)
                view.tag = renderKey
            }
            currentOnTextViewReady?.invoke(view)
        },
        modifier = modifier,
    )
}

/**
 * 「非链接不消费」的 [LinkMovementMethod]：基类链上 `Touch.onTouchEvent` 对 ACTION_DOWN 无条件
 * 返回 true（登记 DragState 供拖动自动滚动），因此只要装了 movement method，TextView 就吞掉全部
 * 触摸，祖先 Compose `clickable` 收不到手势（docs/android_agent_job_view.md §2.2/§2.7）。
 *
 * 本实现在 **DOWN 未命中 [ClickableSpan]** 时返回 false（不消费 → 冒泡给 Compose）；其余事件
 * （含 UP）一律交回基类——基类的 UP 负责清理 DragState、并在 UP 落点命中链接时调 onClick；
 * 非链接手势因 DOWN 未进基类、没有 DragState，基类 UP/MOVE 自然返回 false，不会误消费。
 *
 * 非链接 **DOWN** 时把落点字符偏移回调给 [onContentTap]（§10.2 的全屏定位用），随后返回 false。
 * 注意必须在 DOWN 记录而不是 UP：**DOWN 不消费后，View 层不再把该手势的 UP/MOVE 下发到本
 * TextView**（ViewGroup 只把后续事件派发给消费了 DOWN 的子 View）——真机实测 UP 钩子永不触发。
 * 拖动会在 DOWN 记录一次（clickable 随后被 cancel → 仅更新记录值，无副作用）。
 * 每 TextView 一个实例（构造注入回调），无内部可变状态（DragState 存在 TextView 的 Spannable 上）。
 */
private class ClickThroughLinkMovementMethod(
    private val onContentTap: ((charOffset: Int) -> Unit)? = null,
) : LinkMovementMethod() {

    override fun onTouchEvent(widget: TextView, buffer: Spannable, event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_DOWN && !hitsLink(widget, buffer, event)) {
            offsetAt(widget, event)?.let { onContentTap?.invoke(it) }
            return false
        }
        // MOVE 不交回基类：基类（Touch）的 MOVE 分支会对"已登记 DragState"（链接上起手）的手势做
        // widget.scrollTo 内部自滚——折叠气泡里 layout 高于视图，一旦自滚内容会内部错位（§11 review）。
        // 本 App 的正文 TextView 从不自滚（滚动一律由 Compose 容器负责），此处锁死该不变量。
        if (event.actionMasked == MotionEvent.ACTION_MOVE) return false
        return super.onTouchEvent(widget, buffer, event)
    }

    /** 复刻 LinkMovementMethod 的命中判定；落点在文本外（padding/越界）判否。 */
    private fun hitsLink(widget: TextView, buffer: Spannable, event: MotionEvent): Boolean {
        val off = offsetAt(widget, event) ?: return false
        return buffer.getSpans(off, off, ClickableSpan::class.java).isNotEmpty()
    }

    /** 事件落点在文本内的字符偏移；落在文本外返回 null（y 越界直接判否——getLineForVertical 不保证越界安全）。 */
    private fun offsetAt(widget: TextView, event: MotionEvent): Int? {
        val layout = widget.layout ?: return null
        val y = event.y.toInt() - widget.totalPaddingTop + widget.scrollY
        if (y < 0 || y > layout.height) return null
        val x = (event.x.toInt() - widget.totalPaddingLeft + widget.scrollX)
            .coerceIn(0, layout.width)
        val line = layout.getLineForVertical(y)
        if (line < 0) return null
        return layout.getOffsetForHorizontal(line, x.toFloat())
    }
}
