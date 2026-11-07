# Feature Design: AI Avatar 自定义照片

## Context

AiAvatar 支持展示用户自定义照片（从相册选择 / 拍照 → 裁剪 → 本地持久化），点击头像弹出 `AvatarCustomizationSheet` 面板；头像数据仅存本地，SDK 无改动。

**Sheet 修改（仅涉及 `AvatarCustomizationSheet.kt` 一个文件）**：

1. 「头像选择」item 间距过大——去掉 `SheetOption` 的上下 8dp padding，item pitch 64dp → 48dp；
2. 「头像选择」标题右侧新增「正常｜缩小」胶囊按钮（逻辑接线见「头像隐藏到标题栏」）。

## App UI 设计语言 (Theme Reference)

所有新增 UI 必须遵循 app 现有深色主题规范：

| Token | 值 | 用途 |
|-------|-----|------|
| `Background` | `#0F172A` | 页面背景 |
| `Surface` | `#1E293B` | 卡片/底部弹出面板背景 |
| `SurfaceVariant` | `#334155` | 分隔线/次级表面 |
| `TextPrimary` | `#F8FAFC` | 标题、主文字 |
| `TextSecondary` | `#94A3B8` | 副文字、图标 tint |
| `BrandIndigo` | `#6366F1` | 主色调 |
| `BrandViolet` | `#8B5CF6` | 辅助色调 |
| `AvatarGradientStart/End` | `#6366F1 / #8B5CF6` | 头像默认渐变（值与 BrandIndigo/Violet 相同） |

| Typography | Size / Weight | 用途 |
|------------|---------------|------|
| `headlineLarge` | 28sp Bold | 大标题 |
| `titleLarge` | 22sp SemiBold | 面板标题、页面标题 |
| `bodyLarge` | 16sp Normal | 正文 |
| `labelLarge` | 14sp Medium | 按钮文字、选项标签 |
| `bodySmall` | 12sp Normal | 辅助说明 |

| Spacing | 值 |
|---------|-----|
| `xs` | 4dp |
| `sm` | 8dp |
| `md` | 16dp |
| `lg` | 24dp |
| `xl` | 32dp |
| `xxl` | 48dp |

## Overview

```
ConversationScreen
  └─ AiAvatar（点击）
       └─ AvatarCustomizationSheet (ModalBottomSheet)
            ├─ 「同声翻译」4 个模式开关（对话/听众/转录/全模态 → 各 Toggle 流程）
            ├─ 「从相册选择」 → PickVisualMedia → AvatarCropScreen → 确认 → 保存 → 更新 UI
            ├─ 「拍照」       → TakePicture（必要时请求 CAMERA 权限）→ AvatarCropScreen → 确认 → 保存 → 更新 UI
            └─ 「恢复默认头像」（仅已有自定义头像时显示）→ 删除本地文件 → 恢复默认显示
```

## 涉及文件

| 文件 | 职责 |
|------|------|
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/components/AiAvatar.kt` | AI 头像组件（自定义照片展示 / 默认渐变 + `~` / 状态光环）+ `rememberBitmapFromUri` 解码辅助 |
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/conversation/AvatarCustomizationSheet.kt` | 底部面板：同声翻译开关 + 头像选择（**本次修改唯一涉及文件**） |
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/conversation/AvatarCropScreen.kt` | 全屏裁剪页：拖拽/缩放 + 圆形遮罩，确认后产出裁剪图 URI |
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/data/repository/AvatarRepository.kt` | 自定义头像文件读写（保存/读取/删除） |
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/conversation/ConversationUiState.kt` | avatar 相关状态字段 |
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/conversation/ConversationEvent.kt` | avatar 相关事件 |
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/conversation/ConversationViewModel.kt` | 头像加载/保存/删除/裁剪流转 |
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/conversation/ConversationScreen.kt` | 选图/拍照 launcher、裁剪页与面板渲染、AiAvatar 调用点 |
| `apps/android/LivaChatApp/src/androidTest/kotlin/dev/livachat/app/ui/screens/conversation/AvatarCustomizationSheetTest.kt` | 面板 UI 的 instrumented 回归（行距 ≤50dp、胶囊位置与无行为、同声翻译区完整） |

## 详细设计

### 1. AvatarRepository — 本地文件存储

