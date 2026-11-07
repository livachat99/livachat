# Feature Design: AI Text with Image (Video Chat v1)

> **Status**: Implemented
> **Date**: 2026-06-30

---

## 1. Feature Overview

When the client sends video frames (camera images) to the server, the server passes them to a vision-capable LLM. The LLM generates a text response based on what it "sees." The image that was shown to the LLM is included in the **final** `ai_text` message, so the client can display it alongside the AI reply.

### Scope

- **Final ai_text only**: Only the final chunk (`is_final=true`) carries the image.
- **Single image**: The sliding window holds at most 1 frame (the latest).
- **Backward compatible**: All new fields are optional; old clients ignore them.

---

## 2. WebSocket Message Design

### 2.1 Schema: `common.json`

```jsonc
"Orientation": { "type": "integer", "enum": [0, 90, 180, 270] },
"CameraFacing": { "type": "string", "enum": ["front", "back"] }
```

### 2.2 Schema: `c2s/video.json`

Payload added `camera_facing` field. Required fields: `data`, `frame_id`, `width`, `height`, `orientation`, `camera_facing`, `quality`.

### 2.3 Schema: `s2c/ai-text.json`

Payload added optional `image` object:

```jsonc
"image": {
  "type": "object",
  "required": ["data", "mime", "width", "height", "orientation", "camera_facing"],
  "properties": {
    "data":        { "type": "string" },           // base64-encoded JPEG
    "mime":        { "type": "string", "const": "image/jpeg" },
    "width":       { "type": "integer" },           // original pixel width
    "height":      { "type": "integer" },           // original pixel height
    "orientation": { "$ref": "Orientation" },       // 0/90/180/270
    "camera_facing": { "$ref": "CameraFacing" }     // "front"/"back"
  }
}
```

### 2.4 Wire Format

**Final chunk (with image):**

```json
{
  "type": "ai_text",
  "seq": 35, "timestamp": 1712345686000,
  "payload": {
    "text": "我看到桌子上有一杯咖啡。",
    "message_id": "msg_abc123456789",
    "index": 5, "is_final": true,
    "image": {
      "data": "/9j/4AAQSkZJRg...base64...",
      "mime": "image/jpeg",
      "width": 640, "height": 480,
      "orientation": 270,
      "camera_facing": "front"
    }
  }
}
```

Intermediate chunks (`is_final=false`) have no `image` field.

---

## 3. Server Design

### 3.1 Changes

| File | Change |
|------|--------|
| `services/session/context.py` | `latest_video_frames` tuple: `(frame_id, jpeg_bytes, ts, width, height, orientation, camera_facing)` — 7-tuple |
| `services/session/orchestrator.py` | `_handle_video`: store all 7 fields; `_start_thinking`: snapshot last frame, build `ServerAiTextImage`, pass on final chunk |

### 3.2 Snapshot Logic (`_start_thinking`)

```python
if self._ctx.enable_video and self._ctx.latest_video_frames:
    _, raw_jpeg, _, width, height, orientation, camera_facing = \
        self._ctx.latest_video_frames[-1]
    latest_image_payload = ServerAiTextImage(
        data=base64.b64encode(raw_jpeg).decode(),
        mime="image/jpeg",
        width=width, height=height,
        orientation=orientation,
        camera_facing=camera_facing,
    )
```

The final `_send_ai_text` call includes `image=latest_image_payload`. Image is omitted when `None` (no video / no frames).

### 3.3 Passthrough

Server does not consume `orientation` or `camera_facing` — these are transparently passed from client `video` message to server `ai_text` response.

---

## 4. Client SDK Design

### 4.1 New Types

**`AiImage`** (`dev.livachat.sdk.model`):

```kotlin
data class AiImage(
    val data: String,        // base64-encoded JPEG
    val mime: String,        // "image/jpeg"
    val width: Int,
    val height: Int,
    val orientation: Int,    // 0, 90, 180, 270
    val cameraFacing: String, // "front" | "back"
)
```

**`Orientation`** enum updated with `degrees` property:

```kotlin
enum class Orientation(val degrees: Int) {
    PORTRAIT_0(0), LANDSCAPE_90(90),
    PORTRAIT_180(180), LANDSCAPE_270(270),
}
```

### 4.2 Public API Changes

| Method | Change |
|--------|--------|
| `pushVideoJpeg` | Added `cameraFacing: String = "front"` |
| `pushVideoBitmap` | Added `cameraFacing: String = "front"` |
| `onAiText` callback | Added `image: AiImage? = null` |

### 4.3 Data Flow (Client Send)

```
CameraController.processFrame()
  → rotationDegreesToSdkOrientation(CameraX.rotationDegrees)
  → sdk.pushVideoJpeg(jpeg, w, h, orientation, "front"/"back")
  → VideoSendPipeline.onEncoded(jpeg, frameId, w, h, orientation, cameraFacing)
  → ClientVideoPayload(orientation=orientation.degrees, cameraFacing=cameraFacing)
  → WebSocket JSON
```

