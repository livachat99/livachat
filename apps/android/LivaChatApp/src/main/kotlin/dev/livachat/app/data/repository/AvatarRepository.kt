package dev.livachat.app.data.repository

import android.content.Context
import android.net.Uri
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Singleton
class AvatarRepository @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val avatarDir = File(context.filesDir, "avatar")
    private val cameraTempDir = File(context.cacheDir, "avatar_photos")
    private val cropTempFile = File(context.cacheDir, "avatar_crop_temp.jpg")

    suspend fun getAvatarUri(): Uri? = withContext(Dispatchers.IO) {
        avatarDir.listFiles()?.maxByOrNull { it.lastModified() }?.let { Uri.fromFile(it) }
    }

    suspend fun saveAvatarFromUri(sourceUri: Uri): Uri = withContext(Dispatchers.IO) {
        avatarDir.mkdirs()
        // 每次唯一命名：Uri 变化驱动 rememberBitmapFromUri 重新解码（固定名会让第二次换头像后各消费方停留在旧图）
        val newFile = File(avatarDir, "custom_avatar_${System.currentTimeMillis()}.jpg")
        try {
            context.contentResolver.openInputStream(sourceUri)?.use { input ->
                newFile.outputStream().use { output -> input.copyTo(output) }
            } ?: throw IllegalStateException("Cannot open source URI: $sourceUri")
        } catch (e: Exception) {
            newFile.delete()  // 清理半截文件，避免 getAvatarUri 按 lastModified 把它当最新头像
            throw e
        }
        avatarDir.listFiles()?.filter { it != newFile }?.forEach { it.delete() }
        Uri.fromFile(newFile)
    }

    suspend fun removeAvatar() {
        withContext(Dispatchers.IO) { avatarDir.listFiles()?.forEach { it.delete() } }
    }

    /** 删除自建的相机临时源图；处理 FileProvider content URI（拍照链路）与 file URI 两种形态，
     *  且仅限 cacheDir/avatar_photos 下的文件（相册等外部 URI 不动）。 */
    suspend fun deleteCameraSourceIfOwned(uri: Uri) {
        withContext(Dispatchers.IO) {
            val file = when {
                uri.scheme == "file" -> uri.path?.let(::File)
                uri.authority == "${context.packageName}.fileprovider" ->
                    uri.lastPathSegment?.let { File(cameraTempDir, it) }
                else -> null
            } ?: return@withContext
            if (file.parentFile == cameraTempDir) file.delete()
        }
    }

    /** 保存成功后清理裁剪中间产物。 */
    suspend fun cleanupCropTemp() {
        withContext(Dispatchers.IO) { cropTempFile.delete() }
    }
}