```kotlin
// data/repository/AvatarRepository.kt
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
        // 唯一命名（custom_avatar_<ts>.jpg）：Uri 变化驱动 rememberBitmapFromUri 重新解码；
        // 先写新文件、成功后清理旧文件（失败不丢原头像）
        ...
    }

    suspend fun removeAvatar() {
        withContext(Dispatchers.IO) { avatarDir.listFiles()?.forEach { it.delete() } }
    }

    /** 删除自建的相机临时源图；仅限 cacheDir/avatar_photos 下的文件（相册 URI 不动）。 */
    suspend fun deleteCameraSourceIfOwned(uri: Uri) { ... }

    /** 保存成功后清理裁剪中间产物。 */
    suspend fun cleanupCropTemp() {
        withContext(Dispatchers.IO) { cropTempFile.delete() }
    }
}
```

- 正式存储：`filesDir/avatar/custom_avatar_<ts>.jpg`，应用私有，无需存储权限；**每次保存唯一命名**（Uri 必然变化），保存成功后仅保留最新一份（`maxByOrNull(lastModified)` 兼容历史遗留的 `custom_avatar.jpg`）
- 全部读写为 `suspend` + `withContext(Dispatchers.IO)`（与 `rememberBitmapFromUri` 的 IO 纪律一致）
- 中间产物归属与主动清理：裁剪结果 `cacheDir/avatar_crop_temp.jpg`（AvatarCropScreen 写入，保存成功后 `cleanupCropTemp()` 清理）；拍照原图 `cacheDir/avatar_photos/camera_<ts>.jpg`（FileProvider 临时文件：crop 确认/取消时 `deleteCameraSourceIfOwned()` 清理，**系统相机内取消拍照**（`TakePicture` success=false）同样经 `discardCameraSource` 清理）

### 2. AiAvatar 组件

```kotlin
@Composable
fun AiAvatar(
    state: AvatarState,
    customAvatarBitmap: ImageBitmap? = null,
    onClick: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
)
```

- 外层容器 120dp 不变，内圆 96dp（`CircleShape` clip）
- `customAvatarBitmap != null` → 圆内 `Image(bitmap, contentScale = ContentScale.Crop)` 展示照片；`null` → 现有默认：`Brush.linearGradient(AvatarGradientStart → AvatarGradientEnd)` + `~` 字符（headlineLarge/TextPrimary）
- 状态光环 `AvatarHalo` 只由 `state` 驱动（IDLE 不画环），不受自定义照片影响；`Crossfade(300ms)` 切换状态
- `onClick != null` → 整个 120dp 组件可点击，`clickable` 无 ripple（`indication = null`）
- 配套 `AvatarTitleBarButton`（隐藏态标题栏按钮）：视觉 Ø36 圆——有自定义头像时显示照片、否则渐变 + `"~"`；48×48 触控层——详见「头像隐藏到标题栏」§3
- 配套 `rememberBitmapFromUri(uri: Uri?): ImageBitmap?`：IO 线程用 `BitmapFactory` 解码本地 URI，URI 为 null 或解码失败时返回 null

### 3. 会话状态与事件

```kotlin
// ConversationUiState.kt
val customAvatarUri: Uri? = null,        // 已保存的自定义头像
val avatarVisible: Boolean = true,       // 大头像可见性（false = 隐藏到标题栏）
val showAvatarSheet: Boolean = false,    // 自定义面板可见性
val showAvatarCrop: Boolean = false,     // 裁剪页可见性
val avatarCropSourceUri: Uri? = null,    // 裁剪源图（选图/拍照结果）

// ConversationEvent.kt
data object ShowAvatarSheet : ConversationEvent()
data object DismissAvatarSheet : ConversationEvent()
data class SetAvatarVisible(val visible: Boolean) : ConversationEvent()
data object RemoveCustomAvatar : ConversationEvent()
data object StartAvatarCrop : ConversationEvent()
data object CancelAvatarCrop : ConversationEvent()
```

### 4. ConversationViewModel 处理逻辑

- `init`：`viewModelScope.launch { customAvatarUri = avatarRepository.getAvatarUri() }` —— 启动时恢复
- `ShowAvatarSheet` / `DismissAvatarSheet` → 置 `showAvatarSheet`
- `StartAvatarCrop` → `showAvatarSheet = false, showAvatarCrop = true`；`CancelAvatarCrop` → `showAvatarCrop = false, avatarCropSourceUri = null`，并清理自建相机临时源图
- `setAvatarCropSourceUri(uri)`：记录选图/拍照源 URI（裁剪页渲染前提）
- `onCropConfirmed(uri)`：`saveCustomAvatar(uri)` + 关闭裁剪页 + 清理自建相机临时源图（裁剪页 onConfirm 直达，不经事件）
- `saveCustomAvatar(uri)`：`avatarRepository.saveAvatarFromUri(uri)` → `customAvatarUri = 新 uri, showAvatarSheet = false` + `cleanupCropTemp()`；失败 → `ConversationError.Unknown("保存头像失败: …")`
- `RemoveCustomAvatar` → `removeCustomAvatar()`：`avatarRepository.removeAvatar()` → `customAvatarUri = null, showAvatarSheet = false`
- `SetAvatarVisible(visible)`：「头像隐藏到标题栏」的状态接线（见该节 §1）
- 各模式的确认分支（`ConfirmTranslateLanguage` / `ConfirmLectureLanguage` / `ConfirmTranscribeSource` / `ConfirmOmniMode`）会同时置 `showAvatarSheet = false`

