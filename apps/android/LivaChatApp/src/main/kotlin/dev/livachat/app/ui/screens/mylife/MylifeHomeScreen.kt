package dev.livachat.app.ui.screens.mylife

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import dev.livachat.app.ui.screens.mylife.timeline.TimelineScreen
import dev.livachat.app.ui.theme.Spacing
import kotlin.random.Random

/** Web body::before 星点背景的 Compose 等价（静态无动画，固定 seed 确定性分布）。 */
@Composable
private fun StarfieldBackground(modifier: Modifier = Modifier) {
    BoxWithConstraints(modifier = modifier) {
        val starCount = (maxWidth.value * maxHeight.value / 9000).toInt().coerceIn(24, 90)
        val stars = remember(maxWidth.value, maxHeight.value, starCount) {
            val rnd = Random(42)
            List(starCount) {
                Triple(
                    rnd.nextFloat() * maxWidth.value,
                    rnd.nextFloat() * maxHeight.value,
                    rnd.nextFloat() * 1.4f + 0.6f,
                )
            }
        }
        Canvas(modifier = Modifier.fillMaxSize()) {
            for ((x, y, r) in stars) {
                drawCircle(
                    color = Color.White.copy(alpha = 0.10f * r),
                    radius = r * 1.6f,
                    center = Offset(x, y),
                )
            }
        }
    }
}

/**
 * MyLife 宿主：单视图（2026-09-22 §14 R1——移除星图 tab 与底栏）。
 * TimelineScreen 的 ViewModel 以本 backstack entry 为 owner（默认 hiltViewModel()）。
 */
@Composable
fun MylifeHomeScreen(
    onNavigateToGallery: (from: String, to: String, label: String, focusFrameId: String?, pending: Boolean) -> Unit,
    onNavigateToHotSlot: (startMs: Long, endMs: Long, pending: Boolean) -> Unit,
    onNavigateToEventList: (tsMs: Long, pending: Boolean) -> Unit,
    onNavigateToAudio: (date: String, label: String, count: Int, pending: Boolean) -> Unit,
    onNavigateBack: () -> Unit,
) {
    MylifeTheme {
        Box(modifier = Modifier.fillMaxSize()) {
            StarfieldBackground(modifier = Modifier.fillMaxSize())
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .statusBarsPadding()
                    .navigationBarsPadding(),
            ) {
                // 顶部标题（2026-09-22 §14 R2：改「时光机」，去 ✦）
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = Spacing.sm, vertical = Spacing.xs),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = onNavigateBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.common_back), tint = MylifeColors.Dust)
                    }
                    Text(
                        text = stringResource(R.string.common_time_machine),
                        style = MaterialTheme.typography.titleLarge,
                        color = MylifeColors.Gold,
                        modifier = Modifier.padding(start = Spacing.xs),
                    )
                }

                Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                    TimelineScreen(
                        onNavigateToGallery = onNavigateToGallery,
                        onNavigateToHotSlot = onNavigateToHotSlot,
                        onNavigateToEventList = onNavigateToEventList,
                        onNavigateToAudio = onNavigateToAudio,
                    )
                }
            }
        }
    }
}
