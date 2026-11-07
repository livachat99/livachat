package dev.livachat.app.ui.components

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.util.Base64
import android.util.Log
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.sdk.model.AiImage

@Composable
fun ChatImageView(
    image: AiImage,
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
) {
    val bitmap = remember(image.data, image.orientation) { decodeAiImageBitmap(image) } ?: return

    val aspect = bitmap.width.toFloat() / bitmap.height

    Image(
        bitmap = bitmap.asImageBitmap(),
        contentDescription = stringResource(R.string.chat_ai_image),
        modifier = modifier
            .fillMaxWidth()
            .aspectRatio(aspect)
            .clip(RoundedCornerShape(12.dp))
            .then(if (onClick != null) Modifier.clickable { onClick() } else Modifier),
        contentScale = ContentScale.FillWidth,
    )
}

/**
 * 气泡与全屏查看器共用的解码：Base64 → Bitmap，按 orientation 旋转。
 * 一切失败（非法 Base64 / 非图片字节 / 空数据）返回 null，不抛异常——调用方在组合中执行，
 * 抛出会直接炸组合；损坏数据可达（服务端异常帧、历史版本落库数据）。
 */
internal fun decodeAiImageBitmap(image: AiImage): Bitmap? {
    val bytes = runCatching { Base64.decode(image.data, Base64.DEFAULT) }.getOrNull() ?: return null
    val original = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
    val degrees = image.orientation

    Log.println(Log.DEBUG, "LivaChat-ChatImageView", "before matrix: w=${original.width} h=${original.height} orientation=$degrees")

    if (degrees == 0) return original

    val matrix = Matrix().apply { postRotate(degrees.toFloat()) }
    val rotated = Bitmap.createBitmap(original, 0, 0, original.width, original.height, matrix, true)
    if (rotated != original) original.recycle()
    Log.println(Log.DEBUG, "LivaChat-ChatImageView", "after matrix: w=${rotated.width} h=${rotated.height} orientation=$degrees")
    return rotated
}