### 5. ConversationScreen — 入口与渲染

- 选图：`galleryLauncher = rememberLauncherForActivityResult(PickVisualMedia())`，结果非空 → `setAvatarCropSourceUri(uri) + StartAvatarCrop`
- 拍照：`launchAvatarPhotoCapture()` 在 `cacheDir/avatar_photos/` 建临时文件并 `FileProvider.getUriForFile` → `cameraLauncher = TakePicture()`；成功 → `setAvatarCropSourceUri(uri) + StartAvatarCrop`，取消（success=false）→ `discardCameraSource(uri)` 清理临时文件。CAMERA 权限未授权时先经 `RequestPermission` launcher 请求；权限被拒 / 系统拒绝 / 无相机应用（`ActivityNotFoundException`）统一收敛到权限引导弹窗，不裸崩
- 裁剪页（全屏覆盖，渲染后 `return`）：

```kotlin
val cropSourceUri = uiState.avatarCropSourceUri
if (uiState.showAvatarCrop && cropSourceUri != null) {
    AvatarCropScreen(
        sourceUri = cropSourceUri,
        onConfirm = { croppedUri -> viewModel.onCropConfirmed(croppedUri) },
        onCancel = { viewModel.onEvent(ConversationEvent.CancelAvatarCrop) },
    )
    return
}
```

- 面板渲染：`if (uiState.showAvatarSheet) { AvatarCustomizationSheet(...) }`，传入 `hasCustomAvatar`、两个 launcher 回调、`RemoveCustomAvatar`/`DismissAvatarSheet`，以及 4 个模式开关的状态与 Toggle 事件（对话/听众/转录/全模态）
- AiAvatar 调用点（会话页顶部；`avatarBitmap` 在所在 composable 内由 `rememberBitmapFromUri` 解码）：

```kotlin
val avatarBitmap = rememberBitmapFromUri(uiState.customAvatarUri)
...
// 仅可见态渲染大头像区；隐藏态（avatarVisible = false）整块不渲染，消息区上移（128dp）
if (uiState.avatarVisible) {
    Box(modifier = Modifier.padding(top = Spacing.sm)) {
        AiAvatar(
            state = mapSessionToAvatarState(uiState),
            customAvatarBitmap = avatarBitmap,
            onClick = { viewModel.onEvent(ConversationEvent.ShowAvatarSheet) },
        )
        // 同 Box 内右侧为 AgentJobStatusBadge（offset 摆放）；右上为 ModeBadge（26dp 圆）
    }
}
```

### 6. AvatarCropScreen — 图片裁剪

签名 `AvatarCropScreen(sourceUri: Uri, onConfirm: (Uri) -> Unit, onCancel: () -> Unit)`，全屏单页 Compose。

- **解码**：`BitmapFactory` 先 `inJustDecodeBounds` 探尺寸，长边 > 2048 时按 `inSampleSize` 降采样；**解码后按源图 EXIF orientation 校正**（旋转 90/180/270 + 镜像/转置共 8 态，`androidx.exifinterface`）；解码失败直接 `onCancel()`
- **手势**：`detectTransformGestures` 单指拖拽 + 双指缩放；缩放范围 `[minScale, initialScale × 3]`（`minScale` = 图片短边**覆盖裁剪圆**，即短边 = 圆直径 2×`cropCirclePx`；`initialScale` = 图片填满裁剪区）；偏移 clamp 保证图片边缘不进入裁剪圆（不留空白）
- **视觉**：背景 `Background`；裁剪圆**半径** 96dp（Ø192dp——`cropCirclePx` 在绘制/偏移 clamp/裁剪反算三处一致按半径使用；与 AiAvatar 内圆 Ø96 尺寸不同，最终显示经圆形裁切、取景一致），圆外 `Color.Black.copy(alpha = 0.6f)` 遮罩（EvenOdd path），圆环 `BrandIndigo.copy(alpha = 0.5f)` 2dp stroke；顶部居中提示「拖动图片调整位置」（bodySmall/TextSecondary）；左上角「取消」TextButton（labelLarge/TextSecondary）
- **确认裁剪**：按当前 scale/offset 反算图像空间中心 `(imgW/2 - offsetX/scale, imgH/2 - offsetY/scale)` 与裁剪半径 `cropCirclePx/scale`，取与图片边界的交集后裁正方形 → 写 `cacheDir/avatar_crop_temp.jpg`（JPEG 90）→ `onConfirm(该文件 Uri)`
- **底部按钮**：「确认」，`fillMaxWidth().height(48dp)`，`RoundedCornerShape(12dp)`，`BrandIndigo` 填充 + `TextPrimary` 文字

