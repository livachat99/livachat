package dev.livachat.app.ui.components

import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import androidx.compose.animation.Crossfade
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import dev.livachat.app.ui.theme.AvatarGradientEnd
import dev.livachat.app.ui.theme.AvatarGradientStart
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.Info
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.LivaChatTypography
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Success
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.Warning
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.cos
import kotlin.math.sin

enum class AvatarState { IDLE, CONNECTING, LISTENING, THINKING, SPEAKING }

val AvatarState.haloColor: Color
    get() = when (this) {
        AvatarState.IDLE -> Success
        AvatarState.CONNECTING -> Warning
        AvatarState.LISTENING -> Info
        AvatarState.THINKING -> Warning
        AvatarState.SPEAKING -> Success
    }

@Composable
fun AiAvatar(
    state: AvatarState,
    customAvatarBitmap: ImageBitmap? = null,
    onClick: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val haloColor by animateColorAsState(
        targetValue = state.haloColor,
        animationSpec = tween(200),
        label = "haloColor",
    )

    val clickModifier = if (onClick != null) {
        Modifier.clickable(
            interactionSource = remember { MutableInteractionSource() },
            indication = null,
            onClick = onClick,
        )
    } else {
        Modifier
    }

    Crossfade(targetState = state, animationSpec = tween(300), label = "avatarAnim") { currentState ->
        Box(
            modifier = modifier
                .size(120.dp)
                .then(clickModifier),
            contentAlignment = Alignment.Center,
        ) {
            if (currentState != AvatarState.IDLE) {
                AvatarHalo(
                    state = currentState,
                    color = haloColor,
                )
            }

            Box(
                modifier = Modifier
                    .size(96.dp)
                    .clip(CircleShape)
                    .background(
                        Brush.linearGradient(
                            colors = listOf(AvatarGradientStart, AvatarGradientEnd),
                        ),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                if (customAvatarBitmap != null) {
                    androidx.compose.foundation.Image(
                        bitmap = customAvatarBitmap,
                        contentDescription = stringResource(R.string.avatar_custom_image_desc),
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop,
                    )
                } else {
                    Text(
                        text = "~",
                        style = dev.livachat.app.ui.theme.LivaChatTypography.headlineLarge.copy(
                            color = dev.livachat.app.ui.theme.TextPrimary,
                        ),
                    )
                }
            }
        }
    }
}

/** 标题栏头像按钮（docs/fd_avator_customization.md）：有自定义头像时显示头像照片，否则初始样式
 *  （渐变 + "~"）的圆形按钮；视觉 Ø36；48×48 触控层（同 AgentJobStatusBadge 模式）。
 *  [overlay] 槽在**不裁剪**的外层盒上（调用方对齐 BottomEnd）：模式徽章悬于圆外右下侧、不被头像圆裁切。 */
@Composable
fun AvatarTitleBarButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    customAvatarBitmap: ImageBitmap? = null,
    overlay: @Composable BoxScope.() -> Unit = {},
) {
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier
                .size(48.dp)
                .clip(CircleShape)
                .clickable(onClick = onClick),
        ) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(
                        Brush.linearGradient(listOf(AvatarGradientStart, AvatarGradientEnd)),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                if (customAvatarBitmap != null) {
                    Image(
                        bitmap = customAvatarBitmap,
                        contentDescription = stringResource(R.string.avatar_custom_image_desc),
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop,
                    )
                } else {
                    Text("~", style = LivaChatTypography.headlineLarge, color = TextPrimary)
                }
            }
        }
        overlay()
    }
}

