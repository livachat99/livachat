package dev.livachat.app.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary
import dev.livachat.app.ui.util.localizeDigits
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

@Composable
fun MessageBubble(
    message: ChatMessage,
    showCursor: Boolean = false,
    modifier: Modifier = Modifier,
    onLinkClick: ((String) -> Unit)? = null,
    onImageClick: (() -> Unit)? = null,
    /**
     * 非 null ⇒ 整卡可点（仅 agent job 结果气泡由调用方传入，docs/android_agent_job_view.md §2.1）。
     * 参数为本次点击处的正文字符偏移（未记录到则 null，如点的是头行/内边距）——全屏定位用（§10.2）。
     */
    onJobResultClick: ((charOffset: Int?) -> Unit)? = null,
) {
    // 最近一次正文点击的字符偏移：由 MarkdownContent 的 onContentTap 写入（interop UP 派发先于
    // 祖先 clickable，§2.7 时序结论），clickable 的 onClick 读取——仅 job 结果气泡需要
    var tappedCharOffset by remember { mutableStateOf<Int?>(null) }

    val maxWidth = LocalConfiguration.current.screenWidthDp.dp * 0.75f
    val locale = LocalConfiguration.current.locales[0]
    val isUser = message.role == MessageRole.USER
    val isAgent = !isUser && message.source == "agent"

    // §2.3.3 D4=B：模式后缀在展示时按当前语言拼（写入只存正文+mode 标记；
    // 迁移前历史行 mode=null 且后缀已并入 text，原样渲染，不回拆）
    val modeSuffix = modeSuffixResFor(message.mode, message.role)?.let { stringResource(it) }
    val displayText = if (modeSuffix == null) message.text else "${message.text} $modeSuffix"

    val bubbleShape = if (isUser) {
        RoundedCornerShape(16.dp, 16.dp, 16.dp, 8.dp)
    } else {
        RoundedCornerShape(16.dp, 16.dp, 8.dp, 16.dp)
    }

    val alignment = if (isUser) Alignment.End else Alignment.Start
    val arrangement = if (isUser) Arrangement.End else Arrangement.Start

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp),
        horizontalArrangement = arrangement,
    ) {
        if (isUser) {
            Spacer(modifier = Modifier.weight(1f))
        }

        Box(
            modifier = Modifier
                .widthIn(max = maxWidth)
                .clip(bubbleShape)
                .then(
                    if (isUser) {
                        Modifier.background(BrandIndigo)
                    } else {
                        Modifier
                            .background(MaterialTheme.colorScheme.surface)
                            .border(1.dp, SurfaceVariant, bubbleShape)
                    },
                )
                // 整卡可点（仅 agent job 结果气泡）：位于背景/描边之后 → 涟漪受圆角约束并覆盖整卡；
                // 正文区非链接触摸能否到达这里，取决于 MarkdownContent 的 D13 改造（§2.2）
                .then(
                    if (onJobResultClick != null) {
                        Modifier.clickable { onJobResultClick(tappedCharOffset) }
                    } else {
                        Modifier
                    },
                )
                .padding(horizontal = Spacing.md, vertical = Spacing.sm),
        ) {
            Column {
                if (message.image != null && !isUser) {
                    ChatImageView(
                        image = message.image!!,
                        onClick = onImageClick,
                        modifier = Modifier.padding(bottom = Spacing.sm),
                    )
                }

                if (isAgent) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(bottom = Spacing.xs),
                    ) {
                        Icon(
                            imageVector = Icons.Filled.SmartToy,
                            contentDescription = "Agent",
                            tint = TextSecondary,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(
                            text = "Agent: ${formatTime(message.timestamp, locale)}",
                            style = MaterialTheme.typography.labelMedium,
                            color = TextSecondary,
                        )
                        // 方案②：可展开提示（紧贴时间文本，不用 weight 撑行尾——
                        // weight 会把头行撑到约束上限，短结果气泡也会被拉成满宽）
                        if (onJobResultClick != null) {
                            Spacer(modifier = Modifier.width(Spacing.sm))
                            Icon(
                                imageVector = Icons.Filled.OpenInFull,
                                contentDescription = stringResource(R.string.common_view_full_result),
                                tint = TextSecondary,
                                modifier = Modifier.size(14.dp),
                            )
                        }
                    }
                }

                if (isUser || !message.isFinal || onLinkClick == null) {
                    Text(
                        text = displayText,
                        style = MaterialTheme.typography.bodyLarge,
                        color = if (isUser) TextPrimary else MaterialTheme.colorScheme.onSurface,
                    )
                } else {
                    // 共享折叠文本块（§12.3.1）：会话气泡与时间线 agent 行同源。
                    // 折叠门槛 = 「有全屏出口」（onJobResultClick != null）——会话/History 都传了（§12.2/12.3.2）；
                    // 未传出口（如预览）则只平铺，避免长结果看不全
                    AgentJobResultText(
                        markdown = displayText,
                        onLinkClick = onLinkClick,
                        collapsible = onJobResultClick != null,
                        // 仅 job 结果气泡需要记录点击偏移（其余气泡零开销）
                        onContentTap = if (onJobResultClick != null) {
                            { offset -> tappedCharOffset = offset }
                        } else {
                            null
                        },
                    )
                }

                if (showCursor && !message.isFinal) {
                    BlinkingCursor()
                }
            }
        }

        if (!isUser) {
            Spacer(modifier = Modifier.weight(1f))
        }
    }
}