### 7. AvatarCustomizationSheet — 现状结构

`ModalBottomSheet(containerColor = Surface, 顶部圆角 24dp, skipPartiallyExpanded)`，内容为：

```
┌────────────────────────────────────────────┐
│  SectionHeader「同声翻译」(labelLarge/BrandIndigo) │
│  SwitchRow ×4：对话模式 / 听众模式 / 转录模式 / 全模态 │
│    （title labelLarge/TextPrimary + subtitle        │
│      bodySmall/TextSecondary + Switch）             │
│  HorizontalDivider (SurfaceVariant, 上下 8dp)       │
│  SectionHeader「头像选择」                          │
│  SheetOption「从相册选择」/「拍照」                   │
│  SheetOption「恢复默认头像」(hasCustomAvatar 时显示)  │
└────────────────────────────────────────────┘
```

- `SwitchRow`：`padding(vertical = sm)` + 48dp Switch，行 pitch 64dp（由 Switch 高度决定，**本次不动**）；Switch track 色 checked `BrandIndigo` / unchecked `SurfaceVariant`
- `SheetOption`（现状）：`padding(vertical = sm) + height(48dp)` → item pitch 64dp，而单行文字只占 20dp，视觉间距过大（本次修改对象）
- 头像项点击后立即收起面板（`onClick` 内 `onDismiss()`，随后进入选图/拍照/恢复默认）；模式开关翻动时不收起面板，其确认流程（选语言/音源、`ConfirmOmniMode`）提交时才一并 `showAvatarSheet = false`

### 8. AvatarCustomizationSheet — 修改内容

#### 8.1 item 间距 64dp → 48dp

去掉 `SheetOption` 的 `padding(vertical = Spacing.sm)`，保留 48dp 高度（行紧贴、触控目标恰 48dp，满足最小触控规范）：

```kotlin
@Composable
private fun SheetOption(
    icon: @Composable () -> Unit,
    text: String,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .height(48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        icon()
        Spacer(Modifier.width(Spacing.md))
        Text(text, style = LivaChatTypography.labelLarge, color = TextPrimary)
    }
}
```

#### 8.2 「头像选择」右侧「正常｜缩小」胶囊按钮

`SectionHeader` 增加可选 trailing 槽位（默认 null，「同声翻译」不受影响）：

```kotlin
@Composable
private fun SectionHeader(
    title: String,
    trailing: (@Composable () -> Unit)? = null,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = Spacing.xs),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, style = LivaChatTypography.labelLarge, color = BrandIndigo)
        trailing?.invoke()
    }
}
```

胶囊（选中段 = `BrandIndigo` 底 + `TextPrimary` 字；未选段 = `TextSecondary` 纯字）：

```kotlin
@Composable
private fun ShowHideToggle(visible: Boolean, onToggle: (Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .background(SurfaceVariant)
            .padding(2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PillSegment("正常", selected = visible, onClick = { onToggle(true) })
        PillSegment("缩小", selected = !visible, onClick = { onToggle(false) })
    }
}

@Composable
private fun PillSegment(text: String, selected: Boolean, onClick: () -> Unit) {
    Text(
        text = text,
        style = LivaChatTypography.labelLarge,     // 14sp Medium
        color = if (selected) TextPrimary else TextSecondary,
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .clickable(onClick = onClick)
            .background(if (selected) BrandIndigo else Color.Transparent)
            .padding(horizontal = 12.dp, vertical = 4.dp),
    )
}
```

调用处：

```kotlin
SectionHeader("头像选择", trailing = { ShowHideToggle(visible = avatarVisible, onToggle = onAvatarVisibleChange) })
```

规格：

- 构造式尺寸：容器 = 段 + 上下各 2dp 内边距；段 = 单行文字行盒 + 垂直 4dp + 水平 12dp。绝对高度随字体行盒浮动——真机实测容器 ≈28dp（单行文字行盒 16.3dp），Robolectric 渲染 ≈33dp（行盒取 lineHeight 20dp）
- 容器底 `SurfaceVariant`、全圆角；标题行高由 ~20dp 增至 ~32dp（内容区 +12dp）
- **接线**：两段 `clickable`，选中态随 `avatarVisible`；点击发射 `SetAvatarVisible`、不关面板（详见「头像隐藏到标题栏」§1；触控维持段视觉 28dp，决策 #7）

