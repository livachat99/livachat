package dev.livachat.app.ui.screens.conversation

import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import dev.livachat.app.R
import dev.livachat.app.ui.theme.LivaChatTypography
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

/**
 * 图库选图全屏预览（docs/camera_enhance.md §10，B 方案）：
 * 黑底；左返回重新打开 gallery、右取消回主屏幕；无中间提示；
 * 捏合 1x–5x + 放大拖动；底部悬浮「发送」。只有图片 onSuccess 后才允许发送。
 * 缩放仅用于查看，发送的是完整照片（最终由 GalleryPhotoSender 规范化）。
 */
@Composable
fun GalleryPhotoPreviewScreen(
    uri: Uri,
    onSend: () -> Unit,
    onCancel: () -> Unit,
    onBackToPicker: () -> Unit,
) {
    // 系统返回 = 左侧返回（§10.9 P11）：重新打开 gallery 继续选择
    BackHandler { onBackToPicker() }

    // 换图（左返回重新选择）时重置缩放/平移/加载态，避免沿用上一张图的 loaded=true
    var scale by remember(uri) { mutableFloatStateOf(ImageViewerMath.MIN_SCALE) }
    var offset by remember(uri) { mutableStateOf(Offset.Zero) }
    var intrinsic by remember(uri) { mutableStateOf(IntSize.Zero) }
    var loaded by remember(uri) { mutableStateOf(false) }
    var failed by remember(uri) { mutableStateOf(false) }
    var viewport by remember { mutableStateOf(IntSize.Zero) }

    val fitSize = remember(intrinsic, viewport) {
        if (intrinsic.width > 0 && intrinsic.height > 0) {
            ImageViewerMath.fitSize(intrinsic.width, intrinsic.height, viewport)
        } else {
            Size.Zero
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black),
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .statusBarsPadding()
                    .height(56.dp)
                    .padding(horizontal = Spacing.sm),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = onBackToPicker) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = stringResource(R.string.common_back),
                        tint = TextPrimary,
                    )
                }
                Spacer(modifier = Modifier.weight(1f))
                TextButton(onClick = onCancel) {
                    Text(
                        text = stringResource(R.string.common_cancel),
                        style = LivaChatTypography.labelLarge,
                        color = TextSecondary,
                    )
                }
            }

            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .clipToBounds()
                    .onSizeChanged { viewport = it }
                    .pointerInput(uri, fitSize) {
                        detectTransformGestures { centroid, pan, zoom, _ ->
                            if (fitSize != Size.Zero) {
                                val oldScale = scale
                                val newScale = ImageViewerMath.clampScale(oldScale * zoom)
                                offset = ImageViewerMath
                                    .offsetAfterZoom(offset, centroid, viewport, oldScale, newScale, pan)
                                    .let { ImageViewerMath.clampOffset(it, fitSize, newScale, viewport) }
                                scale = newScale
                            }
                        }
                    },
                contentAlignment = Alignment.Center,
            ) {
                AsyncImage(
                    model = uri,
                    contentDescription = stringResource(R.string.conv_gallery_preview_image),
                    contentScale = ContentScale.Fit,
                    onLoading = {
                        loaded = false
                        failed = false
                    },
                    onSuccess = { state ->
                        val drawable = state.result.drawable
                        intrinsic = IntSize(drawable.intrinsicWidth, drawable.intrinsicHeight)
                        loaded = true
                        failed = false
                    },
                    onError = {
                        loaded = false
                        failed = true
                    },
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer {
                            scaleX = scale
                            scaleY = scale
                            translationX = offset.x
                            translationY = offset.y
                        },
                )
                if (!loaded && !failed) {
                    CircularProgressIndicator(
                        color = TextSecondary,
                        modifier = Modifier.size(32.dp),
                    )
                }
                if (failed) {
                    Text(
                        text = stringResource(R.string.conv_gallery_preview_failed),
                        style = LivaChatTypography.bodyLarge,
                        color = TextSecondary,
                    )
                }
            }
        }

        Button(
            onClick = onSend,
            enabled = loaded && !failed,
            shape = RoundedCornerShape(50),
            // 用户定稿：灰底白字；禁用态灰暗 + 次级文字色
            colors = ButtonDefaults.buttonColors(
                containerColor = SurfaceVariant,
                contentColor = TextPrimary,
                disabledContainerColor = SurfaceVariant.copy(alpha = 0.5f),
                disabledContentColor = TextSecondary,
            ),
            contentPadding = PaddingValues(horizontal = Spacing.xl),
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .navigationBarsPadding()
                .padding(bottom = Spacing.xl)
                .height(44.dp),
        ) {
            Text(
                text = stringResource(R.string.conv_gallery_send),
                style = LivaChatTypography.labelLarge,
            )
        }
    }
}