@Composable
fun rememberBitmapFromUri(uri: Uri?): ImageBitmap? {
    val context = LocalContext.current
    var bitmap by remember { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(uri) {
        if (uri == null) {
            bitmap = null
            return@LaunchedEffect
        }
        withContext(Dispatchers.IO) {
            try {
                context.contentResolver.openInputStream(uri)?.use { stream ->
                    bitmap = BitmapFactory.decodeStream(stream)?.asImageBitmap()
                }
            } catch (_: Exception) {
                bitmap = null
            }
        }
    }
    return bitmap
}

@Composable
private fun AvatarHalo(
    state: AvatarState,
    color: Color,
    modifier: Modifier = Modifier,
) {
    val transition = rememberInfiniteTransition(label = "halo")
    val density = LocalDensity.current

    when (state) {
        AvatarState.IDLE -> {
            val scale by transition.animateFloat(
                initialValue = 1.00f, targetValue = 1.03f,
                animationSpec = infiniteRepeatable(tween(2000), repeatMode = RepeatMode.Reverse),
                label = "idleScale",
            )
            Canvas(
                modifier = Modifier
                    .size(120.dp)
                    .scale(scale),
            ) {
                val strokePx = with(density) { 4.dp.toPx() }
                drawCircle(color = color, radius = size.minDimension / 2, style = Stroke(width = strokePx))
            }
        }

        AvatarState.CONNECTING -> {
            val alpha by transition.animateFloat(
                initialValue = 0.5f, targetValue = 1.0f,
                animationSpec = infiniteRepeatable(tween(800), repeatMode = RepeatMode.Reverse),
                label = "connectAlpha",
            )
            Canvas(modifier = Modifier.size(120.dp)) {
                val strokePx = with(density) { 4.dp.toPx() }
                drawCircle(color = color.copy(alpha = alpha), radius = size.minDimension / 2, style = Stroke(width = strokePx))
            }
        }

        AvatarState.LISTENING -> {
            val delays = listOf(0, 500, 1000)
            val phases = delays.map { d ->
                transition.animateFloat(
                    initialValue = 0f, targetValue = 1f,
                    animationSpec = infiniteRepeatable(
                        tween(1500, delayMillis = d),
                        repeatMode = RepeatMode.Restart,
                    ),
                    label = "ripple$d",
                )
            }
            val p0 by phases[0]
            val p1 by phases[1]
            val p2 by phases[2]
            val ripplePhases = listOf(p0, p1, p2)

            Canvas(modifier = Modifier.size(120.dp)) {
                val strokePx = with(density) { 3.dp.toPx() }
                ripplePhases.forEach { phase ->
                    val alpha = (1f - phase).coerceAtLeast(0f)
                    drawCircle(
                        color = color.copy(alpha = alpha * 0.4f),
                        radius = size.minDimension / 2 * (0.6f + phase * 0.4f),
                        style = Stroke(width = strokePx),
                    )
                }
            }
        }

        AvatarState.THINKING -> {
            val angle by transition.animateFloat(
                initialValue = 0f, targetValue = 360f,
                animationSpec = infiniteRepeatable(tween(1200), repeatMode = RepeatMode.Restart),
                label = "thinkAngle",
            )
            val particleCount = 8

            Canvas(modifier = Modifier.size(120.dp)) {
                val orbitPx = with(density) { 50.dp.toPx() }
                val particlePx = with(density) { 3.dp.toPx() }
                for (i in 0 until particleCount) {
                    val a = Math.toRadians((angle + i * 360f / particleCount).toDouble())
                    val cx = center.x + orbitPx * cos(a).toFloat()
                    val cy = center.y + orbitPx * sin(a).toFloat()
                    drawCircle(color = color, radius = particlePx, center = Offset(cx, cy))
                }
            }
        }

        AvatarState.SPEAKING -> {
            val scale1 by transition.animateFloat(
                initialValue = 0.7f, targetValue = 1.2f,
                animationSpec = infiniteRepeatable(tween(600), repeatMode = RepeatMode.Reverse),
                label = "speakRipple",
            )
            Canvas(modifier = Modifier.size(120.dp)) {
                val strokePx = with(density) { 3.dp.toPx() }
                val alpha = (1f - scale1 / 1.2f).coerceIn(0f, 0.5f)
                drawCircle(
                    color = color.copy(alpha = alpha),
                    radius = size.minDimension / 2 * scale1,
                    style = Stroke(width = strokePx),
                )
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun AiAvatarAllStatesPreview() {
    LivaChatTheme {
        Surface(color = Background) {
            Column(
                modifier = Modifier.padding(Spacing.md),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(Spacing.lg),
            ) {
                AvatarState.entries.forEach { state ->
                    AiAvatar(state = state)
                }
            }
        }
    }
}