### 9. DI

`AvatarRepository` 为 `@Singleton` + `@Inject constructor(@ApplicationContext)`，Hilt 自动绑定，`AppModule` 无需手动 `@Provides`（现状即如此）。

## 数据流总结

```
启动：avatarRepository.getAvatarUri() → uiState.customAvatarUri → AiAvatar 显示自定义照片

User tap avatar → ShowAvatarSheet → 面板弹出

「从相册选择」 → PickVisualMedia → setAvatarCropSourceUri + StartAvatarCrop
「拍照」       → TakePicture（权限不足先请求）→ setAvatarCropSourceUri + StartAvatarCrop
                     ↓
             AvatarCropScreen（拖拽/缩放）→「确认」→ 裁剪写 cacheDir 临时 jpg
                     ↓
             onCropConfirmed(uri) → saveAvatarFromUri（复制到 filesDir）→ customAvatarUri 更新 → AiAvatar 更换照片
                     ↓（「取消」）
             CancelAvatarCrop → 返回会话页，无变化

「恢复默认头像」 → RemoveCustomAvatar → 删除 filesDir 文件 → customAvatarUri = null → AiAvatar 恢复渐变 "~"
```

## Verification

1. **首次点击 avatar** → 面板弹出：同声翻译 4 开关 + 「从相册选择」「拍照」，「恢复默认头像」不显示
2. **从相册选择照片** → 系统相册 → 裁剪页（拖动/缩放，圆外遮罩）→ 确认 → 返回会话页，avatar 显示自定义照片
3. **拍照**（含首次 CAMERA 权限请求流程；拒绝时弹引导不崩溃）→ 裁剪 → 确认 → avatar 显示照片
4. **再次点击 avatar** → 面板显示「恢复默认头像」→ 点击 → avatar 恢复默认 `~`
5. **裁剪页取消** → 返回会话页，头像不变
6. **kill 应用重启** → 自定义头像仍显示（`filesDir/avatar/` 持久化）
7. **面板修改**：「头像选择」三项间距 48dp（无项间空隙）；标题右侧胶囊「正常｜缩小」两段可点、发射 `SetAvatarVisible`（见「头像隐藏到标题栏」）；同声翻译开关等其余功能回归正常
8. **自动化回归**（androidTest，真机）：`AvatarCustomizationSheetTest` 断言 item 行距 ≤50dp（改前 64dp）、胶囊位于标题右侧且两段点击分别发射 `onAvatarVisibleChange(false/true)`、同声翻译区完整——断言模式镜像 `MicDevicePickerTest`/`OutputDevicePickerTest` 的行距用例
9. **相机横置设备拍照**（三星/小米等 EXIF 90° 机型）→ 裁剪页方向正确 → 保存后头像方向正确（EXIF 校正）
10. **正常/缩小**（见「头像隐藏到标题栏」）：面板选「缩小」→ 消息上移、标题栏出现头像按钮，模式激活时徽章在其右下；点按钮开面板选「正常」恢复；kill 重启保持缩小态
11. **连续更换头像**（第二次及以上）→ 大盘与标题栏按钮**均**显示最新照片（唯一文件名驱动重解码；真机已验证）
12. **裁剪页捏合到最小档** → 圆内不留空、可沿长轴拖动、确认结果与所见一致（`minScale = 2×cropCirclePx/minDim`；instrumented 回归 `AvatarCropScreenPinchTest`，真机双向验证：旧公式失败/修复通过）
13. **系统相机内取消拍照** → `cacheDir/avatar_photos` 无新增残留（`discardCameraSource` 清理；Repository 删除映射与 VM 转发均有单测，真机实走取消无残留）

---

## 头像隐藏到标题栏

### 需求

1. 面板「正常｜缩小」接线：「正常」= 大头像居中显示；「缩小」= 收缩到标题栏按钮。
2. 隐藏后：头像从会话区收缩到**标题栏中区域**——**圆形按钮**（**有自定义头像时显示头像照片，否则初始样式：渐变紫底 + 白色「~」**）；位置固定居中、不随状态文案变化；点击按钮打开头像面板；面板选「正常」恢复原位置与样式。
3. 隐藏后消息区向上占满（头像区 128dp 让给对话列表）。
4. 同声翻译某模式激活时，模式图标位于头像按钮**右下侧**（悬于圆外、不被头像圆裁切）；有 agent job 时，job 图标与数量出现在按钮右侧。
5. 渲染对照图见 `/tmp/avatar_hide_mock/`（Robolectric NATIVE，392dp 宽，与真机同宽）。

