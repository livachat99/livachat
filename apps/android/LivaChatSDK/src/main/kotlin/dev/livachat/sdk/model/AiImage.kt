package dev.livachat.sdk.model

/** Image data carried in a final ai_text message when video is enabled. */
data class AiImage(
    /** Base64-encoded JPEG image data. */
    val data: String,
    /** MIME type (e.g., "image/jpeg"). */
    val mime: String,
    /** Image width in pixels. */
    val width: Int,
    /** Image height in pixels. */
    val height: Int,
    /** Camera frame rotation in degrees (0, 90, 180, 270). */
    val orientation: Int = 0,
    /** Which camera captured the frame: "front" or "back". */
    val cameraFacing: String = "front",
)