/** 数字经 [localizeDigits]（ar 阿数，docs/android_multilingual.md §20 缺口⑤）。 */
private fun formatTime(instant: Instant, locale: Locale): String =
    localizeDigits(DateTimeFormatter.ofPattern("HH:mm").withZone(ZoneId.systemDefault()).format(instant), locale)

/** D4=B：mode + role → 后缀资源（null = 无后缀）。纯函数，供 JVM 单测直测。 */
internal fun modeSuffixResFor(mode: String?, role: MessageRole): Int? = when (mode) {
    ChatMessage.MODE_TRANSCRIBE -> R.string.conv_msg_suffix_transcript
    ChatMessage.MODE_TRANSLATE ->
        if (role == MessageRole.USER) R.string.conv_msg_suffix_original
        else R.string.conv_msg_suffix_translation
    else -> null
}

@Composable
private fun BlinkingCursor() {
    val transition = rememberInfiniteTransition(label = "cursor")
    val alpha by transition.animateFloat(
        initialValue = 1f,
        targetValue = 0f,
        animationSpec = infiniteRepeatable(
            animation = tween(500),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "cursorAlpha",
    )

    Text(
        text = "▎",
        style = MaterialTheme.typography.bodyLarge,
        color = MaterialTheme.colorScheme.onSurface.copy(alpha = alpha),
    )
}

@Preview(showBackground = true)
@Composable
private fun UserMessagePreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            Column(modifier = Modifier.padding(Spacing.md)) {
                MessageBubble(
                    message = ChatMessage(
                        id = "1", sessionId = "s1", role = MessageRole.USER,
                        text = "你好，今天天气真好！", isFinal = true, seq = 0,
                        timestamp = Instant.now(),
                    ),
                )
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun AiMessagePreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            Column(modifier = Modifier.padding(Spacing.md)) {
                MessageBubble(
                    message = ChatMessage(
                        id = "2", sessionId = "s1", role = MessageRole.AI,
                        text = "是啊，阳光明媚的午后最适合出去走走了。你想去哪里？",
                        isFinal = false, seq = 1,
                        timestamp = Instant.now(),
                    ),
                    showCursor = true,
                )
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun AiMarkdownMessagePreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            Column(modifier = Modifier.padding(Spacing.md)) {
                MessageBubble(
                    message = ChatMessage(
                        id = "3", sessionId = "s1", role = MessageRole.AI,
                        text = "**加粗** 与 [链接](https://example.com)\n\n" +
                            "- 列表项\n\n" +
                            "| A | B |\n|---|---|\n| 1 | 2 |",
                        isFinal = true, seq = 2,
                        timestamp = Instant.now(),
                    ),
                    onLinkClick = {},
                )
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun BothMessagesPreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            Column(modifier = Modifier.padding(Spacing.md)) {
                MessageBubble(
                    message = ChatMessage(
                        id = "1", sessionId = "s1", role = MessageRole.USER,
                        text = "你好！", isFinal = true, seq = 0,
                        timestamp = Instant.now(),
                    ),
                )
                MessageBubble(
                    message = ChatMessage(
                        id = "2", sessionId = "s1", role = MessageRole.AI,
                        text = "你好啊！有什么我可以帮你的？",
                        isFinal = true, seq = 1,
                        timestamp = Instant.now(),
                    ),
                )
            }
        }
    }
}