### 现状分析（codebase）

**标题栏**（`ConversationScreen.kt:335`，外有覆盖层 `Box` `:334`）：`TopAppBar`——`title` = `StatusIndicator`（左：8dp 圆点 + 状态文案如「我在听...」，`StatusIndicator.kt:78-113`），`actions` = 录制 + 更多。

**大头像区**（`ConversationScreen.kt:944-971`）：
- `Box(padding top = 8dp)` 内：`AiAvatar(120dp, onClick = ShowAvatarSheet)` + `AgentJobStatusBadge`（`offset(x = AvatarSize + 8dp)`，`AvatarSize = 120.dp` @ `:1027`）+ 模式徽章（26dp 圆、`TopEnd`，`if/else`：Translate / Subtitles / AutoAwesome）
- **消息区**：`LazyColumn(weight(1f))`（`:973`）——头像 Box 不渲染时自动上移 128dp（120 + 8）

**复用组件规格**：

| 组件 | 规格 | 出处 |
|------|------|------|
| AiAvatar 初始样式 | `linearGradient(AvatarGradientStart → AvatarGradientEnd)` + `"~"`（headlineLarge/TextPrimary） | `AiAvatar.kt:107-132` |
| AgentJobStatusBadge | `count > 0` 时显示；28dp 胶囊（SmartToy 14dp + 数字）+ 48dp 触控热区；点击 = 开 job 面板 | `AgentJobStatusBadge.kt:43-90` |
| 模式徽章 | 26dp 圆、BrandIndigo 0.7α、1.5dp Surface 描边、14dp 图标 | `ConversationScreen.kt:1034`（ModeBadge） |
| StatusIndicator | 8dp 圆点 + labelLarge 文案 | `StatusIndicator.kt:78-113` |

**面板 pill**（`AvatarCustomizationSheet.kt:155-178`）：「正常｜缩小」胶囊——本节的接线对象（实现见 §1）。

**持久化基建**：`UserPreferences`（DataStore）布尔字段的成熟模式（`PreferencesDataSource.kt` 映射/键/update 三处 + `SettingsRepository.kt`）——新增 1 字段成本 = 1 个 key + 映射 1 行 + update 函数 + repo 包装。

### 设计

#### 1. 状态与事件

- `ConversationUiState` 新增 `avatarVisible: Boolean = true`（默认 true = 现状，老行为零变化）
- `ConversationEvent` 新增 `data class SetAvatarVisible(val visible: Boolean)`
- **极性统一**：UiState / 事件 / 面板参数 / `UserPreferences` 全部使用 `avatarVisible`（true = 显示）同名同义——边界处零取反，消除"点显示变隐藏"类接线错误
- ViewModel 处理：乐观更新 `uiState.avatarVisible`（**不关面板**——pill/子项随状态实时切换）；`settingsRepository.setAvatarVisible(visible)` 持久化（DataStore）；启动/变更恢复 = 现有 `prefsState` 收集处（`ConversationViewModel.kt:265` 附近）镜像 `prefs.avatarVisible` → `uiState.avatarVisible`
- 参数链路：`AvatarCustomizationSheet` 增公开参数 `avatarVisible: Boolean = true` + `onAvatarVisibleChange: (Boolean) -> Unit = {}`；ConversationScreen 调用处直传（无取反）：

```kotlin
AvatarCustomizationSheet(
    ...,
    avatarVisible = uiState.avatarVisible,
    onAvatarVisibleChange = { viewModel.onEvent(ConversationEvent.SetAvatarVisible(it)) },
)
```

- 面板内 pill：`ShowHideToggle(visible = avatarVisible, onToggle = onAvatarVisibleChange)`

**触控热区（决策 #7 = ②：维持现状）**：不做 48dp 扩展——两段各自 `clickable`（热区 = 段视觉区 52×28dp），标题行高与胶囊视觉完全同已拍板渲染。已知偏离 48dp 最小触控规范，为本次**唯一**接受偏离项（无代价可达 48dp 的控件——标题栏按钮、job 徽章——均已达标）；扩展方案（`minimumInteractiveComponentSize` 撑高容器至 52dp / 48dp 覆盖层加高标题行至 52dp）均舍弃，取舍证据见 `pill_touch.png`

#### 2. 标题栏布局 = 覆盖层固定居中（渲染 `bar_redesign.png`、`bar_fixed_center.png`）

