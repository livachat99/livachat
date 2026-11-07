package dev.livachat.app.mock.media

import android.content.Context
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import dev.livachat.sdk.LivaChatSDK
import java.io.File
import java.util.concurrent.Executors

/**
 * Manages camera capture via CameraX and feeds JPEG frames to the SDK.
 *
 * Uses file-based capture (compatible with CameraX 1.3.x).
 */
class CameraController(
    private val sdk: LivaChatSDK,
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
) {
    private val captureExecutor = Executors.newSingleThreadExecutor()
    private var imageCapture: ImageCapture? = null
    private var provider: ProcessCameraProvider? = null

    @Volatile
    var isActive: Boolean = false
        private set

    fun start() {
        if (isActive) return
        isActive = true

        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            provider = future.get()
            bind(provider!!)
        }, ContextCompat.getMainExecutor(context))
    }

    private fun bind(cameraProvider: ProcessCameraProvider) {
        imageCapture = ImageCapture.Builder()
            .setTargetResolution(android.util.Size(1080, 1920))
            .build()

        cameraProvider.unbindAll()
        cameraProvider.bindToLifecycle(
            lifecycleOwner,
            CameraSelector.DEFAULT_FRONT_CAMERA,
            imageCapture,
        )
    }

    fun captureFrame() {
        val capture = imageCapture ?: return
        if (!isActive) return

        val photoFile = File.createTempFile("livachat_frame_", ".jpg", context.cacheDir)
        val outputOptions = ImageCapture.OutputFileOptions.Builder(photoFile).build()

        capture.takePicture(
            outputOptions,
            captureExecutor,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    val bitmap = android.graphics.BitmapFactory.decodeFile(photoFile.absolutePath)
                    if (bitmap != null) {
                        sdk.pushVideoBitmap(
                            bitmap, dev.livachat.sdk.model.Orientation.PORTRAIT_0,
                        )
                        bitmap.recycle()
                    }
                    photoFile.delete()
                }

                override fun onError(exception: ImageCaptureException) {
                    photoFile.delete()
                }
            },
        )
    }

    fun stop() {
        isActive = false
        imageCapture = null
        provider?.unbindAll()
        provider = null
    }
}