### 4.4 Data Flow (Client Receive)

```
WebSocket JSON
  → MessageCodec.decode() → ServerMessage.AiText
  → SessionManager.dispatchMessage()
  → AiImage(data, mime, w, h, orientation, cameraFacing)
  → listenerDispatcher.onAiText(text, msgId, index, isFinal, image)
  → ConversationListenerAdapter → ChatMessage.image
```

---

## 5. Client App Design

### 5.1 Camera Orientation Detection

`CameraController` uses `OrientationEventListener` to dynamically update `ImageAnalysis.targetRotation`, enabling CameraX to compute correct `rotationDegrees` for both portrait and landscape:

```kotlin
private val orientationEventListener = object : OrientationEventListener(context) {
    override fun onOrientationChanged(orientation: Int) {
        if (orientation == ORIENTATION_UNKNOWN) return
        imageAnalysis?.targetRotation = UseCase.snapToSurfaceRotation(orientation)
    }
}
```

Enabled on `bind()`, disabled on `unbind()`.

### 5.2 Room Persistence

Images are stored as JPEG files on disk (`chat_images/{msg_id}.jpg`). Room stores metadata only:

| Column | Type | Description |
|--------|------|-------------|
| `imagePath` | String? | Absolute file path |
| `imageMime` | String? | "image/jpeg" |
| `imageWidth` | Int? | Pixels |
| `imageHeight` | Int? | Pixels |
| `imageOrientation` | Int? | 0/90/180/270 |
| `imageCameraFacing` | String? | "front"/"back" |

### 5.3 UI: `ChatImageView`

**Approach**: Pre-rotate bitmap with `Matrix.postRotate()`, then use `aspectRatio = bitmap.width / bitmap.height`. No Compose `rotate` modifier.

```kotlin
// w,h are intrinsic properties that never change.
// Matrix rotation handles content orientation.
val bitmap = remember(image.data, degrees) {
    val original = BitmapFactory.decodeByteArray(bytes, ...)
    if (degrees == 0) original
    else {
        val matrix = Matrix().apply { postRotate(degrees.toFloat()) }
        Bitmap.createBitmap(original, ..., matrix, true)
    }
}
val aspect = bitmap.width / bitmap.height  // reflects rotation
Image(bitmap, modifier = fillMaxWidth().aspectRatio(aspect).clip(...),
      contentScale = ContentScale.FillWidth)
```

**Result for all 4 scenarios (640×480 sensor):**

| Scene | degrees | Matrix | bitmap | aspect | Display |
|-------|---------|--------|--------|--------|---------|
| Front portrait | 270 | rotate 270° | 480×640 | 0.75 | Tall portrait |
| Front landscape | 0 | no rotation | 640×480 | 1.33 | Wide strip |
| Back portrait | 90 | rotate 90° | 480×640 | 0.75 | Tall portrait |
| Back landscape | 0 | no rotation | 640×480 | 1.33 | Wide strip |

### 5.4 UI Layout

```
┌─────────────────────────────────┐
│  ┌───────────────────────────┐  │
│  │     Camera Image (JPEG)   │  │  ← ChatImageView
│  └───────────────────────────┘  │
│  "我看到桌子上有一杯咖啡..."     │  ← Text
└─────────────────────────────────┘
```

---

## 6. End-to-End Data Flow

```
CameraX (YUV 640×480, rotationDegrees)
  → OrientationEventListener → setTargetRotation → CameraX recalculates rotationDegrees
  → CameraController.processFrame()
  → pushVideoJpeg(jpeg, w, h, orientation, cameraFacing)
  → ClientVideoPayload → WebSocket
  → Server: latest_video_frames deque (7-tuple)
  → _start_thinking: snapshot → ServerAiTextImage
  → WebSocket → ServerMessage.AiText
  → Client SDK: SessionManager → AiImage
  → Room: chat_images/{msg_id}.jpg + metadata
  → ChatImageView: Matrix.postRotate(degrees) → Image
```

## 7. Key Decisions

1. **Image stored as file, not in Room** — avoids 200KB+ base64 blobs in SQLite. File path in `imagePath` column.
2. **Matrix pre-rotation, not Compose rotate** — bitmap pixels rotated before layout, so `aspectRatio` works naturally with rotated dimensions. No clipping/overflow issues.
3. **`OrientationEventListener` + `setTargetRotation`** — required for CameraX to correctly compute `rotationDegrees` across portrait/landscape. Without this, degrees are fixed per-camera on some devices.
4. **Image only on final chunk** — avoids 200KB+ on every streaming token. Single image per AI reply.
5. **`cameraFacing` for future disambiguation** — currently not needed for display logic (degrees alone suffices after `setTargetRotation`), but provides context for potential future uses.