保持 `TopAppBar` 不变（`title` 槽 = 纯 `StatusIndicator`，与可见态同构）；隐藏态在 **TopAppBar 外层 `Box`** 叠一个居中簇——头像锚定**屏中线**，位置不随状态文案长短、徽章出现/消失而移动：

```kotlin
Box {                                          // ConversationScreen.kt:334
    TopAppBar(title = { StatusIndicator(...) }, actions = { ... })
    if (!uiState.avatarVisible) {
        Row(
            modifier = Modifier
                .align(Alignment.Center)
                .offset(x = (AvatarBarJobSlot + Spacing.sm) / 2),   // 扣除 job 固定槽，头像贴屏中线
            verticalAlignment = Alignment.CenterVertically,
        ) {
            AvatarTitleBarButton(onClick = ...) { ModeBadge(..., size = 20.dp, iconSize = 11.dp) }
            Spacer(Modifier.width(Spacing.sm))
            Box(Modifier.width(AvatarBarJobSlot), contentAlignment = Alignment.CenterStart) {
                AgentJobStatusBadge(...)                        // 固定预留槽：出现/消失不移动头像
            }
        }
    }
}
```

392dp 渲染实测（`bar_fixed_center.png`）：「我在听...」「随时可以说」「正在连接...」三档文案下圆心恒为 **195.8dp = 屏中线**（逐像素一致）；与左侧状态文案的最大侵入裕量 ≥76dp。真机同验（按钮文字节点圆心 588px = 196dp，恰屏中线）。

历次方案弃用理由：weight 剩余空间居中（192–200dp ±4dp 随文案漂移，与"位置固定"要求冲突）；更早的槽内居中（A 碰撞）、`CenterAlignedTopAppBar`（B 换组件动现状）亦不再采用。

#### 3. 标题栏头像按钮（渲染 `variants_zoom.png`、`bar_redesign.png`）

按钮 = **自定义头像照片（有则用，`ContentScale.Crop`）/ 初始头像样式（渐变 + 「~」）**的圆形版；`overlay` 槽在**不裁剪**的外层盒上，模式徽章悬于头像右下圆外：

```kotlin
@Composable
fun AvatarTitleBarButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    customAvatarBitmap: ImageBitmap? = null,
    overlay: @Composable BoxScope.() -> Unit = {},
) {
    Box(modifier = modifier, contentAlignment = Alignment.Center) {   // 不裁剪外层：overlay 悬于圆外
        Box(  // 48×48 触控层（同 AgentJobStatusBadge 模式）
            contentAlignment = Alignment.Center,
            modifier = Modifier.size(48.dp).clip(CircleShape).clickable(onClick = onClick),
        ) {
            Box(  // 视觉 Ø36 圆
                modifier = Modifier
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(Brush.linearGradient(listOf(AvatarGradientStart, AvatarGradientEnd))),
                contentAlignment = Alignment.Center,
            ) {
                if (customAvatarBitmap != null) {
                    Image(customAvatarBitmap, contentDescription = "自定义头像", modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                } else {
                    Text("~", style = LivaChatTypography.headlineLarge, color = TextPrimary)
                }
            }
        }
        overlay()
    }
}
```

落点 `AiAvatar.kt`（与初始样式同源）；ConversationScreen 以 `barAvatarBitmap = rememberBitmapFromUri(uiState.customAvatarUri)` 传入；点击 → `ShowAvatarSheet`。

尺寸（已定，变体 C）：视觉 **Ø36 圆** + 「~」(headlineLarge)；触控 **48×48dp**；模式徽章 **20dp**（图标 11dp，`BottomEnd` 对齐外层盒右下角、不被头像圆裁切——徽章圆心 = 头像中心 +14dp/+14dp，真机实测 630,237 vs 头像 588,195，放大图见 `verify_badge_br_zoom.png`）。圆尺寸变体 A（Ø32+26）过挤、B（Ø36+26）偏挤，对照见 `variants_zoom.png`。

#### 4. 隐藏后的页面与徽章

- 隐藏态（`avatarVisible = false`）：头像 `Box`（`:945`）整块不渲染；`LazyColumn` 自然上移（渲染 `hidden_page.png`）
- 面板：`头像选择` 子项（从相册选择 / 拍照 / 恢复默认头像）在「正常/缩小」两态均展示；切换 pill 不关面板，选中态实时切换（真机已验证）
- 隐藏态簇 = `[圆形头像按钮（自定义照片/初始样式；模式徽章悬于右下、不裁切）] 8dp [AgentJobStatusBadge 固定 48dp 预留槽]`；job 徽章点击 = `ToggleJobStatusPanel`（不变）；触控：按钮 48×48、job 徽章 48dp 高（现制）
- 模式图标映射沿用现状：对话/听众 → Translate；转录 → Subtitles；全模态 → AutoAwesome
- `ModeBadge` 已抽共享（`ConversationScreen.kt:1034`）：大头像 26dp / 标题栏覆盖 20dp 两档
- 可见态：布局与徽章挂载完全同现状
- 切换过渡：瞬时（不做动画）

