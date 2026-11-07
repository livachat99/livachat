package dev.livachat.app.ui.screens.mylife.audiogallery.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.AudioSegment
import dev.livachat.app.media.AudioPlayer
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.audiogallery.formatTimecode
import dev.livachat.app.ui.screens.mylife.audiogallery.segmentBodyText
import dev.livachat.app.ui.theme.Spacing

/**
 * 语音段卡片（docs/android_timeline_audio_gallery.md §4.4）：时间分隔（可选）→ 播放条
 * （播放/暂停按钮 + 可拖进度轨 + 时码）→ **正文单区**（转写 >5 字用转写，否则用摘要）。
 *
 * 播放态是**单一全局态**（§4.5）：只有 `player.key == segment.eventRef` 的卡片显示暂停图标与实时进度，
 * 其余卡片恒为「▶ + 空轨 + 总长」。时长优先用播放器实测，未就绪时回落段 `duration_s`。
 */
@Composable
internal fun AudioSegmentCard(
    segment: AudioSegment,
    timeLabel: String?,
    player: AudioPlayer.State,
    scrubMs: Long?,
    onToggle: () -> Unit,
    onScrub: (Long) -> Unit,
    onScrubEnd: (Long) -> Unit,
    onTimeClick: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val isActive = player.key == segment.eventRef
    val isPlaying = isActive && player.isPlaying
    val durationMs = if (isActive && player.durationMs > 0L) player.durationMs else segment.durationMs
    // 拖动中优先显示本地 scrub 位置（**即使该段还没开始播**——拖动即会切段播放，§4.5 修订）
    val positionMs = scrubMs ?: if (isActive) player.positionMs else 0L
    val fraction = if (durationMs > 0L) {
        (positionMs.toFloat() / durationMs).coerceIn(0f, 1f)
    } else {
        0f
    }
    val error = if (isActive) player.error else null

    Column(modifier = modifier.fillMaxWidth()) {
        // 时间头（§9）：时间 + ›› 整组可点 → eventListViewer（该时刻 ±10 分钟）
        timeLabel?.let { AudioTimeHeader(label = it, onTimeClick = onTimeClick) }
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = MylifeColors.Panel.copy(alpha = 0.6f),
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = Spacing.md, vertical = Spacing.xs),
        ) {
            Column(modifier = Modifier.padding(Spacing.md)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .clip(CircleShape)
                            .background(MylifeColors.Panel.copy(alpha = 0.9f))
                            .clickable(onClick = onToggle, role = Role.Button)
                            .padding(Spacing.xs),
                    ) {
                        Icon(
                            imageVector = if (isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                            contentDescription = if (isPlaying) stringResource(R.string.common_pause) else stringResource(R.string.common_play),
                            tint = MylifeColors.Gold,
                            modifier = Modifier.size(22.dp),
                        )
                    }
                    Spacer(modifier = Modifier.width(Spacing.sm))
                    SegmentProgressBar(
                        fraction = fraction,
                        durationMs = durationMs,
                        onScrub = onScrub,
                        onScrubEnd = onScrubEnd,
                        // 带高 32→40dp（§10.4：与包络柱共用同一条带）
                        modifier = Modifier.weight(1f).height(40.dp),
                        // 全卡可拖/可点（§4.5 修订 2026-09-24）：非播放段由 VM 先切段再定位，
                        // 避免「进屏未播放时整屏拖不动」
                        interactive = true,
                        envelope = segment.energy,
                    )
                    Spacer(modifier = Modifier.width(Spacing.sm))
                    Text(
                        text = if (isActive && durationMs > 0L) {
                            "${formatTimecode(positionMs)} / ${formatTimecode(durationMs)}"
                        } else {
                            formatTimecode(durationMs)
                        },
                        style = MaterialTheme.typography.labelMedium,
                        color = MylifeColors.Dust,
                    )
                }
                if (error != null) {
                    Spacer(modifier = Modifier.height(Spacing.xs))
                    Text(
                        text = error,
                        style = MaterialTheme.typography.bodySmall,
                        color = MylifeColors.Red,
                    )
                }
                // 正文单区（§4.4）：转写 >5 字显示转写，否则显示摘要；两者皆无则不渲染
                segmentBodyText(segment.transcript, segment.summary)?.let {
                    Spacer(modifier = Modifier.height(Spacing.sm))
                    Text(
                        text = it,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MylifeColors.Star,
                    )
                }
            }
        }
    }
}
