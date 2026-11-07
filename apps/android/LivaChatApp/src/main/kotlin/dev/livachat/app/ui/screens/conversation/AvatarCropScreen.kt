package dev.livachat.app.ui.screens.conversation

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.media.applyExifOrientation
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.LivaChatTypography
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

@Composable
fun AvatarCropScreen(
    sourceUri: Uri,
    onConfirm: (Uri) -> Unit,
    onCancel: () -> Unit,
) {
    val context = LocalContext.current
    val density = LocalDensity.current

    val sourceBitmap = remember(sourceUri) {
        try {
            // Decode bounds first to calculate inSampleSize
            val boundsOptions = BitmapFactory.Options().apply {
                inJustDecodeBounds = true
            }
            context.contentResolver.openInputStream(sourceUri)?.use { stream ->
                BitmapFactory.decodeStream(stream, null, boundsOptions)
            }

            val maxDim = maxOf(boundsOptions.outWidth, boundsOptions.outHeight)
            val inSampleSize = if (maxDim > 2048) {
                (maxDim / 2048).coerceAtLeast(1)
            } else {
                1
            }

            val decodeOptions = BitmapFactory.Options().apply {
                this.inSampleSize = inSampleSize
            }
            val decoded = context.contentResolver.openInputStream(sourceUri)?.use { stream ->
                BitmapFactory.decodeStream(stream, null, decodeOptions)
            }
            decoded?.let { applyExifOrientation(context, sourceUri, it) }
        } catch (_: Exception) {
            null
        }
    }

    if (sourceBitmap == null) {
        onCancel()
        return
    }

    val imageBitmap = remember(sourceBitmap) { sourceBitmap.asImageBitmap() }

    val cropCircleDp = 96
    val cropCirclePx = with(density) { cropCircleDp.dp.toPx() }

    // 最小缩放：图片短边覆盖裁剪圆（直径 = 2×cropCirclePx——cropCirclePx 在绘制/clamp/裁剪反算中均为半径）
    val minScale = remember(imageBitmap) {
        val minDim = minOf(imageBitmap.width, imageBitmap.height).toFloat()
        if (minDim > 0) 2 * cropCirclePx / minDim else 1f
    }

    var cropAreaSize by remember { mutableStateOf(IntSize.Zero) }

    // Scale to fill the crop area (screen), maintaining aspect ratio (like ContentScale.Crop)
    val fillScale = if (cropAreaSize.width > 0 && cropAreaSize.height > 0) {
        maxOf(
            cropAreaSize.width.toFloat() / imageBitmap.width,
            cropAreaSize.height.toFloat() / imageBitmap.height,
        )
    } else {
        minScale
    }
    val initialScale = maxOf(minScale, fillScale)

    var scale by remember { mutableFloatStateOf(initialScale) }
    var offsetX by remember { mutableFloatStateOf(0f) }
    var offsetY by remember { mutableFloatStateOf(0f) }

    LaunchedEffect(initialScale) {
        scale = initialScale
        offsetX = 0f
        offsetY = 0f
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Background),
    ) {
        // Top bar
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .statusBarsPadding()
                .padding(Spacing.md),
        ) {
            TextButton(onClick = onCancel) {
                Text(stringResource(R.string.common_cancel), style = LivaChatTypography.labelLarge, color = TextSecondary)
            }
        }

        // Crop area
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .onSizeChanged { cropAreaSize = it }
                .clipToBounds()
                .pointerInput(initialScale) {
                    detectTransformGestures { centroid, pan, zoom, _ ->
                        scale = (scale * zoom).coerceIn(minScale, initialScale * 3f)
                        offsetX += pan.x
                        offsetY += pan.y
                        val maxX = (imageBitmap.width * scale / 2f - cropCirclePx).coerceAtLeast(0f)
                        val maxY = (imageBitmap.height * scale / 2f - cropCirclePx).coerceAtLeast(0f)
                        offsetX = offsetX.coerceIn(-maxX, maxX)
                        offsetY = offsetY.coerceIn(-maxY, maxY)
                    }
                },
            contentAlignment = Alignment.Center,
        ) {
            Canvas(modifier = Modifier.fillMaxSize()) {
                val halfW = size.width / 2
                val halfH = size.height / 2
                val cx = halfW + offsetX
                val cy = halfH + offsetY

                // Draw the image centered with transforms
                withTransform({
                    translate(left = cx, top = cy)
                    scale(scale, scale, Offset.Zero)
                    translate(left = -imageBitmap.width / 2f, top = -imageBitmap.height / 2f)
                }) {
                    drawImage(imageBitmap)
                }

                // Dark overlay outside the crop circle using EvenOdd fill
                val circleRadius = cropCirclePx
                val overlayPath = Path().apply {
                    addRect(Rect(Offset.Zero, size))
                    addOval(Rect(
                        halfW - circleRadius, halfH - circleRadius,
                        halfW + circleRadius, halfH + circleRadius,
                    ))
                    fillType = PathFillType.EvenOdd
                }
                drawPath(overlayPath, color = Color.Black.copy(alpha = 0.6f))

                // Crop circle border
                drawCircle(
                    color = BrandIndigo.copy(alpha = 0.5f),
                    radius = circleRadius,
                    center = Offset(halfW, halfH),
                    style = Stroke(width = 2.dp.toPx()),
                )
            }

            // Hint text
            Text(
                text = stringResource(R.string.conv_avatar_drag_hint),
                style = LivaChatTypography.bodySmall,
                color = TextSecondary,
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = Spacing.md),
            )
        }

        // Bottom button
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = Spacing.lg, vertical = Spacing.md),
        ) {
            Button(
                onClick = {
                    // Intersection of the crop circle's bounding box with the image bounds.
                    // Image-space crop center = (imgW/2 - offsetX/scale, imgH/2 - offsetY/scale).
                    val centerX = imageBitmap.width / 2f - offsetX / scale
                    val centerY = imageBitmap.height / 2f - offsetY / scale
                    val cropRadiusPx = cropCirclePx / scale

                    val cropX = (centerX - cropRadiusPx).toInt().coerceIn(0, imageBitmap.width)
                    val cropY = (centerY - cropRadiusPx).toInt().coerceIn(0, imageBitmap.height)
                    val cropEndX = (centerX + cropRadiusPx).toInt().coerceIn(0, imageBitmap.width)
                    val cropEndY = (centerY + cropRadiusPx).toInt().coerceIn(0, imageBitmap.height)

                    val cropW = cropEndX - cropX
                    val cropH = cropEndY - cropY
                    val size = minOf(cropW, cropH)

                    if (size > 0) {
                        // Center the square within the available crop rect
                        val adjX = cropX + (cropW - size) / 2
                        val adjY = cropY + (cropH - size) / 2
                        val cropped = Bitmap.createBitmap(
                            sourceBitmap,
                            adjX,
                            adjY,
                            size,
                            size,
                        )
                        val tempFile = java.io.File(context.cacheDir, "avatar_crop_temp.jpg")
                        tempFile.outputStream().use { out ->
                            cropped.compress(Bitmap.CompressFormat.JPEG, 90, out)
                        }
                        cropped.recycle()
                        onConfirm(android.net.Uri.fromFile(tempFile))
                    }
                },
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = BrandIndigo),
            ) {
                Text(stringResource(R.string.conv_confirm), style = LivaChatTypography.labelLarge, color = TextPrimary)
            }
        }
    }
}