#### 5. 恢复显示

面板 pill 选「正常」→ `avatarVisible = true` → 大头像区恢复，标题栏簇消失（徽章回大头像）。

### 设计决策（已拍板）

| # | 项 | 决定 |
|---|----|------|
| 1 | 标题栏布局 | **TopAppBar 外层 Box 覆盖层、簇固定居中**：头像锚定屏中线（实测恒定 195.8dp），不随状态文案/徽章出现移动；`title` 槽保持纯 `StatusIndicator` |
| 2 | 按钮尺寸 | **视觉 Ø36 圆**（触控 48×48dp；模式徽章覆盖 20dp——变体 C） |
| 3 | 隐藏状态持久化 | **持久化**：`UserPreferences.avatarVisible`（默认 true），重启保持 |
| 4 | 点「正常/缩小」 | **立即生效、不关闭面板**（pill 选中态实时切换；子项两态均展示；VM 只更新 `avatarVisible`） |
| 5 | 过渡动画 | **不做**（瞬时切换） |
| 6 | 「~」字号 | **沿用 headlineLarge**（与初始头像同源） |
| 7 | 触控热区 | 统一原则：**无代价可达 48dp 的控件一律达标**。① 标题栏按钮：**48×48dp** 触控层（视觉 Ø36 圆居中，零视觉/布局影响）；② sheet 内 pill：**维持 28dp**（扩展有视觉/行高代价，为本次唯一接受偏离项）；模式徽章纯视觉、不作要求 |

### 涉及文件

| 文件 | 改动 |
|------|------|
| `ConversationScreen.kt` | `TopAppBar` 外层 `Box` 覆盖层（固定居中簇 + job 固定槽）；头像 `Box` 条件渲染；`ModeBadge` 共享；面板调用处传参 |
| `AvatarCustomizationSheet.kt` | 公开参数 `avatarVisible`/`onAvatarVisibleChange`；`ShowHideToggle` 增 `visible`/`onToggle`，两段各加 `clickable`（不做触控扩展） |
| `AiAvatar.kt` | `AvatarTitleBarButton`：视觉 Ø36 圆 + 48×48 触控 + `overlay` 槽（模式徽章覆盖） |
| `ConversationUiState.kt` / `ConversationEvent.kt` / `ConversationViewModel.kt` | `avatarVisible` + `SetAvatarVisible`（极性统一，无取反；+ 持久化读写） |
| `UserPreferences.kt` / `PreferencesDataSource.kt` / `SettingsRepository.kt` | +1 布尔字段 `avatarVisible`（默认 true；DataStore 现成模式） |
| 单元测试 | `ConversationViewModelTest` 增用例：`SetAvatarVisible` → 状态更新 + `showAvatarSheet = false` + prefs 写入/镜像恢复；`SettingsRepositoryTest`/`PreferencesDataSourceTest` 增 `avatarVisible` 默认值与读写（均为仓库现成模式） |
| androidTest | `AvatarCustomizationSheetTest`：pill 点击 → 事件发射 + 选中态跟随；隐藏态（`avatarVisible = false`）头像选择子项不存在的断言（`avatar_items_absent_when_avatar_invisible`）；行距/同声翻译区回归 |

### 渲染对照图（/tmp/avatar_hide_mock/）

| 文件 | 内容 |
|------|------|
| `bar_redesign.png` | 一键对照：现状（矩形，簇随文案漂移）vs A（Ø32+26）/ B（Ø36+26）/ C（Ø36+20，已定） |
| `variants_zoom.png` | A/B/C 头像簇放大对比（徽章与「~」遮挡关系） |
| `bar_fixed_center.png` | 固定居中验证：三档文案下圆心恒 195.8dp = 屏中线 |
| `hidden_page.png` | 现状 vs 隐藏后整页（消息上移 128dp；bar 为旧矩形版） |
| `pill_touch.png` | pill 触控取舍（决策 #7：维持 28dp；②③扩展方案均不采用） |
| `/tmp/verify_badge_br_zoom.png` | 真机放大：模式徽章悬于头像右下、完整圆形不被裁切（最终形态） |

（渲染用临时 Robolectric 工具，已删；图片留 /tmp/avatar_hide_mock/。）
