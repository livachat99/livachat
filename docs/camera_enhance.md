# LivaChat 摄像头增强设计稿（v3 工具栏闪烁 + v4 首帧暖机 + v5 图库照片上行 + v6 选图预览确认 + v7 camera_facing=gallery）

**版本**: v7.1（v3 为闪烁修复定稿；v4 新增 §8 相机首帧暗帧暖机修复；v4.1/v4.2 为 §8 评审修订；v5 新增 §9 图库照片经 video 消息上行；v5.1–v5.3 为 §9 review/复核/D5 拍板；v5.4 为 §9 实施；v6.0 新增 §10 选图预览/缩放/发送确认设计；v6.1 为 §10 review 定稿：B 方案 + 左返回/右取消；v6.2 系统返回=左返回→gallery；v6.3 为 §10 实施；v6.4 picker 取消回主屏 + 发送按钮灰底白字；v7.0 新增 §11 `camera_facing="gallery"` 协议扩展设计；v7.1 为 §11 实施）
**日期**: 2026-09-08（v3）；2026-10-04（v4 新增章节；v4.1 评审修订；v4.2 复核修订）；2026-10-17（v5 新增章节；v5.1 review 定稿；v5.2 复核修正；v5.3 D5 二次拍板；v5.4 实施；v6.0 预览确认设计；v6.1 §10 定稿；v6.2 系统返回语义；v6.3 §10 实施；v6.4 返回/按钮修正；v7.0 camera_facing=gallery 设计；v7.1 §11 实施）
**状态**: 闪烁修复已实施（2026-09-08，改动按 §5 落于 ConversationViewModel.kt；真机验证待执行，用例见 §7）；首帧暖机修复已实施（2026-10-04，改动按 §8.3 落于 CameraController.kt / CameraControllerTest.kt，单测通过；真机验证待执行，用例见 §8.7）；**§9 图库照片上行已实施（2026-10-17，D5=常驻）：自动化验证通过（LivaChatApp 单测 934/0 failed/4 skipped；BottomToolbarTest 21/21 真机通过）；真机 chat 主链路 E2E 通过（图库选图 → AI 立刻描述并回图，§9.11）；边界 E2E（大图/EXIF、模式矩阵、录制、取消/连续、RTL）通过（§11.10）**；**§10 选图预览/缩放/发送确认已实施（2026-10-17，B 方案；v6.4：picker 取消/系统返回回主屏、发送按钮灰底白字）：预览 Compose 5/5（含 pinch 渲染差异）、全量单测 934/0/4、真机 E2E 与边界 E2E 通过（§10.11/§11.10）**；**§11 `camera_facing="gallery"` 已实施（v7.1）：schema/生成物/SDK/App 完成，自动化门禁通过；服务端已部署（watchfiles 于 17:35 加载新枚举）；真机 gallery E2E 通过（1440×1920 → AI 描述+回图，回显落库 `imageCameraFacing=gallery`，§11.10）**
**适用范围**: `apps/android/LivaChatApp` 会话页摄像头开关（手动 toggle / autoCameraOnJoin / 返回重绑 / 前后摄切换）；§8 追加覆盖 CameraController 视频帧上行（首帧暖机）；§9 追加覆盖底部工具栏「+」→ 系统图库选图 → `video` 消息上行；§10 追加覆盖「选图 → 全屏预览/缩放 → 发送确认 → 处理上行 → 回主屏幕」；§11 追加覆盖 `camera_facing="gallery"` 协议扩展（v7.1 已实施）

> 本文档正文以**函数/符号锚点**引用代码，行号为对应快照：§1–§7 见附录 A（2026-09-08），§8 见附录 B（2026-10-04），§9 见附录 C（2026-10-17），§10 见附录 D（2026-10-17 实施后快照），§11 见附录 E（2026-10-17 设计快照），避免行号随改动漂移。

---

## 1. 文档沿革与结论变更

| 版本 | 根因结论 | 方案取舍 |
|---|---|---|
| v1 | bind() 内 unbind() 的 false 发射跨帧覆盖乐观值；bind 风暴放大闪烁 | B+C 先做，A 备选 |
| v2 | **双写竞争**：uiState.isCameraOn 被 combine 反推写入者覆盖 | **A 根治（采纳）**，B 可选清理，C 正交 |
| v3 | 同 v2，修正"确定性"表述、补边界与用例 | **采纳 A**，实施细节补全 |
| v4（2026-10-04） | §8 新增：每轮流首批推送必然落在开流 ~0–0.9s（lastFrameTimeMs 初值 0 / 从不重置），AE 未收敛帧被上行 | §8 采纳纯时间窗（WARMUP_MS=1000ms）；亮度门 / Camera2 AE 状态 / 趋势判读 不采纳（§8.2） |
| v4.1（2026-10-04） | 同 v4 | review 修订：§8.2 方案 C 改判「CameraX 公共 API 不支持，升级依赖不解锁」（原「≥1.4 提供 addCaptureCallback」经 1.4.0 官方 aar javap 证伪）；§8.3 暖机哨兵改 lambda 局部态，取代共享 @Volatile 字段 + unbind 复位（消除在飞帧跨流竞态）；§8.1 时延表述精确化；§8.2 补 E 备选；§8.7 补常态节流回归用例 |
| v4.2（2026-10-04） | §8.2-C 证据复核：stable 线（1.3.4 / 1.4.0 / 1.6.2 官方 aar javap 实证）确无 capture-result 回调，但 1.7.0-alpha03 已引入公开 interop 接口 setRepeatingCaptureCallback（androidx-main 源码同）——v4.1 的「升级依赖无法解锁」更正为「需升 1.7 alpha 渠道，不划算」 | §8.2-C、§8.6 相应修订；结论（不采纳 C）不变 |
| v5（2026-10-17） | §9 新增：图库选图经 `video` 消息上行。现状链路取证 + UI/图片处理设计 + 触发语义选型 | **待 review，未实施**。推荐 v1：App-only（Photo Picker → 1920/q70 → 复用 v3 文本回合，`text` 触发）；备选 turn_id / 服务端 gallery 源 / 纯缓冲 |
| v5.1（2026-10-17） | §9 review 定稿 | 新增 D9：**摄像头开启时「+」隐藏且不可点击**；确认 D2=T1（选图即让 AI 立刻描述）；D1=A（仅摄像头关闭时整行可点）；其余按推荐（F2 / 复用失败文案 / 常驻 / q70 / 抽公共 EXIF / 允许连选） |
| v5.2（2026-10-17） | §9 复核修正（对抗式自审） | **D5 由「常驻」改为「仅 chat 模式显示」**：T1 在 omni 只作视觉上下文（`semantic_vad` 不支持免开口起轮，TODO-1 未实施）、translate/lecture/transcribe 被服务端门控；原常驻会产生可点但无反应的死控件。T1 限定为 chat；新增 D10（omni 立即描述 = 后续 TODO-1，不在本期）；新增 §9.10 复核记录 |
| v5.3（2026-10-17） | §9 D5 二次拍板 | **D5 恢复为常驻：所有模式都显示「+」**（仅摄像头开启时按 D9 隐藏）。接受非 chat 模式选图不产生回合/回复（死控件行为已知）；omni 选图仅作视觉上下文；**screen recording（chat）场景照常工作**。撤销 `canUseGallery` / `showGalleryPicker` chat-only 门控；恢复 omni 下 `text=null` 分支 |
| v5.4（2026-10-17） | §9 实施 | 已落地：BottomToolbar「+」、单图 Photo Picker、`GalleryPhotoSender`（采样/长边 1920/q70/EXIF/三态/相机守卫）、`ImageExif` 抽取、VM 事件接线。验证：LivaChatApp 单测 **929 passed / 0 failed / 4 skipped**；BottomToolbarTest **21/21**（真机 LIO-AN00）；assembleDebug 与 androidTest 编译通过；**真机 chat 主链路 E2E 通过**（选图 → AI 立刻描述并回图，§9.11）。大图/EXIF、omni/translate、录屏与边界待验证 |
| v6.0（2026-10-17） | §10 新增：选图预览 + 缩放 + 发送确认 | **设计待 review，代码未改动**。picker → 独立全屏预览（Coil + ImageViewerMath 1x–5x + 拖动）→ 底部「发送」才触发 1920/q70 处理与上行，随后回主屏幕；取消/返回不处理不上行。推荐在 ConversationScreen 内以 early-return 覆盖层实现（对齐 AvatarCropScreen），新增 `galleryPreviewUri` 状态与 `OpenGalleryPreview/DismissGalleryPreview` 事件；新增预览相关文案（15 语言） |
| v6.1（2026-10-17） | §10 review 定稿 | **UI=B（黑底 + 底部悬浮发送）**；标题栏左「返回」→ 重新打开 gallery 继续选择，右「取消」→ 直接回主屏幕；**不显示中间提示文字**；其余按推荐（覆盖层、Coil、加载成功后启用发送、错误态、无双击、新语义文案、无单击退出、发送后立即回主屏、detectTransformGestures）。代码未改动，待实施 |
| v6.2（2026-10-17） | §10 系统返回语义定稿 | **系统返回键/手势 = 左侧「返回」**：重新打开 gallery 继续选择（picker 取消则保持当前预览）；右侧「取消」仍直接回主屏幕。更新 §10.3/§10.4/§10.5/§10.6/§10.7/§10.9 与 mock |
| v6.3（2026-10-17） | §10 实施 | 已落地：`GalleryPhotoPreviewScreen`（B：黑底、左返回/右取消、无中间提示、悬浮「发送」、Coil + `ImageViewerMath` 1x–5x）、`galleryPreviewUri`/`OpenGalleryPreview`/`DismissGalleryPreview`、VM 发送即清预览、15 语言文案。验证：LivaChatApp 单测 **932/0 failed/4 skipped**；`GalleryPhotoPreviewScreenTest` **4/4**；BottomToolbarTest **21/21**；真机 E2E（左返回进 gallery、右取消回主屏、发送后 AI 描述并回图）。versionCode 未动 |
| v6.4（2026-10-17） | §10 修正 | **picker 内系统返回/取消 = 关闭预览回主屏幕**（不再回到旧预览）；预览底部「发送」改 **灰底白字**（`SurfaceVariant` + `TextPrimary`，禁用态灰暗）。同步 §10.1/§10.3/§10.4/§10.5/§10.6/§10.7/§10.9/§10.11 与 mock |
| v7.0（2026-10-17） | §11 新增：`camera_facing="gallery"` | **设计待 review，代码/协议未改动**。`CameraFacing` 枚举 front/back/screen → +gallery；schema 重生成 Python/Kotlin；SDK `mapCameraFacing` + `GalleryPhotoSender` 改传 gallery；服务端仅透传/校验，无行为分支；**服务端先行部署**。当前 v6.4 代码仍为 screen |
| v7.1（2026-10-17） | §11 实施 | 已落地：`CameraFacing` 四值（Python/Kotlin 重生成）、SDK `mapCameraFacing` 加 gallery、`GalleryPhotoSender` 改 `camera_facing="gallery"`、服务端 gallery 接受/回显测试、协议文档同步。验证：协议 6/6；services 全量 **1623 passed / 1 skipped / 97 deselected**；SDK **186/0**；App **934/0/4**；`assembleDebug` 通过；Python/Kotlin `--check` up to date。**服务端已部署；真机 gallery E2E 通过（2026-10-17，`imageCameraFacing=gallery`，AI 描述并回图，无 1006/4001）**；versionCode 未动 |
| v7.2（2026-10-27，已实施） | §9 图库指令本地化 | `GALLERY_ACTION_TEXT` 与屏幕抓取 `TAP_CAPTURE_TEXT` 合并为 15 locale 资源 `conv_image_action_text`（随 App 语言、英文兜底；omni 仍 null）；随后扩展至 17 locale（th/vi）。详见 `docs/android_multilingual.md` §2.3.9/§21。**已实施；单测/构建通过** |

> 2026-10-27 已实施：Android UI locale 15 → 17（新增 `th`/`vi`，泰语使用 Gregorian）。本文 v7.2 及以下的“15 locale/15 目录”为实施时快照；最新状态见 `docs/android_multilingual.md` §21。

v1 两处结论性错误，此处明示以防误导：
1. v1 §3.1 归因于 bind() 内 unbind() 的 false 帧——**不成立**：打开场景 `_isCameraOn` 本为 false，unbind() 的 false 写被 StateFlow 去重，不发射（CameraController.unbind()）。
2. v1 §3.3 归因于 AndroidView 每次重组重建 PreviewView——**不成立**：AndroidView 跨重组保留实例；实测 bind 风暴是哨兵放大回路（下 §4.2）驱动，因果与 v1 相反：**闪烁（状态覆盖）驱动重绑风暴，而非反向**。

## 2. 现象

- 点击「摄像头」打开相机时，底部工具栏图标出现 **ON→OFF→ON** 闪烁
- 切换前/后摄像头时同样闪烁
- 会话越活跃（流式 transcription 越密）、CameraX 绑定越慢，闪烁越明显

## 3. 状态链路与两个写入者

```
BottomToolbar 相机按钮（BottomToolbar.cameraButton）
  └─ uiState.isCameraOn
        ├─ 写入者① 乐观直写：toggleCamera() / OnScreenStart autoCameraOnJoin
        └─ 写入者② combine 反推：ConversationViewModel.init 的 combine 变换
             （isCameraOn = camOn，camOn 来自 cameraController.isCameraOn）
```

写入者②的关键问题：其 combine 上游不只是相机——connectionState / sessionState（LISTENING↔SPEAKING 频繁翻转）/ 静音×2 / `cameraController.isFrontCamera` / `listenerAdapter.transcription`（流式 ASR 部分结果）/ isTranscriptionFinal / isRecording。**任一上游发射都会重跑变换，并无条件用 controller 当前值重写 isCameraOn**，覆盖写入者①的乐观值。

## 4. 根因

### 4.1 打开相机：乐观写被 combine 覆盖（主因）

| 时刻 | 事件 | 图标 |
|---|---|---|
| t0 | 点击 → toggleCamera() 乐观写 isCameraOn=true（+ videoVisibility=VISIBLE） | 亮 |
| t1 | UI 进入 VISIBLE 分支 → PreviewView 创建 → LaunchedEffect(cameraPreviewView) → bindCamera() → CameraController.bind() 同步段。`_isFrontCamera.value = frontCamera`（默认后置设置下 true→false 必翻转）**触发一次 combine 重算**；此后异步窗口内 transcription/sessionState 等任一上游发射同样触发重算 → 用 controller 旧值（false）覆盖乐观值 | 灭 |
| t2 | 数十~数百 ms 后 CameraX 异步绑定完成 → `_isCameraOn = true`（bind() 异步 listener）→ combine → 恢复 | 亮 |

- 窗口长度 = CameraX 异步绑定延迟（冷启动 provider 初始化慢时更长）。
- `_isFrontCamera` 翻转只在「默认后置」设置下发生（controller 初值 true vs `defaultFrontCameraSetting` 默认 false）；用户设「默认前置」时该触发消失，仅剩上游随机发射。**OFF 帧是否可见取决于渲染帧边界与窗口内是否有发射落在独立帧**——文档不再称"确定性"，由实施后日志验证（§7 用例 10）。
- bind() 同步段的 false→true 对（unbind()→恢复 wasOn）在同一调用栈内，单帧合并，不直接贡献闪烁。

### 4.2 切换前后摄：瞬态 + 两个放大器

切换路径：SwitchCamera 翻转 desiredFrontCamera → cameraResetCount++（Screen 上 onSwitchCamera 回调与切换按钮 key 双重驱动）→ key(cameraResetCount) 销毁重建 PreviewView → LaunchedEffect → bindCamera() → bind()。

- bind() 同步段产生一对 false→true 瞬态发射（unbind→恢复 wasOn），单帧可合并；`_isFrontCamera` 切换时必然翻转，保证重绑窗口内有 combine 重算（活跃会话时窗口内另有 transcription 等发射）。
- **放大回路（结构性）**：Screen 的 prevCameraOn 上升沿哨兵——一旦观察到 isCameraOn 翻 false 再回 true，再次 cameraResetCount++ → 再销毁重建 PreviewView → 再触发一轮完整 bind()。一次状态漏出被放大成第二轮（甚至多轮）重绑，preview 同步闪烁；每轮新 bind 又制造新窗口，形成自激，直至异步绑定成功。
- **渲染放大器**：BottomToolbar 相机按钮的 animateColorAsState spring 动画把 isActive 抖动渲染成可见颜色渐变（可感知时长被拉长，不属根因）。

### 4.3 同根因的次级竞态：绑定挂起中关闭 → 图标卡死

打开后异步绑定完成前关闭（如 <100ms 内再点）：`unbind()` 的 false 被去重不发射，`uiState.isCameraOn` 停在乐观 true、videoVisibility 已 HIDDEN → **图标亮但相机已关**；且哨兵 prev 已为 true，下次点击被判为"关闭"，需等上游偶然发射重算才恢复。方案 A 的三处显式 false 顺带根治此竞态。

## 5. 修复方案：单一写入者

**原则**：`uiState.isCameraOn` 只表达「UI 期望状态」，由事件路径显式管理；controller 不再经 combine 反推，仅通过 onBindFailed 异步上报失败（失败通道已就绪：UI 复位 + 错误横幅「相机启动失败，请检查相机权限」）。

### 改动点（4 处）

1. **combine 移除相机项**（`ConversationViewModel.init` combine，L100-147）：
   - combine1 输入去掉 `cameraController.isCameraOn`（L106）
   - arrayOf 去掉 `camOn`（L108）
   - 解构读取删 `val camOn = arr[4]`，数组收为 4 元素（L124）
   - transform 删除 `isCameraOn = camOn`（L132）
   - 消灭写入者②。

2. **三处显式补写关闭路径**（今日依赖 combine 传播 controller 的 false，删 combine 后必须自写）：
   - `toggleCamera()` 关闭分支（wasOn==true）：copy 加 `isCameraOn = false`
   - `toggleScreenRecording()` 互斥关相机：copy 加 `isCameraOn = false`
   - `bindCamera()` 的 onBindFailed：copy 加 `isCameraOn = false`

3. **不动**：
   - CameraController 的 wasOn 保存/恢复（bind()）保留——controller 内部流自洽即可，不再喂 UI
   - Screen 的 prevCameraOn / cameraResetCount 哨兵不动——isCameraOn 稳定后自然安静，放大回路随之消除
   - `uiState.isFrontCamera` 仍走 combine2（切换按钮朝向文案依赖它，语义不受影响）

修复后全部路径上 `isCameraOn` 与 `(videoVisibility == VISIBLE)` 同写恒等（开路径两处同写 true、三处关闭路径同写 false），字段一致性不变量成立。

### 修复后语义

| 路径 | 行为 |
|---|---|
| 打开（手动 / autoCameraOnJoin） | 乐观点亮，无竞争者 → 不闪 |
| 切换前后摄 | 重绑窗口无人改写 isCameraOn → 不闪 |
| 关闭 / 录制互斥关相机 | 显式 false，立即熄灭 |
| 绑定失败（权限缺失/撤销、无匹配镜头） | onBindFailed → isCameraOn=false + HIDDEN + 错误横幅 |
| 绑定挂起中关闭（<100ms 竞态） | 显式 false，图标熄灭，可立即重开 |

语义变化：图标表达「用户意图」而非「硬件在流」。失败路径已有兜底，可接受。

### 不采纳的替代方案

combine 加 `pendingCameraOp` 标志做 `isCameraOn = camOn || pendingCameraOp`：能止血，但保留双写语义，状态含义仍然混乱，后续维护还会踩。

### 已知边界（非本次范围）

CameraX 活跃期异常——他应用抢占相机、UseCase 运行时 onError——当前无监听（bindUseCases 未挂 error listener），修复后图标会保持意图态。属既有缺口，建议后续独立处理（controller 增活跃期失败通道）。

## 6. 边界覆盖检查

- **返回页面重绑**（videoVisibility 保留 VISIBLE）：uiState 已为 true；bind 成功无事件；失败走 onBindFailed → false ✓
- **autoCameraOnJoin + 权限缺失**：乐观 true → bind() 前置权限检查失败 → onBindFailed → false + 横幅 ✓
- **录制互斥自动关相机**：显式 false ✓
- **快速连续 toggle/切换**：无写入竞争，状态单调；bindGeneration 守卫丢弃过期异步回调 ✓
- **离开页面 / VM 销毁**：DisposableEffect 仅 unbind 相机硬件；导航前进时 VM 与 uiState 保留（返回后按 `videoVisibility=VISIBLE` 重绑），VM 真正销毁时状态随作用域消失 ✓

## 7. 验证计划

1. 会话中打开相机：图标一次点亮不闪；logcat `CameraController` 的 `bind:` 只出现一次（当前因哨兵放大可出现多次）
2. 连续快速切换前后摄多次：图标常亮不闪（preview 黑闪属 CameraX 重建的正常表现，不在本次范围）
3. 关闭相机：图标立即熄灭
4. **打开后 <100ms 立即关闭再重开**：图标熄灭、可正常重开（验证 §4.3 竞态消除）
5. 开录制互斥关相机：图标熄灭 + 录制正常启动
6. 权限拒绝两条路径（手动 toggle / autoCameraOnJoin）：错误横幅 + 图标熄灭
7. 离开再返回会话页：重绑后图标保持点亮
8. **「默认前置摄像头=开」设置变体**下打开相机：不闪（覆盖 §4.1 无 front 翻转触发的场景）
9. **安静会话**（无 transcription）下开关/切换：不闪（排除活跃会话混淆变量）
10. 根因确认日志（实施首轮）：打印 `uiState.isCameraOn` 与 controller `_isCameraOn` 每次变化 + 时间戳，核对 4.1 覆盖源（预期：t1 窗口内的 front 翻转或上游发射先于 t2 成功写入落地）

## 附录 A：代码锚点与行号核对表（2026-09-08 快照）

| 符号 | 文件 | 行号 |
|---|---|---|
| toggleCamera()（乐观 true L431-434；关闭分支 L424-428） | ConversationViewModel.kt | 417 |
| switchCamera()（仅翻转 desiredFrontCamera） | ConversationViewModel.kt | 438 |
| bindCamera() + onBindFailed lambda | ConversationViewModel.kt | 370 |
| OnScreenStart autoCameraOnJoin（乐观 true） | ConversationViewModel.kt | 216 |
| toggleScreenRecording() 互斥段 | ConversationViewModel.kt | 392 |
| combine（含 cameraController.isCameraOn L106 / isCameraOn = camOn L132） | ConversationViewModel.kt | 99 |
| defaultFrontCameraSetting 默认 false | ConversationViewModel.kt | 61 |
| bind()（wasOn L73 / unbind L74 / 权限检查 L80-86 / 恢复 wasOn L88 / front 翻转 L91 / 异步成功 true L99） | CameraController.kt | 67 |
| unbind()（_isCameraOn=false L190） | CameraController.kt | 186 |
| _isFrontCamera 初值 true | CameraController.kt | 41 |
| prevCameraOn 哨兵 / cameraResetCount++ | ConversationScreen.kt | 226 |
| key(cameraResetCount)（VISIBLE 分支） | ConversationScreen.kt | 350 |
| LaunchedEffect(cameraPreviewView) → bindCamera() | ConversationScreen.kt | 253 |
| BottomToolbar 相机按钮 | BottomToolbar.kt | 76 |
| ToolbarButton animateColorAsState | BottomToolbar.kt | 98 |
| VideoPreview AndroidView(factory) | VideoPreview.kt | 54 |
| ConversationUiState.isCameraOn | ConversationUiState.kt | 19 |

---

## 8. 相机首帧暗帧暖机修复（v4 新增 2026-10-04；v4.1/v4.2 评审修订；已实施）

> 本节方案编号（A–E）为**本节独立编号**，与 §1–§7 的闪烁修复编号无关。符号锚点见附录 B（2026-10-04 实施后快照）。

### 8.1 现象与根因

- **现象**：刚打开摄像头（含前后摄切换、返回页面重绑）后，上行服务端的第一帧必偏暗，随后画面明亮起来（AE 自动曝光收敛所致）。
- **根因（代码证据，确定性而非偶发）**：
  1. `CameraController.lastFrameTimeMs` 初值为 0 且从不在 bind/unbind 重置，analyzer 节流判据为 `now - lastFrameTimeMs >= 900`。因此**每轮流（bind）后，首次推送必然落在 analyzer 收到本轮首帧后的 ~0–0.9s 内**（analyzer 首帧回调本身落后 `bind()` 一个管线建立时延，典型 200–600ms）：进程内首次打开时 `now - 0 ≥ 900` 恒真 → 新流第一帧立推；重绑场景（前后摄切换 / 返回重绑）距上次推送 >900ms 时同样立推，否则残余节流窗（<900ms）到期即推。而此刻 CameraX 刚开流，AE 尚在从默认值爬升（典型数百 ms ~1s，暗场景更慢），故推上去的必然是暖机暗帧。
  2. 部分设备 HAL 在开流前吐 1~2 帧全黑，同样被立推。
- **影响面**：暗帧在服务端 `latest_video_frames`（K=1）约 0.9s 后被顶替，单帧存续有限；但「开相机后立即说话」的**首个 LLM 轮次（video_text）**会携带暗帧（可能产生「画面很暗」类错误描述），avdetector 的 video check（10s 节拍）亦可能恰好采到。

### 8.2 判读信号选型（结论：纯时间窗）

| 方案 | 判读信号 | 评价 | 结论 |
|---|---|---|---|
| **A. 纯时间窗** | 距流开始 < `WARMUP_MS` 的帧一律丢弃 | 零成本、确定性、无新参数面；不含亮度语义——暗场景 1s 后仍可能偏暗（§8.6 已知局限） | **采纳，WARMUP_MS = 1000ms** |
| B. 亮度门 | YUV Y 平面（Y 即亮度）采样均值 < 阈值判「未就绪」，配 latch + 硬上限 | 直接测暗；但引入阈值参数、采样实现与误判面（真暗场景靠硬上限兜底），复杂度超出本修复所需 | 不采纳（真机日志若出现 >1s 未收敛证据，可作后续叠加） |
| C. AE 收敛状态 | Camera2 `CONTROL_AE_STATE == CONVERGED` | 语义最准；**可用性（复核修订）**：stable 线（1.3.4 / 1.4.0 / 1.6.2 官方 aar javap 实证）interop 只能下发 capture request options、无 capture-result 回调（内部钩子 `Camera2CameraControlImpl.addCaptureResultListener` 为 package-private）；capture-result 回调于 **1.7.0-alpha03 才出现**（alpha01/02 尚未含）——新 interop 公开接口 `CameraCaptureSessionInterop.setRepeatingCaptureCallback(Executor, CameraCaptureSession.CaptureCallback)`，接入面 `SessionConfigCamera2Interop` / `CameraControlCamera2Interop`，androidx-main 源码含。采用需引入 1.7 **alpha** 依赖 | 不采纳（stable 无公共 API；为 1s 暖机升 alpha 不划算，待 1.7 stable 后再评估） |
| D. 亮升趋势 | 连续多帧 luma 递增判「爬升中」、平顶判「收敛」 | 多帧状态机复杂度，实际收益与 A 的固定窗口基本重叠 | 不采纳 |
| E. 帧数窗 | 每轮开流丢前 N 帧 | 最朴素；但 N 帧时长随 fps 浮动（30fps 下 N=30 才 1s），与 AE 爬升的时间语义不对应，窗口标定反而更难 | 不采纳 |

（「不做修复」备选：暗帧仅存续 ~0.9s、影响有限——但首个 LLM 轮次的可见影响使修复必要，不取。）

### 8.3 方案 A 设计

**语义**：每轮流（每次 bind）开流后前 `WARMUP_MS = 1000ms` 内，analyzer 丢弃全部帧（close + 抽样日志）；跨过窗口后恢复现有 ~900ms 节流推送。首个上行帧从 t≈0（必暗）变为 t≈1.0s（暖机后首帧）。

**时序**：

| 时刻 | 事件 | 行为 |
|---|---|---|
| t≈0 | 本轮首个帧回调 | 置 `streamStartMs = now`、`lastFrameTimeMs = 0`；日志 `warmup: start` |
| 0 < t < 1000ms | 后续帧（~30fps） | warm-up 丢弃；仅首条记 `warmup: skipped (elapsedMs=…)`，避免日志风暴 |
| t≈1000ms | 首个跨窗帧 | 节流通过（`lastFrameTimeMs=0`）→ 推送首帧；日志 `warmup: done (elapsedMs=…)` |
| t>1000ms | 常态 | 现有节流逻辑（900ms）完全不变 |

**改动点（全部在 CameraController.kt）**：

1. companion 新增 `WARMUP_MS = 1000L`，并把现有字面量 900 提为 `THROTTLE_MS = 900L`（建议，两常量同区命名）。
2. 暖机哨兵做成 **analyzer lambda 捕获的局部变量**（v4.1 修订，取代初稿的共享 `@Volatile` 字段 + `unbind()` 复位）：`bindUseCases()` 每轮创建新的 ImageAnalysis 实例与新的 analyzer lambda，`var streamStartMs = 0L` 声明于 lambda 外、`bindUseCases()` 内——随 lambda 按流重生（`bind()` 内嵌 `unbind()` + 新 lambda，天然覆盖首开/切换/重绑三条路径）；仅在 analysisExecutor 单线程上读写，无需 `@Volatile`、无需 `unbind()` 复位，并在构造上消除「unbind 前已越过守卫的在飞帧重新武装共享哨兵、缩短/跳过新流暖机窗」的跨流竞态。`bindGeneration` 新增 analyzer 线程读取 → 标注 `@Volatile`（写仅在主线程，`++` 单写者安全）。
3. analyzer 流程（示意，日志沿用既有 `sdk.logDebug("CameraController", …)` 通道）：

```kotlin
.also { analysis ->
    var streamStartMs = 0L        // 本轮流暖机哨兵：随 lambda 按流重生，仅 analyzer 线程访问
    var warmupSkipLogged = false  // 「warmup: skipped」每轮只记首条，防日志风暴
    analysis.setAnalyzer(analysisExecutor) { imageProxy ->
        // generation 在 bindUseCases 内捕获；回调首行丢弃滞后旧流残帧（消解 §8.4 滞后回调边界）
        if (generation != bindGeneration) { imageProxy.close(); return@setAnalyzer }
        val now = SystemClock.elapsedRealtime()
        if (streamStartMs == 0L) { streamStartMs = now; lastFrameTimeMs = 0 }  // 本轮首个回调
        if (shouldSkipWarmup(now - streamStartMs)) {   // 纯函数，默认 WARMUP_MS
            if (!warmupSkipLogged) { warmupSkipLogged = true; /* log warmup: skipped */ }
            imageProxy.close(); return@setAnalyzer
        }
        // 以下沿用现有节流与 processFrame 逻辑
    }
}
```

4. 纯函数接缝（可直测，与 `scaledMaxLongSide` 同风格）：

```kotlin
internal fun shouldSkipWarmup(elapsedMs: Long, warmupMs: Long = WARMUP_MS): Boolean =
    elapsedMs < warmupMs
```

5. 时间源：统一使用 `SystemClock.elapsedRealtime()`（建议）——暖机与既有节流一并切换（同区改动；单调钟，行为差异仅在墙钟跳变场景；`lastFrameTimeMs=0` 哨兵语义不变）。
6. warm-up 丢弃**不**计入 `throttledDropCount`（判定在节流判据之前），节流日志语义保持纯净。
7. 不改动：Preview 显示路径（用户预览不受影响）、`processFrame`/`imageProxyToJpeg`、`ScreenCaptureController` 屏幕抓取路线（本次只改 camera 路线）、SDK 与服务端。

### 8.4 边界覆盖检查

- **打开后 <1s 立即关闭**：0 帧推送（旧行为：立推 1 暗帧）——更优，无副作用。
- **前后摄切换 / 返回页面重绑**：每轮重新暖机 1s；旧行为首个重绑后推送落在 0~0.9s（取决于残余节流窗，越靠前越暗），新行为统一推到 ~1.0s——上行首帧最多晚 ≤1s，换取暗帧不再上行（预览不受影响）。
- **快速连开关**：generation 守卫不变；暖机哨兵随新 lambda 重生、无共享态，无新竞态。
- **unbind 与重绑间隙的滞后回调**（analyzer 队列中的旧流残帧）：无守卫时旧帧按旧闭包状态（暖机已完成）直接进入节流判定，通过即上送旧流画面（facing 按当前值误标）——analyzer 首行加 `generation != bindGeneration` 守卫直接丢弃（§8.3-3）。暖机哨兵为 lambda 局部态，旧流残帧无从触碰新流哨兵（初稿共享字段设计的「在飞帧跨流重新武装哨兵」竞态随之在构造上消除）。
- **暗环境（夜晚 / 镜头遮挡）**：1s 后照推、不卡死（时间窗不含亮度语义——选型决定，见 §8.6）。
- **autoCameraOnJoin**：同「打开」路径，无特殊处理。
- **开相机后 <1s 立即说话**：该轮次由「带暗帧」变为「无帧」——更贴近事实（尚无可用画面），见 §8.5。
- **与录屏互斥（D9）**：不变。

### 8.5 服务端影响面

- 服务端与协议**零改动**；首帧到达由 t≈0 推迟至 t≈1.0s。
- 时序量级可忽略：帧间距 900ms、`latest_video_frames` K=1、avdetector video check 10s 独立节拍。
- 收益：首个 video_text 轮次不再携带 AE ramp 暗帧；<1s 内的极早期轮次不再把「刚开流、尚不可用」的画面上送（该轮无帧）。

### 8.6 已知局限（A 的确定取舍）

- AE 若在 1s 内未收敛（很暗房间 / 慢设备），首个推送帧仍可能偏暗——纯时间窗不含亮度判读。真机日志（`warmup: done` 后上行帧抽检）若显示仍明显偏暗，可后续叠加 B（亮度门）；C 暂不划算（stable 线无公共 capture-result 回调；1.7.0-alpha03 起的新 interop 接口需引入 alpha 依赖，待 1.7 stable 后再评估，见 §8.2）。
- HAL 在 >1s 后仍吐黑帧的罕见设备，A 不覆盖。

### 8.7 验证计划

- 单测（CameraControllerTest 追加，沿用既有纯函数风格）：
  - `shouldSkipWarmup` 边界：0 / 999 → true；1000 / 1001 → false；默认参数即 1000。
- 真机（logcat 断言 + 服务端抽帧目视）：
  1. 亮环境打开相机：`warmup: start` → 首个 `capture:` 间隔 ≥1000ms；服务端拉取首帧不暗。
  2. 前后摄切换：每轮 bind 成对出现 `warmup: start` / `warmup: done`。
  3. 打开后 <1s 立即关闭：无 `capture:` 推送日志（0 帧）。
  4. 遮镜头打开：1s 后照推，帧流不卡死。
  5. 返回页面重绑：首帧推送距重绑 ≥1000ms。
  6. 开相机后立即说话：首个 video turn 携带帧非暗（<1s 时无帧）。
  7. 常态节流回归：暖机结束后连续 `capture:` 间隔 ≥900ms（时间源切换 elapsedRealtime 后节流节奏不变）。

---

## 附录 B：§8 代码锚点（2026-10-04 实施后快照）

| 符号 | 文件 | 行号 |
|---|---|---|
| `@Volatile bindGeneration`（analyzer 代次守卫读取） | CameraController.kt | 58-59 |
| `lastFrameTimeMs`（初值 0） | CameraController.kt | 61 |
| `bind()`（内部先 unbind） | CameraController.kt | 73 |
| generation 捕获（bindUseCases 内，analyzer 守卫用） | CameraController.kt | 121 |
| analyzer 暖机判据 `shouldSkipWarmup(now - streamStartMs)` | CameraController.kt | 164 |
| analyzer 节流判据 `now - lastFrameTimeMs >= THROTTLE_MS` | CameraController.kt | 176 |
| `unbind()` | CameraController.kt | 258 |
| companion：`VIDEO_MAX_LONG_SIDE` 282 / `WARMUP_MS` 285 / `THROTTLE_MS` 287 | CameraController.kt | 282-287 |
| 纯函数 `shouldSkipWarmup` | CameraController.kt | 290 |
| 单测落点（scaledMaxLongSide + warmup 边界四例） | CameraControllerTest.kt | 14-73 |
| CameraX 依赖版本 1.3.4（interop 调研） | apps/android/LivaChatApp/build.gradle.kts | 101-104 |
| `pushVideoJpeg`（上行出口） | LivaChatSDK.kt | 273 |
| `latest_video_frames`（K=1） | services/session/context.py | 31-34 |
| `_video_interval_s`（avdetector 视频节拍，默认 10s） | services/session/av_detector.py | 133 |
| `feed_frame` | services/session/av_detector.py | 220 |

---

## 9. 图库照片经 video 消息上行（v5 新增 2026-10-17；v5.1–v5.3 review/复核；v5.4 已实施，chat 主链路真机 E2E 通过）

> 本节方案编号（A–F）为**本节独立编号**，与 §1–§8 无关。符号锚点见附录 C（2026-10-17 快照）。
> **状态：已实施（2026-10-17）；chat 主链路真机 E2E 通过，其余模式/边界待验证**。组合：D1=A（摄像头关闭时整行可点）、D2=T1（选图即描述，**仅 chat 立即生效**）、**D3=F1（v7.1 已实施：新增 `"gallery"` 并用于图库照片；服务端已部署，真机 E2E 通过）**、D4=复用现有文案、**D5=常驻（所有模式；仅摄像头开启时隐藏，D9）**、D6=q70、D7=抽公共 EXIF、D8=允许连选、D9=摄像头开启时「+」隐藏且不可点、D10=omni 立即描述不在本期（omni 仅视觉上下文）。非 chat 无回合为已知且接受的行为。实施记录见 §9.11，复核发现见 §9.10，决策全表见 §9.9；协议扩展见 §11。

### 9.1 需求与范围

原文需求：

1. 在会话页底部工具栏「摄像头」文字前加入「+」；点击即打开手机图库，选取一张图片。
2. 只能选一张；图片过大时**长边 ≤ 1920**，等比缩放。
3. 以当前 codebase 为基础，不猜测；UI/UX 风格跟随现有 App。
4. 分析与设计落入本文档，等待 review（2026-10-17 已完成，见 §9.9）。

**本次范围内**：App UI（工具栏、选择器接线）、图片解码/EXIF 旋正/缩放/JPEG 编码、经现有 `video` 消息上行；App 侧自动化测试与真机验证计划。

**本次范围外**：多选/多图、裁剪/预览/编辑、分享、离线排队与重试 UI、相机/录屏链路改动；服务端行为改动（定稿方案 T1 下为零改动）。

**产品语义（review 已确认）**：选择图片 = 让 AI 立刻“看”这张图并描述/回应（§9.9 D2=T1），与开相机首帧直起轮一致。**该语义在 chat 模式完整成立**；omni 模式下照片只能作实时模型的视觉上下文（现有 `semantic_vad` 不支持免开口起轮，见 §9.10 F1），translate/lecture/transcribe 被服务端文本回合门控排除、照片不产生回合——**这些模式的“无回合”行为已由 review 接受**（D5=常驻）。「+」在**所有模式常驻**，仅摄像头开启时隐藏（D9）；screen recording（chat）场景照常工作。不采用“先把图挂上、随后用语音提问”的延迟挂图语义——当前服务端没有该机制（帧缓冲 5s 空闲即清，见 §9.2），需要另做服务端改动，不在本设计内。

### 9.2 现状取证（代码事实）

> 行号为 2026-10-17 当前工作树快照，符号锚点见附录 C。

#### 9.2.1 客户端现状

| 事实 | 证据 |
|---|---|
| 底部工具栏固定三列（麦克风 / 扬声器 / 摄像头），用 `SpaceEvenly` 排布；caption 只渲染首词：`caption.split(" ").first()` | `BottomToolbar.kt` 78-137、212-215 |
| 工具栏已持有 `isCameraOn`（摄像头列激活态），可直接用于「+」条件渲染与回调禁用（D9） | `BottomToolbar.kt` 61、130-135 |
| 既有「文字前挂图标 + 整行点击」范式：`captionLeading` + `onCaptionClick`（麦克风 `^` / 扬声器 `^` 设备选择器） | `BottomToolbar.kt` 101-111、118-128、143-147、200-221 |
| caption 行带 `Modifier.clickable`，且 clickable 位于 padding 外层（触区含行底 8dp，约 24dp 高）；前置图标 16dp、文字后 18dp 对称占位保证文字中心与图标轴对齐 | `BottomToolbar.kt` 188-221；androidTest `BottomToolbarTest` 337-362、305-334 |
| 图库选择已有先例：头像「从相册选择」使用 `ActivityResultContracts.PickVisualMedia()` + `PickVisualMediaRequest(ImageOnly)`，**单图、无存储权限** | `ConversationScreen.kt` 126-133、696-700 |
| 图片解码已有先例：先 `inJustDecodeBounds`、再按 `maxDim/2048` 设 `inSampleSize`、解码后 `applyExifOrientation`；EXIF 八种朝向齐全 | `AvatarCropScreen.kt` 72-99、275-309；`androidx.exifinterface:1.3.7`（`build.gradle.kts` 113-114） |
| 相机帧上行：`CameraController` analyzer → `processFrame` → `imageProxyToJpeg`（长边 1280、按 `rotationDegrees` 旋正、JPEG q70）→ `sdk.pushVideoJpeg(..., PORTRAIT_0, facing, turnCounter.current)` | `CameraController.kt` 205-260、292-301、318-330 |
| 屏幕抓取上行：长边 1920、JPEG q70、带 `text = conv_image_action_text`（v7.2 已实施）、`camera_facing="screen"`、`turnId=turnCounter.current` | `ScreenCaptureController.kt` 639-644、910-911、939、985-996 |
| SDK `pushVideoJpeg` 是**预编码直通**：不缩放、不重编码，调用方负责尺寸；`frame_id` 由 SDK 原子自增；`turnId` 默认 0（未启用）、`text` 默认 null | `LivaChatSDK.kt` 298-317；`VideoSendPipeline.kt` `pushJpeg` |
| `camera_facing` 映射：`"back"→BACK`、`"screen"→SCREEN`、`"gallery"→GALLERY`、其他→FRONT（v7.1） | `LivaChatSDK.kt` 367-372；§11 |
| 采集轮计数：`CaptureTurnCounter` 进程级单例，`current` 初值 0，`next()` 从 1 起；App 层维护、SDK 直通 | `CaptureTurnCounter.kt` 14-27 |
| 会话未连接时 SDK 静默丢帧（仅首条 warn 日志） | `ConnectionManager.kt` 101-105；`SessionManager.kt` 237-243（video 走闸口外宽容路径） |
| 字符串 15 目录 key 集合强制齐平（en/Hans/Hant/ja/ko/ru/de/fr/it/es/pt/tr/hi/id/ar）；现有 `conv_avatar_from_gallery`（"Choose from gallery"/"从相册选择"）已全量存在 | `I18nResourceFilesTest.kt` 61-84；`values*/strings.xml` |

#### 9.2.2 服务端现状（`services/session/orchestrator.py::_handle_video`）

| 上行条件 | 服务端行为 | user 行 content | avdetector |
|---|---|---|---|
| `data` 非空的帧 | 解码（`validate=True`）后进 `latest_video_frames`（K=1，逐出最旧）；空 data 只回 `processed=True`、不入 buffer | — | 无 text 帧才 `feed_frame` |
| `payload.text` 非空（strip 后 ≥ `min_meaningful_chars`）且模式为 chat 系 | **立即**快照（取走并清空 buffer）→ 三件一体落盘 → 条件中断 → `_start_thinking(text, image_frames)` | = `text`（v7.2 已实施为本地化发图指令） | **否**（`not text` 门控） |
| `payload.turn_id` 非空且非 0、与上次不同、无 text、模式为 chat 系 | 同上立即起轮，但走相机语义 | 固定 `"[相机画面]"` | **是**（无 text） |
| 上述都不满足（无 text、无 turn 变化/模式门控） | 仅进 K=1 buffer；5s 无新帧自动清空 | — | 是 |
| omni 模式 | 不直起轮；帧 `feed_image` 给 omni 引擎作视觉上下文 | — | 否（节拍要求 `full`） |
| translate / transcribe / asr_only | 文本/首帧直起轮均被模式门控排除；帧仅入 buffer | — | 否 |

- 关键代码：buffer 入队 2033-2071；v3 文本回合 2073-2151；turn 首帧直起轮 2153-2232；omni 投喂 2234-2249；avdetector 2251-2276；`_snapshot_turn_images` 2291-2307。
- 每次起轮的 `final ai_text` 会**回带本轮帧**（`image_frames[-1]` → `ServerAiTextImage`，orchestrator 1585-1596）。客户端 `onAiText` 收到 image 后写入 Room（`ChatRepository` 105-148），因此照片最终会出现在 **AI 回复气泡**的图片位。
- App 端不接收服务端 user 行：本地会话气泡来自 `listenerAdapter.onTranscription/onAiText`，服务端 `chat_store` 的 user 行不在客户端会话流中。故本功能不会产生「用户侧图片气泡」，照片只会在 AI 回复气泡里出现（现有相机帧同款行为）。
- 服务端 `_handle_video` 的「入 buffer → 快照」之间无 await；照片帧与其自身快照在同一同步段，相机的并发帧不会在中间抢走它。
- `camera_facing` 当前**只作为元数据存储/回显**：不参与 LLM prompt、avdetector prompt 或客户端渲染（检索 `services/` 与 App 无消费方）。

#### 9.2.3 与现有链路的关系

- 图库照片与相机帧、屏幕抓取共用同一条 `video` 消息与同一个 K=1 服务端缓冲；**不是**新的消息类型，也不经过 `pushBitmap`（那会把长边压到 SDK 的 `videoMaxLongSide=1080`，不满足 1920 需求）。
- 图片经 `pushVideoJpeg` 直通，尺寸责任在 App；服务端只校验 base64/协议字段，不做缩放。
- 最终上行字节 = 重编码后的 JPEG（原图 EXIF/GPS 元数据不会随行；`Bitmap.compress` 不写 EXIF）。

### 9.3 UI/UX 设计

#### 9.3.1 位置与形态（D1=A / D5 / D9，定稿；摄像头关闭时常驻）

- 「+」作为**摄像头 ToolbarButton 的 `captionLeading`**，位于「摄像头 / Camera / カメラ / الكاميرا…」首词前；**所有模式都显示**（D5=常驻），仅摄像头开启时隐藏（D9）。
- 非 chat 模式选图的行为：omni 仅作视觉上下文（无立即回复）；translate/lecture/transcribe 不产生照片回合。**这些“无回合”行为已由 review 接受**（§9.5.3 / §9.9 D5）。
- **摄像头开启时（D9）**：「+」隐藏，且整个 caption 行不可点击——此时无法选图，需先关闭摄像头。开关态直接由既有 `isCameraOn` 驱动，无需新增 UiState。
- 实现：`showGalleryEntry = !isCameraOn`；`captionLeading = if (showGalleryEntry) { ... } else null`、`onCaptionClick = if (showGalleryEntry) onPickGallery else null`。`captionLeading == null` 时 `ToolbarButton` 既有的 18dp 尾部对称占位也不渲染，文字自然居中，无布局残留。
- 入口可用时，复用现有 `captionLeading + onCaptionClick` 机制：整行「+ 摄像头」可点，打开系统图库；正上方 48dp 相机按钮仍只负责开关相机，互不误触。
- 视觉 token 与麦克风 `^` 完全一致：`Icons.Filled.Add`、16dp、`TextSecondary`、与文字间距 2dp；文字后保留 18dp 对称占位，使「摄像头」文字中心仍与上方 48dp 图标轴对齐。
- RTL：Compose `Row` 自动镜像，阿拉伯语下「+」出现在文字阅读方向前方（屏幕右侧），无需额外分支。
- 未选中任何图片时点「+」只打开系统选择器；取消 = 无操作。选择成功后立即上行，无预览/确认步骤（需求只要求选择与传递）。

**本地渲染预览（不入库）**：`/tmp/camera_gallery_toolbar_mock.png`（HTML 源 `/tmp/camera_gallery_toolbar.html`）。
A = 所有模式 + 摄像头关闭时整行可点（定稿）；B = 仅「+」可点（16dp 本体需额外扩触区，且与现有两列交互不一致，不采纳）。预览含摄像头开启态：无「+」、caption 行不可点；非 chat 模式外观与 A 相同（「+」常驻）。

#### 9.3.2 选择器

- `ActivityResultContracts.PickVisualMedia()`（单图契约，不是 `PickMultipleVisualMedia`）+ `PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)`。
- 与头像选择同一实现（`ConversationScreen.kt` 126-133），**不需要** `READ_MEDIA_IMAGES` / `READ_EXTERNAL_STORAGE` 权限；系统 Photo Picker 在 API 26+ 由 Activity 1.9.0 自动回退。
- 回调拿到 `Uri` 后交给 VM；不在 UI 层解码。
- **v6 变更（§10，已实施）**：picker 回调先进入全屏预览；用户点击「发送」后才执行 §9.4/§9.5 的处理与上行。

#### 9.3.3 选择后的可见反馈

- 系统选择器关闭，页面原样返回；照片编码/上行约百毫秒级，不新增 loading UI。
- 服务端起轮后状态指示器进入 THINKING、随后 AI 回复；照片出现在 **AI 回复气泡**的图片位（§9.2.2）。这是现有相机帧的既有展示方式，不新增用户侧图片气泡。
- **摄像头开启时「+」隐藏、caption 行不可点（D9）**：此时不可选图；关闭摄像头后「+」恢复。
- **非 chat 模式（omni / translate / lecture / transcribe）「+」常驻**（D5）：omni 选图仅作视觉上下文（无立即回复）；translate/lecture/transcribe 选图不产生照片回合。这两种“无回合”行为已接受。
- **screen recording（chat）中「+」可见**（相机必关）：选图照常触发 T1 回合，屏幕录制继续。

#### 9.3.4 无障碍与文案（D4 相关）

- 「+」的 `contentDescription` **复用** `conv_avatar_from_gallery`（15 目录已齐平，数值即「从相册选择 / Choose from gallery」），避免新增 key 与全量翻译；该语义仅在实际渲染（摄像头关闭）时暴露给 TalkBack。
- 未采纳备选：新增 `conv_toolbar_pick_gallery` 并同步 15 个 `strings.xml`（`I18nResourceFilesTest` 会强制 key 齐平）；若未来要求语义独立可再切换，成本见 §9.8。
- **v7.2（2026-10-27 已实施）**：上行指令改为 15 locale 资源 `conv_image_action_text`，与屏幕抓取共用、随 App 语言、英文兜底。

### 9.4 图片处理设计

#### 9.4.1 责任边界

新增 `GalleryPhotoSender`（`dev.livachat.app.media`，`@Singleton`），职责：`Uri` → 规范化 JPEG → `sdk.pushVideoJpeg`。放 media 层与 `CameraController` / `ScreenCaptureController` 同构；VM 只负责事件转发与错误提示。

```kotlin
@Singleton
class GalleryPhotoSender @Inject constructor(
    private val sdk: LivaChatSDK,
    @ApplicationContext private val context: Context,
    private val turnCounter: CaptureTurnCounter,
) {
    // omniMode：全模态下 text=null（镜像 ScreenCaptureController D6b），帧仅作视觉上下文；
    // 其他模式传发图指令（v7.2 已实施为本地化资源 conv_image_action_text；服务端模式门控决定是否起轮）。
    // isStillAllowed：编码完成后、push 前复检摄像头状态（D9），防编码窗口内开相机的竞态发送。
    internal suspend fun send(uri: Uri, omniMode: Boolean, isStillAllowed: () -> Boolean): GallerySendResult
}

/** 三态：REJECTED 是用户状态变化导致的静默丢弃，不得映射为错误横幅。 */
internal enum class GallerySendResult { SENT, REJECTED, FAILED }
```

**线程**：`withContext(Dispatchers.IO)` 内完成解码/旋正/缩放/编码；`sdk.pushVideoJpeg` 自身复制字节并投递到 SDK 的视频发送协程，可在 IO 线程调用。

**竞态守卫（§9.10 F2）**：编码完成后、`pushVideoJpeg` 前调用 `isStillAllowed()`；VM 传入 `{ !_uiState.value.isCameraOn }`（D9：摄像头开启即不应再上行）。拒绝 → `REJECTED`（不 push、**不弹错误横幅**，debug 日志）；解码/编码失败 → `FAILED`（VM 按 D4 提示）。**模式切换不拦截**：非 chat 选图照常上行，由服务端门控 / omni 分支决定效果（D5 常驻语义）。

#### 9.4.2 步骤（全部以现有代码为参照）

1. **读 bounds**：`contentResolver.openInputStream(uri)` + `BitmapFactory.Options(inJustDecodeBounds = true)`；`outWidth/outHeight <= 0` → 失败。
2. **采样**：`inSampleSize` = 不超过 `maxDim / maxLongSide` 的最大 2 的幂（至少 1）。目标：采样后长边仍 ≥1920（保证缩放质量），同时把采样后长边限制在 <3840（即不到 2×1920）。与头像的 `maxDim/2048` 同思路，但按 1920 标定。
3. **解码**：`BitmapFactory.decodeStream(..., inSampleSize, inPreferredConfig = ARGB_8888)`；null → 失败。
4. **EXIF 旋正**：读取 `ExifInterface.TAG_ORIENTATION`，覆盖八种朝向（90/180/270、水平/垂直翻转、transpose/transverse），用 `Matrix` 生成正立位图；中间位图回收。实现可直接复用/抽取 `AvatarCropScreen.applyExifOrientation`（D7）。
5. **精确缩放**：长边 >1920 时按比例缩到长边恰为 1920（只缩不放），宽高各自 `coerceAtLeast(1)`；与 `CameraController.scaledMaxLongSide` 同规则（但上限为 1920）。旋转与缩放的先后：**先缩放后旋正**（相机路径同序，90°/270° 下长边不变，先缩可降低峰值内存）。
6. **JPEG 编码**：`Bitmap.compress(JPEG, 70, out)`；q70 与相机/屏幕链路一致。输出宽高取旋正后的像素尺寸；上行 `orientation = Orientation.PORTRAIT_0`（像素已正立，协议恒 0，与相机路径一致）。
7. **清理**：`finally` 回收所有中间 Bitmap；失败路径记 `sdk.logWarn("GalleryPhoto", ...)` 并返回 false。

#### 9.4.3 常量与体积

| 常量 | 值 | 依据 |
|---|---|---|
| `MAX_LONG_SIDE` | 1920 | 需求；与屏幕抓取同上限（`ScreenCaptureController.MAX_LONG_SIDE`） |
| `JPEG_QUALITY` | 70 | 与相机/屏幕链路一致；屏幕抓取 1920/q70 实测约 158.6KB（`ScreenCaptureController.kt` 906-911 注释） |
| 上行协议上限 | ws 16MiB | 1920/q70 远低于上限，base64 膨胀约 1.33× |

#### 9.4.4 已知边界

- **透明 PNG**：JPEG 无 alpha，透明区域会变黑；图库照片通常不透明，接受并记录（如需白底可后续在 Canvas 上铺白再编码）。
- **HDR / 广色域 / 动图**：重编码为 8-bit JPEG，取 GIF 首帧；色彩管理由系统解码器决定。
- **HEIC**：解码能力取决于系统/厂商实现（API 28+ 一般可用）；`BitmapFactory` 返回 null 时走失败路径，不崩溃。
- **超大图**：采样已把解码长边限制在 <3840（不到 2×1920）；仍捕获 `OutOfMemoryError` 收敛为失败（不崩溃）。
- **原图元数据**：EXIF/GPS 不随重编码上行（隐私正向）；方向已烘进像素。

### 9.5 上行与触发语义

#### 9.5.1 上行调用（定稿 T1 + F1；v7 协议扩展）

```kotlin
sdk.pushVideoJpeg(
    jpeg = encoded.jpeg,
    width = encoded.width,
    height = encoded.height,
    orientation = Orientation.PORTRAIT_0,
    cameraFacing = "gallery",             // v7.1 D3=F1 已实施（§11）
    turnId = turnCounter.current,         // 不递增：本轮由 text 触发
    text = if (omniMode) null else context.getString(R.string.conv_image_action_text),  // v7.2 已实施；omni 仅视觉上下文
)
```

- 每次选择只推 **1 帧**，不周期推送；不经过 `pushVideoBitmap`。
- **v7.2 已实施**：`GALLERY_ACTION_TEXT` 与 `ScreenCaptureController.TAP_CAPTURE_TEXT` 合并为同一资源 `conv_image_action_text`（15 locale，随 App 语言，英文兜底），避免两处文案漂移。
- **omni**：`text=null`（镜像屏幕抓取 D6b），帧只作视觉上下文、需用户开口才回；**translate / lecture / transcribe**：仍带 `text`，但服务端 v3 模式门控排除，照片不会起轮（D5 常驻下已接受的“无回合”行为）。

#### 9.5.2 触发方式选型（D2）

| 方案 | 做法 | 服务端改动 | 用户行 content | avdetector | 评价 |
|---|---|---|---|---|---|
| **T1（定稿；v7.2 已实施改为本地化）** | `text = context.getString(R.string.conv_image_action_text)`，不递增 turn | 零 | 本地化发图指令 | 否（text 门控） | 复用屏幕抓取已验证的 v3 文本回合；chat 下立即回应；其他模式无回合（accepted）；无相机语义误标 |
| T2 | `turnCounter.next()`，`text = null` | 零 | `[相机画面]` + 相机注记 | 是 | 一行代码即触发，但服务端把照片标成"刚开相机/切镜头"，历史与系统注记失真；不推荐 |
| T3 | 协议加 `gallery` 来源 + 服务端 gallery 分支（`[相册图片]` + 相册注记） | 协议 + 服务端 | `[相册图片]` | 可定义 | 语义最干净，但跨栈改动最大；可与 D3=F1 一并作为后续演进 |
| T4 | 不触发，仅入 K=1 buffer | 零 | — | 是 | 字面"只传给 server"；但 5s 空闲清空，用户选图后稍慢开口即丢图，且无即时反馈；不推荐 |

**定稿 T1 的理由**（review 2026-10-17 确认「选图即让 AI 立刻描述」；立即生效仅 chat，其他模式为 accepted 行为，见 §9.5.3）：
1. 屏幕抓取点按已经用同一条链路（`video.payload.text` → 快照 → 三件一体 → 起轮）验证过，协议/SDK/服务端均无需新行为；
2. 服务端会把照片作为本轮 user 图送 LLM，final `ai_text` 回带同一帧，客户端图片气泡与回答一致；
3. 无 text 的 turn_id 路径会写入 `[相机画面]` 并注入"用户刚开启摄像头/切换镜头"的系统注记，对图库照片是错误语义；
4. T1 的 text 帧**不进 avdetector**，避免一次性照片在后续视频检测里被重复消费。

**代价（如实记录）**：服务端历史与录制落盘（recording tap）会多出一条 user 行（v7.2 已实施为本地化发图指令）；其下游展示（如时间线）取决于既有数据源。App 会话页不显示服务端 user 行（只显示 AI 回复气泡），所以用户侧主要感知是 AI 直接描述图片。若这个合成 user 行不可接受，选 T3（服务端改动）。

#### 9.5.3 模式行为

| 模式 | 行为 |
|---|---|
| chat（默认） | T1：立即文本回合；spinner/状态随服务端 thinking；回复带图 |
| omni | **「+」常驻（D5）**：选图发送 `text=null` 帧 → 仅 `feed_image` 作视觉上下文；需用户开口才回（accepted）。provider 对超大图会 960→640 重压，仍超 262144B 则跳过（既有约束）。免开口起轮需 manual 架构（`docs/fd_pipeline_omni_mode.md` §10 TODO-1），不在本期（D10） |
| translate / lecture / transcribe | **「+」常驻（D5）**：选图仍带 `text` 上行，但服务端 v3/turn 门控排除 → 不产生照片回合（accepted）；语音链路不受影响 |
| screen recording（chat） | **「+」常驻**（相机必关）：选图照常触发 T1 回合；录制继续，服务端 recording tap 照常记录该 user 行与图 |
| 断连 | SDK `sendVideo` 静默丢弃（warn 日志）；无新错误 UI，与相机帧一致；入口仅受相机开关约束（D9），与连接态无关 |

#### 9.5.4 与模式 / 摄像头开关的关系（D5 / D9，review 定稿）

- 入口可用条件 = **摄像头关闭（`!isCameraOn`）**；**所有模式常驻**（D5）；摄像头开启时隐藏「+」并禁用 caption 回调（D9）。
- **模式切换不隐藏入口**（D5）：非 chat 选图照常上行，由服务端模式门控 / omni 分支决定效果；无回合为已接受行为。
- **摄像头开启时不可选图**：「+」隐藏、caption 行不可点；需先关闭摄像头才能打开图库。
- 这样在设计上直接排除了「相机帧与照片帧并发上行」的竞争面：照片上行时相机必然关闭，服务端不会同时收到相机帧。
- 摄像头关闭/开启切换由现有 `isCameraOn` 驱动；不新增互斥状态、不自动开关相机。
- 选择器打开期间如果摄像头原本是关闭的，返回后仍关闭；照片处理与相机生命周期无交叉。`isCameraOn` 变化（开/关）会立即隐藏/恢复「+」，并同步禁用/启用 caption 回调。

### 9.6 边界矩阵

| 场景 | 预期行为 |
|---|---|
| 选择器取消 / 返回 null | 无操作，无日志噪音 |
| URI 不可读 / 文件已删除 / 非图片 | 解码失败 → 记 warn，不崩溃、不发帧；**失败提示见 D4** |
| 长边 ≤1920 | 不放大，仅 EXIF 旋正 + 重编码 |
| 长边 >1920（横/竖） | 等比缩到长边 1920；宽高按比例 |
| EXIF 90/180/270/镜像 | 旋正后上行，`orientation=0` |
| 透明 PNG | JPEG 黑底（已知限制） |
| 动图 GIF/WebP | 取首帧 |
| 摄像头开启时想选图 | 「+」隐藏、caption 行不可点（D9）；需先关闭摄像头 |
| omni | 「+」常驻（D5）；选图发 `text=null` 帧，仅投喂视觉上下文，需用户开口才回（accepted；D10 / TODO-1） |
| translate/lecture/transcribe | 「+」常驻（D5）；服务端模式门控，选图不会产生照片回合（accepted） |
| 选图编码中切到非 chat | 不拦截：照常上行；omni 下 text=null，其余由服务端门控（无回合为接受行为） |
| 非 chat 选图后 5s 内切回 chat 并说话 | 照片仍在 K=1 buffer，可能被 AF 快照消费（服务端既有语义）；5s 空闲后自动清空。低概率边缘，接受（D5 常驻） |
| 选图编码中开相机 | VM 下行前复检 `!isCameraOn`；不满足则 `REJECTED`（静默丢弃，不弹错误） |
| screen recording（chat）中选图 | 「+」常驻（相机必关）；照片照常触发 T1 回合，录制继续 |
| 断连 | SDK 丢弃并记 warn；无崩溃/无卡死 |
| 危险回合在飞（备选 T2/T3） | 首帧直起轮按既有 P6 豁免放弃；T1 走既有中断接管 |
| 选图后 <1s 立即说话（T1） | AF 按既有换装接管；照片已作为 history user 行（含图）在最近图片窗口内，模型可见；本轮 final 回图取决于 AF 快照（可能无图）——与「开相机后立即说话」的既有边界同型 |
| 快速连续选两张 | 允许；第二次按服务端既有中断语义接管（D8 可选 in-flight 守卫） |
| 选择器打开时旋转/Activity 重建 | `rememberLauncherForActivityResult` 重新注册并投递结果；VM 为 nav 级，事件不丢 |
| 旧服务端（仅备选 D3=F1） | 服务端按非法枚举丢帧（error 1006）；**部署必须服务端先行**，否则功能不可用 |
| 15 语言 / RTL | 复用现有 key 时零新增；D1=A 的「+」在 RTL 自动镜像 |

### 9.7 测试计划

#### 9.7.1 JVM / Robolectric（App）

- 缩放纯函数：长边 1919/1920/1921、横/竖、极端比例、`coerceAtLeast(1)`、只缩不放。
- `inSampleSize` 纯函数：大图采样后长边仍 ≥1920；小图恒 1；2 的幂边界。
- 解码/EXIF：用临时图片（4000×3000 + EXIF Orientation=6）→ 输出长边 1920、宽高正立、JPEG magic；八种朝向至少覆盖 90/180/270 + 一个镜像。
- `GalleryPhotoSender`（mock SDK）：非 omni → `text=context.getString(R.string.conv_image_action_text)`（v7.2 已实施）、`facing="gallery"`（v7.1；v5.4 当时为 `screen`）、`turnId=current`；omni → `text=null`；解码失败 → `FAILED` 且不 push；`isStillAllowed=false`（相机开启竞态）→ `REJECTED` 且不 push（**不得触发失败横幅**）。
- VM 守卫：编码期间开相机 → `REJECTED`，不 push；切模式 → 不拦截、照常上行（服务端门控/omni 分支决定效果）；VM 对 `REJECTED` 静默、仅 `FAILED` 提示。
- **v7.2 已实施**：`GalleryPhotoSenderTest` 的 text 断言改为 `context.getString(R.string.conv_image_action_text)`；`I18nResourceFilesTest` 覆盖新 key 的 15 目录齐平。
- 复用/抽取后的 `applyExifOrientation` 与头像路径回归（头像裁剪现有测试）。

#### 9.7.2 androidTest（Compose）

在 `BottomToolbarTest` 追加（与麦克风 `^` 同型回归锁）：
- 摄像头关闭时（所有模式），「+」在「摄像头」文字之前（unmerged bounds 比较）；
- 同上条件点「+ 摄像头」行触发 gallery 回调，**不**触发 `onToggleCamera`；
- **摄像头开启时「+」不渲染、caption 行不可点**（点击不触发 gallery 回调）；
- 模式 flag 变化不影响「+」可见性（D5 常驻；仅 `isCameraOn` 决定隐藏）；
- 隐藏态文字仍居中（无前置图标时 18dp 尾部占位也不渲染）；
- 「摄像头」文字中心与上方 48dp 相机图标中心对齐（18dp 对称占位）；
- caption 触区高度 ≥20dp（仅可点态存在可点区域）；
- 「+」的 contentDescription = `conv_avatar_from_gallery`（定稿复用）。

#### 9.7.3 真机 / 端到端

1. 亮环境选一张大图（>1920）→ 服务端收到 1 帧、长边 1920、AI 回复带图且方向正确；`final ai_text` 图片与所选一致。
2. 竖拍 EXIF 90° 照片 → 不歪。
3. 摄像头开启 → 「+」隐藏、点「摄像头」文字无反应；关闭摄像头 → 「+」恢复，选图后照片触发回合。
4. chat 模式选普通照片 → AI 立刻开始描述（THINKING → 回复带图），不等待用户开口（T1 核心验收）。
5. omni / translate / lecture / transcribe → 「+」常驻可见；omni 选图仅作视觉上下文（开口才回）；translate/lecture/transcribe 选图无照片回合（accepted）。
6. 断网选图 → 不崩溃；恢复后相机/语音正常。
7. 选择器取消、连续快速选两张、选择器打开时旋转。
8. RTL（ar）工具栏：+ 在文字阅读方向前方；15 语言资源守卫通过。
9. 服务端日志：`video text turn` / LLM 请求含 data URL / final ai_text 回图（若 T2 则为 `video turn`）。
10. screen recording（chat）中选图 → 照片照常触发 T1 回合，录制继续、recording tap 记录该 user 行与图（accepted）。

### 9.8 实施影响清单

| 层 | 文件 | 改动 |
|---|---|---|
| App UI | `ui/components/BottomToolbar.kt` | **仅 `!isCameraOn` 时**为摄像头 ToolbarButton 挂 `captionLeading = Icons.Filled.Add` + `onCaptionClick`（D5 常驻 / D9 相机开启隐藏）；新增**必选**回调参数 `onPickGallery`（无默认空实现：D5 常驻入口漏接线必须编译期失败；已同步 2 处 `@Preview` 与 `BottomToolbarTest` 全部 21 处构造点） |
| App Screen | `ui/screens/conversation/ConversationScreen.kt` | 新增图库单图 launcher；工具栏接线（使用既有 `isCameraOn` 参数，无新模式布尔） |
| App 事件 | `ui/screens/conversation/ConversationEvent.kt` | `data class SendGalleryPhoto(val uri: Uri)` |
| App VM | `ui/screens/conversation/ConversationViewModel.kt` | 调用 `sender.send(uri, omniMode = _uiState.value.isOmniMode, isStillAllowed = { !_uiState.value.isCameraOn })`；按结果分流：`SENT` 无提示、`REJECTED` 静默、`FAILED` 按 D4 提示。模式切换不拦截 |
| App 媒体 | **新增** `media/GalleryPhotoSender.kt` | bounds/采样/解码/EXIF/缩放/q70/`pushVideoJpeg`；可测纯函数 |
| App 共享 | `ui/screens/conversation/AvatarCropScreen.kt` → 可选 `media/ImageExif.kt` | D7：抽取 `applyExifOrientation` 复用（或复制） |
| App 测试 | `CameraControllerTest` 风格单测 / `BottomToolbarTest` / `GalleryPhotoSenderTest` | §9.7 |
| SDK | v7.1 已实施 | `pushVideoJpeg` 已支持 facing 参数；`mapCameraFacing` 加 `"gallery"→GALLERY` + 协议模型已重生成 |
| 协议/服务端 | v7.1 已实施，已部署 | `common.json` CameraFacing 增加 `"gallery"` + 已重生成 Python/Kotlin + 服务端 gallery 测试；运行实例已加载新枚举（§11.4/§11.10） |
| i18n | 无（复用 `conv_avatar_from_gallery`） | 备选 D4 新 key：15 个 `strings.xml` 同步，`I18nResourceFilesTest` 强制齐平 |
| 版本 | `LivaChatApp/build.gradle.kts` | 按项目惯例：**仅在 git 提交时递增 versionCode**，迭代期不动 |
| 文档 | 本文档 §9 + 附录 C | 实施后回填状态与真机结果 |

### 9.9 决策定稿

| # | 决策点 | 选项 | 定稿（2026-10-17 review） |
|---|---|---|---|
| D1 | 「+」点按热区 | A：整行「+ 摄像头」可点（复用 captionLeading/onCaptionClick）；B：仅「+」可点 | **A**：摄像头关闭时整行可点；摄像头开启时整行不可点（见 D9） |
| D2 | 触发语义 | T1 text 回合；T2 turn_id 首帧；T3 服务端 gallery 分支；T4 仅缓冲 | **T1**：选图即让 AI 立刻描述（v7.2 已实施 `text=context.getString(R.string.conv_image_action_text)`；review 反馈中的「D1: 选图即描述」即本项）；**仅 chat 立即生效**；非 chat 无回合为接受行为 |
| D3 | `camera_facing` 取值 | F1 协议新增 `"gallery"`（服务端先行）；F2 复用 `"screen"` 占位 | **F1（v7.1 已实施，2026-10-17）**：新增 `"gallery"`；schema 重生成 + SDK 映射 + App 发送端已完成；服务端已部署，真机 E2E 通过（§11.10） |
| D4 | 失败提示 | 复用 `cap_bubble_failed`（"抓取失败，请重试"，15 语言已有）；新增 `conv_gallery_send_failed`；仅日志 | **复用 `cap_bubble_failed`**（零 i18n 改动） |
| D5 | 「+」的可见模式 | 常驻；仅 chat/omni；仅 chat | **常驻（v5.3 二次拍板）**：所有模式显示，仅摄像头开启时隐藏（D9）。非 chat 无照片回合、omni 仅视觉上下文，均为**已接受行为** |
| D6 | JPEG 质量 | 70；85 | **70**（与相机/屏幕一致，体积小） |
| D7 | EXIF 旋正实现 | 抽取 `applyExifOrientation` 到 `media/ImageExif.kt` 复用；复制一份 | **抽取**（八种朝向逻辑只维护一处；头像路径做回归） |
| D8 | 连续快速选图 | 允许（服务端中断接管）；客户端 in-flight 守卫 | **允许**（每次选图都是显式用户动作；避免静默吞掉第二张） |
| D9 | 摄像头开启时的「+」 | 隐藏且不可点击；保持可用 | **隐藏且不可点击**（review 定稿；需先关相机才能选图） |
| D10 | omni 模式免开口描述 | 本期不做；服务端 TODO-1（manual 架构 + `response.create`，大改动） | **本期不做**：现有 omni 为 `semantic_vad`，免开口起轮不可行；「+」常驻，选图仅作视觉上下文（accepted） |

**review 定稿 + 复核 + D5/D3 更新（2026-10-17）**：D1=A（摄像头关闭时整行可点）、D2=T1（仅 chat 立即生效；非 chat 无回合 accepted）、**D3=F1（v7.1 已实施 gallery；服务端已部署，真机 E2E 通过）**、D4=复用 `cap_bubble_failed`、**D5=常驻（所有模式；仅相机开启隐藏）**、D6=q70、D7=抽取公共 EXIF、D8=允许连选、D9=摄像头开启时「+」隐藏且不可点、D10=omni 免开口描述本期不做（仅视觉上下文）。D1/D2/D4–D10 已实施（§9.11）；D3 协议扩展已实施（§11.10），服务端已部署且真机 E2E 通过。

---

### 9.10 复核记录（2026-10-17，doc review）

对抗式复核范围：§9 全部事实断言 + D1–D10 组合 + 服务端模式门控 + omni 能力 + 测试覆盖。发现并处理：

#### F1（严重 → v5.3 二次拍板：接受）D5=常驻与「选图即立刻描述」在非 chat 模式冲突

**事实取证**：

- v3 文本回合门控为 `_pipeline_mode not in ("translate", "transcribe", "asr_only", "omni")`（`orchestrator.py` 2084-2086）。translate / lecture / transcribe 下选图 → 帧仅入 K=1 buffer，**照片本身不会产生回合或可见效果**（该模式的语音链路不受影响）。
- omni：文本回合同样被门控排除；帧只走 `feed_image`（`orchestrator.py` 2237-2249）。现有 omni 引擎跑 `semantic_vad`（`services/omni/providers/alibaba_omni.py:543`），回合由**语音**触发（`docs/fd_pipeline_omni_mode.md` §4 D6b）；免开口起轮需 manual 架构 + `response.create`——探针 V1（semantic_vad）30s 无回复、V2（manual）成功，属 **TODO-1，未实施**（同文档 §10）。
- 因此常驻 D5 会让 omni / translate / lecture / transcribe 出现「+ 可点但无反应」的**死控件**。

**处理（2026-10-17 二次拍板）**：

- **D5 保持常驻**：所有模式显示「+」（仅摄像头开启时按 D9 隐藏）；**接受**非 chat 模式无回合、omni 仅作视觉上下文的死控件行为。
- 撤销 v5.2 的 chat-only 方案：不新增 `canUseGallery` / `showGalleryPicker`；入口条件仅为 `!isCameraOn`。
- `GalleryPhotoSender` 恢复 `omniMode` 参数（omni 下 `text=null`，镜像屏幕抓取 D6b）；保留下行前 `isStillAllowed = { !isCameraOn }` 守卫（见 F2）。
- 行为记录同步到 §9.1 / §9.3.1 / §9.3.3 / §9.5.1 / §9.5.3 / §9.5.4 / §9.6 / §9.7 / §9.8 / §9.9。

**决定依据**：用户 2026-10-17 明确「D5=常驻，其他模式也常驻；对话不工作，recording 也工作，可以接受」。若未来要消除死控件，可补「当前模式不支持图片」提示或做 omni TODO-1，均不在本期。

#### F2（中，修正保留）入口竞态：编码/上行期间开相机

- 编码窗口内摄像头可能被打开（D9 要求此时不可选图）；VM/sender 在下行前复检 `!isCameraOn`，不满足则 `REJECTED`（静默丢弃 + debug 日志，**不弹错误横幅**）；解码/编码失败才是 `FAILED`（D4 提示）。
- 模式切换**不拦截**（D5 常驻）：非 chat 照常上行，由服务端门控 / omni 分支决定效果；`REJECTED` 只对应摄像头开启。

#### F3（已核对，无需改）`camera_facing` 无消费方

- `services/` 与 App 检索：该字段仅入 buffer / 回显 / `ChatImageFrame`；recording sink 只把 `frames` 的 JPEG 写入 `image/`（`_write_item_images` 不写 `camera_facing`）；LLM / avdetector prompt 不使用；客户端只存不渲染。v6.4 的 `"screen"` 占位安全；v7.1 改 `"gallery"` 后仍无消费方（仅存储/回显）。

#### F4（已核对，无需改）chat 模式 T1 可达

- v3 分支入口条件为 `processed && data && text && _count_meaningful_chars(text) >= 2 && mode not in (...)`（2084-2086）；v7.2 已实施的 15 locale 指令均 ≥2 字，chat 模式可达；`_snapshot_turn_images` 同步段取走照片；final `ai_text` 回图（1585-1596）。

#### F5（已核对，无需改）D9 相机并发面

- 摄像头开启时「+」隐藏/不可点，照片上行时相机必关；照片的 v3 分支「append → snapshot」在同一同步段消费照片本身。关闭瞬间的极少量在途相机帧受 analyzer `generation` 守卫，且照片选择耗时远长于帧管线，概率可忽略。

#### F6（测试覆盖补齐）

- 新增：D5 常驻矩阵（chat / omni / translate / lecture / transcribe × 相机开/关；仅相机开关影响可见性）；omni → `text=null`；camera-on 竞态 → `REJECTED`；screen recording 场景（照片回合 + 录制继续）；真机模式切换项。
- 既有：头像 EXIF 回归；BottomToolbar 与麦克风 `^` 同型锁。

**复核结论**：F1 经 2026-10-17 二次拍板定稿为「D5=常驻，接受非 chat 无回合」；F2 的相机竞态守卫保留；F3–F5 核对无问题；未发现新的协议/服务端阻塞项。已按本记录实施（见 §9.11）；chat 主链路真机 E2E 通过，其余模式/边界待验证。

---

### 9.11 实施记录（2026-10-17）

按 v5.3 定稿实施。改动全部在 App 侧；协议 / SDK / 服务端零改动。

| 层 | 实际改动 |
|---|---|
| UI | `BottomToolbar`：摄像头关闭时 `captionLeading = Icons.Filled.Add`、`onCaptionClick = onPickGallery`；摄像头开启时隐藏且整行不可点；contentDescription 复用 `conv_avatar_from_gallery`。`ConversationScreen`：新增 `photoGalleryLauncher`（`PickVisualMedia` 单图 `ImageOnly`）并接线工具栏。 |
| 事件 / VM | `ConversationEvent.SendGalleryPhoto(uri)`；`ConversationViewModel` 注入 `GalleryPhotoSender`；`onEvent` 分支；`sendGalleryPhoto()` 按 `SENT / REJECTED / FAILED` 分流（仅 `FAILED` 用 `cap_bubble_failed` 提示）。 |
| 媒体 | 新增 `GalleryPhotoSender`：bounds → `inSampleSize` → ARGB_8888 解码 → 长边 1920 缩放 → EXIF 旋正 → JPEG q70 → `pushVideoJpeg(facing="screen", turnId=current, text=null if omni else GALLERY_ACTION_TEXT)`；`isStillAllowed = { !isCameraOn }`；OOM/解码/编码失败收敛为 `FAILED`。**v7.1 已把 facing 改为 `"gallery"`（§11）；v7.2 已将 GALLERY_ACTION_TEXT 改为 `conv_image_action_text` 资源**。 |
| EXIF | 从 `AvatarCropScreen` 抽取 `applyExifOrientation` 到 `media/ImageExif.kt`，头像裁剪与图库共用（契约：需要旋转时创建新位图并回收输入）。 |
| 测试 | 新增 `GalleryPhotoSenderTest` **14 例**（采样/缩放纯函数、采样与缩放编码、EXIF 八朝向像素归属、chat/omni 上行参数、REJECTED、FAILED、解码 OOM）；`ConversationViewModelVideoTurnTest` +2 例（FAILED 提示 / REJECTED 静默）；`BottomToolbarTest` +4 例（+ 位置/点击、居中、触区、相机开启隐藏且不可点）。**v7.1 已把 sender 断言从 `"screen"` 更新为 `"gallery"`（§11）**。 |
| 版本 | **versionCode 未动**（项目惯例：仅在 git 提交时递增；本轮未提交）。 |

**验证证据**

- `:LivaChatApp:testDebugUnitTest` 全量：**94 suites / 929 tests / 0 failures / 0 errors / 4 skipped**（XML 汇总）。
- 真机 `LIO-AN00`（Android 12）`am instrument -e class dev.livachat.app.ui.components.BottomToolbarTest com.livachat.livachatapp.test/androidx.test.runner.AndroidJUnitRunner`：**OK (21 tests)**。
- `:LivaChatApp:assembleDebug`、`:LivaChatApp:compileDebugAndroidTestKotlin` 通过；主 app 已用 `:LivaChatApp:installDebug` 安装（未跑 connectedDebugAndroidTest，避免卸载清数据）。
- 测试卫生：Robolectric 的 `Failed to create image decoder` 噪音已通过把「不可读 URI」用例改为不存在的 `file://` URI 消除。

**真机 chat 主链路 E2E（2026-10-17，LIO-AN00 / Android 12）**

- 「+」在摄像头关闭时可见（`content-desc="从相册选择"`）；点击打开系统 DocumentsUI `PickActivity`（`PickVisualMedia` 在 Android 12 上回退到 `ACTION_OPEN_DOCUMENT` 路径）。
- 选择 `livachat_gallery_test.png`（805×799，<1920，无 EXIF 旋转）→ app log：`D/LivaChat-GalleryPhoto: photo pushed 805x799 bytes=69672 omni=false`。
- 服务端 v3 文本回合立即触发：AI 回复直接描述图片内容，AI 气泡回带同一张图；摄像头保持关闭，「+ 摄像头」持续可见。
- 截图（本地，不入库）：`/tmp/livachat_gallery_after.png`。
- 已覆盖：D5（chat 常驻可见）、D9（相机关闭可见）、T1（立即描述）、单图 picker、Uri → 编码 → `pushVideoJpeg`、final `ai_text` 回图。
- 未覆盖（v5.4 当时）：>1920 缩放与 EXIF 竖拍的真机目视、omni/translate/录屏、断网/取消/连续/旋转/RTL。

**真机端到端（v7.1 构建，2026-10-17 已完成，§11.10）**

1. **大图 + EXIF**：4000×3000 / EXIF Orientation=6 → 预览正立 → 采样 2000×1500 → 缩放 1920×1440 → 旋正后 **1440×1920** 上行；AI 描述并回图，回显落库 `gallery`。
2. **omni**：`+` 可见；`photo pushed ... omni=true`，无即时描述回合（视觉上下文；麦克风静音下 8s 无 ai_text）。**translate/transcribe**：`+` 可见；`omni=false` 帧被 ack，但无照片回合（服务端门控，accepted）；**lecture**：`+` 可见（同一 gate）。
3. **screen recording**：抓屏 tap → `pushed 940x1920`，回显 `screen`；录制中选图 → `pushed 1440x1920`，AI 描述并回显 `gallery`，录制继续；停止确认正常。
4. **取消/连续**：右取消/左返回→picker 取消均无上行；两次连续发送（间隔 5s）均推送并各自回图。
5. **旋转**：Manifest `screenOrientation="portrait"`（锁定竖屏），设备旋转不改变预览，属设计内。
6. **RTL**：系统阿拉伯语下工具栏「+」位于文字阅读起点侧（右侧）、预览左/右按钮镜像（返回在右、取消在左）、发送文案 `إرسال`；测试后已恢复中文；15 语言资源守卫（单测）保持通过。

**v6 变更（已实施）**：选图后先进入 §10 预览；点击「发送」才执行 §9.4/§9.5 的处理与上行。§9.11 原先的「picker → 立即 `SendGalleryPhoto`」已替换为「picker → 预览 → 发送」；`GalleryPhotoSender` 本身不变（见 §10.11）。当前全量单测为 **934**（§9.11 表内 929 为 v5.4 当时结果；v6.3 时为 932）。**v7.1 变更（已实施）**：`GalleryPhotoSender` 的 `camera_facing` 从 `"screen"` 改为 `"gallery"`；协议/生成物/SDK 同步已完成，服务端已部署且真机 gallery E2E 通过（§11.10）。

---

## 10. 图库选图预览与发送确认（v6 新增 2026-10-17；v6.1 review 定稿；v6.2 系统返回=左返回；v6.3 已实施；v6.4 返回/按钮修正）

> **状态：已实施（2026-10-17），真机 E2E 通过**。流程为「picker → 全屏预览 → 点击发送才处理/上行 → 回主屏幕」。**UI 采用 B 方案（黑底 + 底部悬浮灰底白字发送）；标题栏左「返回」/系统返回重新打开 gallery 继续选择；picker 内系统返回/取消 = 关闭预览回主屏幕；右「取消」直接回主屏幕；中间不显示提示文字**。其余按定稿（§10.9）。§9.4/§9.5 的图片处理与 `GalleryPhotoSender` 不变。实施记录见 §10.11，UI mock 见 §10.10。

### 10.1 需求与范围

1. 从手机图库选中一张照片后，进入一个**独立的全屏 view**，显示所选图片。
2. 支持放大/缩小（fit 1x .. 5x；放大后可拖动平移）。
3. 底部一个「发送」按钮。
4. **只有点击「发送」才开始处理图片，然后发送给 server**；发送后回到主屏幕。
5. **右侧「取消」** = 关闭预览回主屏幕，不处理、不上行；**左侧「返回」/系统返回** = **先关闭预览**、再重新打开 gallery 继续选择（不发送）。**在 picker 内按系统返回/取消 = 停在主屏幕**（不会回旧预览）。

**范围内**：App UI（预览覆盖层、缩放手势、发送/取消接线）、UiState/事件、i18n、测试；`GalleryPhotoSender` 仅在「发送」时被调用。

**范围外**：裁剪/编辑/滤镜、多选、上传协议或服务端改动、预览阶段的图片压缩（预览只做显示解码；最终 1920/q70 仍由 §9.4 的 sender 处理）。

### 10.2 现状取证（代码事实）

| 事实 | 证据 |
|---|---|
| 当前 v5.4 流程：picker 回调直接发 `SendGalleryPhoto(uri)`，VM 立即 `sendGalleryPhoto` → sender 编码/上行；没有预览 | `ConversationScreen.kt` 136-143；`ConversationViewModel.kt` 435、862-877 |
| 全屏覆盖层先例：`AvatarCropScreen` 由 `ConversationScreen` 在 `showAvatarCrop && avatarCropSourceUri != null` 时 early-return 渲染；不是导航路由/新 Activity | `ConversationScreen.kt` 335-343；`ConversationUiState.kt` 43-44；VM 674、478-479 |
| 单图全屏查看器骨架：`HotSlotFrameViewer`（无 pager）——黑底、`BackHandler`、Coil `AsyncImage`、`onSuccess` 取 intrinsic、`ImageViewerMath` 1x–5x、捏合 + 放大平移、单击退出 | `HotSlotFrameViewer.kt` 65-110、207-266 |
| 缩放/平移纯函数：`ImageViewerMath`（MIN=1、MAX=5、fitSize、clampScale、offsetAfterZoom、clampOffset），已有 JVM 测试 | `ImageViewerOverlay.kt` 277-315；`ImageViewerMathTest.kt` |
| Coil 2.6.0 已依赖；MyLife 查看器使用 `AsyncImage(model=Url)`，`onSuccess` 读 drawable intrinsic 尺寸；Coil 负责 EXIF 旋正与采样 | `build.gradle.kts` 118-120；`FrameViewerOverlay.kt` 321-330；`HotSlotFrameViewer.kt` 262-270 |
| 确认页样式先例：顶部 `TextButton(common_cancel)` TextSecondary；底部全宽 48dp、12dp 圆角的 `Button(containerColor=BrandIndigo)` | `AvatarCropScreen.kt` 143-150、221-268 |
| 文案：`common_cancel`、`conv_confirm`、`chat_ai_image` 已全量；没有「发送」/「预览失败」键；15 目录 key 齐平由守卫强制 | `values*/strings.xml`；`I18nResourceFilesTest.kt` 61-84 |
| 预览 Uri 的状态保存先例：头像裁剪把源 Uri 放 UiState（旋转/重组不丢） | `ConversationUiState.avatarCropSourceUri`；VM 674 |
| 当前会话页无导航路由参数需要变更；`ConversationScreen` 即宿主 | 本文件 §9.2.1 的 UI 结构 |

### 10.3 交互流程

```
底部工具栏「+」 → 系统 Photo Picker（单图 ImageOnly） → Uri
  → OpenGalleryPreview(uri)                       [UiState.galleryPreviewUri]
  → ConversationScreen early-return → GalleryPhotoPreviewScreen
       ├─ 左「返回」/ 系统返回 → DismissGalleryPreview（关闭预览，主屏为底）
       │       └─ 重新启动 Photo Picker：
       │               ├─ 选中新图 → OpenGalleryPreview(newUri)（新预览，缩放重置）
       │               └─ picker 取消 / picker 内系统返回 → 停主屏幕
       ├─ 右「取消」→ DismissGalleryPreview → 主屏幕（不处理、不上行）
       └─ 发送（预览图加载成功后可用）→ SendGalleryPhoto(uri)
             → 清空 galleryPreviewUri（同步）→ 主屏幕
             → GalleryPhotoSender（§9.4/§9.5）：1920/q70 + EXIF → pushVideoJpeg
```

- **处理边界**：预览阶段只用 Coil 做显示解码（不写临时文件、不做 1920/q70、不调 SDK）；点击「发送」后才启动 `GalleryPhotoSender`。
- **左返回 / 系统返回 = 继续选择**：**先关闭当前预览（回主屏）**，再重新拉起同一个单图 Photo Picker；选新图则打开新预览（缩放/加载态全新）；**picker 取消或 picker 内系统返回 → 停在主屏幕**（不会闪回旧预览）。重新打开的是新的系统 picker 会话，**无法恢复上次的滚动/选中态**（系统能力限制，以 picker 自己的上次位置为准）。
- **右取消 = 回主屏幕**：清空预览；不处理、不上行；Photo Picker 的 Uri 授权不持久化。
- **发送后回主屏幕**：发送即同步清空预览状态并回到会话页；sender 在后台继续，失败走既有 `cap_bubble_failed` 错误横幅（§9.11），不会把用户困在预览页。
- 发送成功后 AI 立刻描述并回图（v5.4 已验证的 chat 主链路），只是触发点从 picker 回调后移到「发送」。

### 10.4 UI/UX 设计

#### 10.4.1 布局（B 定稿）

- **全屏覆盖层**（early-return，非导航路由/新 Activity），**黑底**（对齐 `ImageViewerOverlay` / `HotSlotFrameViewer`）。
- 顶部标题栏：
  - 左：`IconButton` 返回箭头（`Icons.AutoMirrored.Filled.ArrowBack`，`common_back`，`TextPrimary`）→ **重新打开 gallery 继续选择**（不关闭当前预览）。
  - 右：`TextButton("取消")`（`common_cancel`）→ **直接回主屏幕**（清空预览、不处理不上行）。
  - **中间不显示任何提示/标题文字**（原「双指缩放」提示已按 review 移除）。
- 中部：`weight(1f)` 图片区，`clipToBounds()`（放大后不覆盖顶部/底部按钮），Coil `AsyncImage(ContentScale.Fit)` + 缩放/平移；初始完整 fit。
- 底部：**悬浮 pill「发送」灰底白字**（居中、`SurfaceVariant` 灰底、`TextPrimary` 白字、高约 44–48dp、全圆角；禁用态 50% 灰底 + `TextSecondary`），叠在图片区底部；标签新增 `conv_gallery_send`。
- 发送按钮**仅在预览图 `onSuccess` 后可点**；加载中/失败时禁用（失败显示占位/错误，见 §10.4.3）。
- 系统返回：`BackHandler { onBackToPicker() }` → **等价左侧「返回」**：先关闭预览（主屏为底），再打开 gallery 继续选择（P11 定稿）；picker 内系统返回/取消 → 停在主屏幕（P12）。
- 状态栏/导航栏沿用现有全屏覆盖层（不做 edge-to-edge 新处理）。

#### 10.4.2 缩放/平移

- 复用 `ImageViewerMath`：`MIN_SCALE=1f`（fit）、`MAX_SCALE=5f`、`fitSize`、`clampScale`、`offsetAfterZoom`、`clampOffset`（已有 JVM 测试）。
- 手势：捏合缩放（锚点）+ 放大态单指拖动；fit 态单指不产生变换。实现推荐 `detectTransformGestures`（本页没有 Pager 需要让位；fit 态单指即使被消费也无视觉副作用），或复制 `HotSlotFrameViewer` 的自定义检测器（fit 态不消费单指）——P10 决策。
- **单击不退出**（与 ImageViewer 的单击退出不同）：本页有明确的「取消」/「发送」，避免点图误退；`BackHandler` 负责返回。
- 缩放入口只有捏合（与现有查看器一致），不新增 +/− 按钮。
- 不引入双击缩放（现有查看器没有；可选 P6）。
- **缩放语义**：缩放/平移仅用于查看，不改变上行内容——发送的是**完整照片**（由 §9.4 规范化为长边 ≤1920/q70），不是当前视口截图/裁剪。
- **实现策略**：新建预览 composable，拷贝 `HotSlotFrameViewer` 的 Coil + `ImageViewerMath` 骨架（项目已有拷贝式复用先例：`FrameViewerOverlay` 拷贝 conversation 骨架）；不改动既有查看器。

#### 10.4.3 图片加载与失败态

- 用 Coil `AsyncImage(model = uri)`：EXIF 自动旋正、按 view 采样解码，不在主线程解全尺寸原图。
- `onSuccess` 记录 intrinsic 尺寸（供 `ImageViewerMath.fitSize/clampOffset`）并启用「发送」。
- `onLoading`：显示占位/进度，发送禁用。
- `onError`：显示居中错误文案（新增 `conv_gallery_preview_failed`），发送禁用；用户可右取消返回。预览失败不自动发送/不自动关闭。
- **左返回换图时重置加载态**（`LaunchedEffect(uri)` / `key(uri)`）：新图 `onSuccess` 前「发送」保持禁用，避免用上一张图的 `loaded=true` 误启用。
- Coil 默认按视图尺寸采样；5x 放大为数字放大（与现有 `ImageViewerOverlay`/`HotSlotFrameViewer` 一致），不做原图全尺寸解码。
- 预览的显示解码与 sender 的最终编码相互独立；sender 仍按 §9.4 重新解码/缩放/EXIF/q70。

#### 10.4.4 视觉候选与 mock

- **B（定稿，2026-10-17 review；v6.4 按钮灰底白字）**：黑底 + 顶部左返回/右取消 + 底部悬浮 pill「发送」（灰底白字）；与 `ImageViewerOverlay`/`HotSlotFrameViewer` 的黑底风格一致；pill 会覆盖图片底部一小块（接受）。
- A（未采纳）：`Background` 全屏 + 顶部取消 + 底部全宽「发送」；虽然与头像裁剪确认页同型，但 review 选择 B。
- 本地渲染预览（不入库）：`/tmp/gallery_preview_mock.png`（HTML 源 `/tmp/gallery_preview.html`），B 为定稿；图片区为示意渐变，非真实照片。
- RTL：左返回在阅读方向起始侧；右取消在结束侧；底部 pill 居中；文案走 15 语言资源。

### 10.5 状态与接线

- **UiState**：新增 `galleryPreviewUri: Uri?`（default null）。与 `avatarCropSourceUri` 同级的页面态；旋转/重组不丢。
- **Events**：
  - `OpenGalleryPreview(uri)`：picker 回调 → 展示预览。
  - `DismissGalleryPreview`：右侧「取消」，或**从预览打开的 picker 返回 null（picker 内系统返回/取消）** → 清空预览、回主屏幕；预览内的左返回/系统返回走 `onBackToPicker`，不直接调用本事件。
  - `SendGalleryPhoto(uri)`：既有事件；仅在预览「发送」时调用。VM 分支先同步清空 `galleryPreviewUri`，再调用既有 `sendGalleryPhoto(uri)`（防连点/确保回主屏幕）。
- **Screen**：
  - `photoGalleryLauncher` 回调从 `SendGalleryPhoto(uri)` 改为：`uri != null → OpenGalleryPreview(uri)`；`uri == null → DismissGalleryPreview`（兜底：从「+」初次打开时预览本为空，Dismiss 为 no-op；从预览打开时预览已在左返回时关闭）。
  - 在 `ConversationScreen` 的 early-return 链中加入（放在 `AvatarCropScreen` 分支同区）：
    ```kotlin
    val previewUri = uiState.galleryPreviewUri
    if (previewUri != null) {
        GalleryPhotoPreviewScreen(
            uri = previewUri,
            onSend = { viewModel.onEvent(ConversationEvent.SendGalleryPhoto(previewUri)) },
            onCancel = { viewModel.onEvent(ConversationEvent.DismissGalleryPreview) },
            // 左返回/系统返回：先关闭预览（主屏为底），再拉起同一个单图 picker
            onBackToPicker = {
                viewModel.onEvent(ConversationEvent.DismissGalleryPreview)
                photoGalleryLauncher.launch(
                    PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                )
            },
        )
        return
    }
    ```
  - 左返回/系统返回在 `onBackToPicker` 中**先 `DismissGalleryPreview`（关闭预览，主屏为底）再 launch picker**；picker 选中新图 → `OpenGalleryPreview(newUri)`；picker 取消/系统返回 → 停在主屏。
  - 预览按 `uri` key/`LaunchedEffect(uri)` 重置缩放、平移与加载状态（换图后回到 fit；新图 `onSuccess` 前发送禁用）。
  - **预览内系统返回（`BackHandler`）调用 `onBackToPicker`**，与左返回一致：先 `DismissGalleryPreview` 关闭预览，再打开 gallery（P11 定稿）；右「取消」调用 `DismissGalleryPreview` 回主屏幕；picker 内系统返回/取消由 picker 自身处理，取消后停在主屏（P12）。
  - 头像裁剪与图库预览状态互斥；先判哪个不影响（两者不会同时为真）。
- **VM**：
  - `OpenGalleryPreview` → `copy(galleryPreviewUri = uri)`。
  - `DismissGalleryPreview` → `copy(galleryPreviewUri = null)`。
  - `SendGalleryPhoto` → `copy(galleryPreviewUri = null)`，然后既有 `sendGalleryPhoto(uri)`（sender 调用不变）。
- **不加**导航路由/Manifest/Activity；预览是 `ConversationScreen` 的全屏 early-return 覆盖层。
- 进程死亡：UiState 丢失 → 无预览、无崩溃；用户重新选图（Photo Picker 授权不跨进程持久化）。
- 旋转：Uri 保留；Coil 缓存/重载；缩放状态是页面局部 state，旋转后回到 fit（可接受，见 D6/边界）。

### 10.6 边界矩阵

| 场景 | 预期行为 |
|---|---|
| Picker 取消 | 无预览、无事件 |
| 预览图加载中 | 占位/进度；「发送」禁用 |
| 预览图加载失败（损坏/不支持/权限失效） | 居中错误 + 「发送」禁用；右取消返回；不自动关闭 |
| 左「返回」/ 系统返回 → picker 选中新图 | 先 `DismissGalleryPreview` 关闭预览（主屏为底），打开 picker；选中后 `OpenGalleryPreview(newUri)` 打开新预览；缩放/平移重置；新图 `onSuccess` 前发送禁用 |
| 左「返回」/ 系统返回 → picker 取消或 picker 内系统返回 | 预览已在打开 picker 前关闭；picker 取消后**停在主屏幕**；不处理、不上行 |
| 右「取消」 | 关闭预览回主屏幕；不处理、不上行；无临时文件 |
| 点「发送」 | 同步清空预览 → 回主屏幕；sender 后台处理；失败走错误横幅 |
| 发送连点 | 第一次同步清空后预览已卸载；`SendGalleryPhoto` 不再被第二次触发 |
| 旋转 | UiState 保留 Uri；预览重建；缩放回 fit（可接受） |
| 进程死亡 | 预览丢失；无崩溃；重新选图 |
| 超大图 | Coil 采样显示；最终 sender 仍长边 1920/q70；内存有界 |
| EXIF 竖拍 | Coil 预览旋正；sender 再按 EXIF 处理后上行 |
| 摄像头开启 | 「+」隐藏（D9），无法进入预览；预览打开期间 UI 无相机入口 |
| 非 chat 模式（omni/translate/lecture/transcribe） | 预览照常；发送后的回合行为由服务端门控/omni 决定（D5 常驻、无回合为 accepted） |
| screen recording（chat） | 预览照常；发送触发 T1；录制继续 |
| RTL / 15 语言 | 左返回在 start 侧、右取消在 end 侧；底部 pill 居中；新文案 15 目录齐平 |
| 发送时源 Uri 失效（预览后文件被删） | 预览可能仍显示缓存；sender `FAILED` → 回主屏幕错误横幅 |
| 相机在预览期间被外部打开 | 预览 modal 无入口；点发送时 `isStillAllowed=false` → `REJECTED` 静默；预览关闭（极端边缘） |

### 10.7 测试计划

#### 10.7.1 JVM / Compose

- `ImageViewerMathTest` 既有 1x/5x/fit/clamp 用例直接覆盖预览缩放数学（无需新增）。
- `GalleryPhotoPreviewScreen`（androidTest Compose）：加载中「发送」禁用；`onSuccess` 后可用；点发送触发 onSend 一次；右取消触发 onCancel；左返回触发 onBackToPicker（不触发 onCancel）；**系统返回触发 onBackToPicker（不触发 onCancel）**；错误态发送禁用；缩放后按钮仍可点（按钮不在手势层内）；`uri` 变化重置缩放/平移与加载态（新图 `onSuccess` 前发送禁用）。Coil 异步加载在测试中注入 fake ImageLoader/本地资源，稳定控制 `onSuccess`/`onError` 时序。
- VM：`OpenGalleryPreview` 设置状态；`Dismiss` 清空且**不调用** sender；`SendGalleryPhoto` 清空预览并调用 sender 一次（mock 验证）；FAILED 错误横幅（既有用例可复用）；picker 回调不再直接调用 sender（改 `OpenGalleryPreview`）。
- 屏幕级：early-return 显隐（预览 Uri 非空 → 主工具栏/消息不渲染；清空 → 恢复）。

#### 10.7.2 真机 / 端到端

1. 「+」→ picker → 预览显示所选图（EXIF 竖拍方向正确）；捏合 1x–5x、放大拖动、边界夹取；标题栏无中间提示文字。
2. 左「返回」/ 系统返回 → 重新打开 gallery：选新图 → 预览替换、缩放重置；picker 取消/系统返回 → **关闭预览回主屏幕**；两种路径 app log 均**无** `GalleryPhoto` 推送。
3. 右「取消」→ 回主屏幕，app log **无** `GalleryPhoto` 推送。
4. 发送 → 预览关闭回主屏幕；app log `photo pushed WxH`；AI 立刻描述并回图（复用 v5.4 chat 主链路）。
5. 大图（>1920）预览 + 发送 → 服务端收到长边 1920；预览不卡顿/OOM。
6. 旋转（预览/加载中）→ Uri 保留、无崩溃；缩放回 fit。
7. omni / translate / lecture / transcribe / screen recording：预览照常，发送后的行为按 §9.5.3（accepted）。
8. RTL（ar）：左返回在 start 侧、右取消在 end 侧；15 语言资源守卫通过。
9. 预览加载失败（可临时用损坏图片/撤销权限模拟）→ 错误态 + 发送禁用 + 可右取消。

**回归**：v5.4 的 `GalleryPhotoSenderTest` 12 例不变；`BottomToolbarTest` 21 例不变；全量单测；§9.11 真机 E2E 在预览流程下重跑。

### 10.8 实施影响清单

| 层 | 文件 | 改动 |
|---|---|---|
| App State | `ui/screens/conversation/ConversationUiState.kt` | 新增 `galleryPreviewUri: Uri? = null` |
| App 事件 | `ui/screens/conversation/ConversationEvent.kt` | `OpenGalleryPreview(uri)` / `DismissGalleryPreview`；`SendGalleryPhoto` 复用 |
| App VM | `ui/screens/conversation/ConversationViewModel.kt` | Open/Dismiss 分支；Send 分支先清预览再调既有 `sendGalleryPhoto` |
| App Screen | `ui/screens/conversation/ConversationScreen.kt` | picker 回调改 `OpenGalleryPreview`；early-return 渲染预览；onSend/onCancel/onBackToPicker 接线（onBackToPicker 复用同一 `photoGalleryLauncher`） |
| App UI | **新增** `ui/screens/conversation/GalleryPhotoPreviewScreen.kt` | 黑底全屏、顶部左返回/右取消（无中间提示）、Coil `AsyncImage`、`ImageViewerMath` 缩放/平移、底部悬浮「发送」、BackHandler、加载/错误态 |
| i18n | 15 个 `values*/strings.xml` | 新增 `conv_gallery_send`（发送 / Send）、`conv_gallery_preview_image`（图片预览 / Photo preview，contentDescription）、`conv_gallery_preview_failed`（无法加载图片 / Can't load photo）；顶部返回复用 `common_back`、取消复用 `common_cancel`；**无中间提示文案**；守卫强制 15 目录齐平；其余 13 语言在实现轮按 `docs/android_multilingual.md` 流程补齐 |
| App 测试 | `GalleryPhotoPreviewScreenTest`（androidTest）/ VM 测试 | §10.7 |
| SDK / 服务端 | 无 | sender/协议零改动；仅调用时机后移 |
| 版本 | `LivaChatApp/build.gradle.kts` | 按惯例仅在 git 提交时递增 versionCode |

**对 v5.4 的影响**：`ConversationScreen.photoGalleryLauncher` 的回调改为 `OpenGalleryPreview`；`SendGalleryPhoto` 只从预览的「发送」触发；`GalleryPhotoSender` 与其单测不变。§9.11 的即时发送真机 E2E 需按新流程重跑。

### 10.9 决策定稿（2026-10-17 review）

| # | 决策点 | 选项 | 定稿 |
|---|---|---|---|
| P1 | 预览宿主 | A：`ConversationScreen` 全屏 early-return 覆盖层（对齐 `AvatarCropScreen`，无路由/Manifest）；B：新导航路由/Activity | **A**（改动最小、状态在 VM、返回即主屏幕） |
| P2 | 视觉 | A：`Background` + 顶部取消 + 底部全宽「发送」；B：黑底 + 悬浮 pill | **B（review 定稿）**：黑底 + 底部悬浮「发送」；标题栏左「返回」→ gallery 继续选、右「取消」→ 主屏幕；**无中间提示文字**；A 未采纳 |
| P3 | 图片加载 | Coil `AsyncImage`（EXIF/采样，MyLife 查看器先例）；自绘 BitmapFactory + `applyExifOrientation` | **Coil**（避免主线程解大图；方向自动） |
| P4 | 发送可用时机 | 预览图 `onSuccess` 后启用；立即启用 | **onSuccess 后**（用户确认看到的是可显示图片） |
| P5 | 预览加载失败 | 占位/错误 + 发送禁用 + 取消（新增文案）；自动回主屏幕报错；复用 `cap_bubble_failed` | **错误态 + 禁用发送**（不把用户甩回主屏幕） |
| P6 | 双击缩放 | 不做（现有查看器无）；做 1x/2x 切换 | **不做**（需求只要求 zoom in/out） |
| P7 | 文案 | 新增 3 个语义键（send/image/failed）+ 15 语言；复用 `conv_confirm`/`chat_ai_image`/`cap_bubble_failed` | **新增语义键**（用户明确要「发送」；图片预览不是 AI 图）；顶部返回/取消复用 `common_back`/`common_cancel`；无中间提示文案 |
| P8 | 单击退出 | 不退出（显式取消/返回）；沿用查看器单击退出 | **不退出**（防误触丢图） |
| P9 | 发送后 | 立即关闭预览、后台处理（失败走主屏错误横幅）；留在预览等待处理 | **立即关闭**（需求「会到主屏幕」；编码百毫秒级） |
| P10 | 缩放手势实现 | A：`detectTransformGestures` + `ImageViewerMath`；B：复制 `HotSlotFrameViewer` 自定义检测器 | **A**（无 Pager 让位需求，代码更短；数学复用） |
| P11 | 预览内系统返回键/手势 | A：等价右侧「取消」→ 主屏幕；B：等价左侧「返回」→ 重新打开 gallery | **B（2026-10-17 review 定稿；v6.4 先关闭预览）**：与左返回一致，**先关闭预览（主屏为底）**，再打开 gallery 继续选择 |
| P12 | picker 内系统返回/取消 | A：关闭预览回主屏幕；B：回到旧预览 | **A（2026-10-17 用户修正）**：picker 返回 null → `DismissGalleryPreview`；右「取消」仍是预览内回主屏幕的入口 |

**review 定稿（2026-10-17）**：P1=A、P2=B（左返回/右取消、无中间提示）、P3=Coil、P4=onSuccess、P5=错误态、P6=无双击、P7=新语义键、P8=不单击退出、P9=发送后立即回主屏、P10=`detectTransformGestures`、**P11=预览内系统返回=左返回→gallery**、**P12=picker 内系统返回/取消→主屏**。已按本决策实施（见 §10.11）；真机 E2E 通过。

### 10.10 UI mock

- `/tmp/gallery_preview_mock.png`（HTML 源 `/tmp/gallery_preview.html`）：**B 定稿** = 黑底 + 顶部左返回/右取消 + 底部悬浮「发送」（灰底白字）、无中间提示；A 未采纳。
- 图片区为示意渐变，不代表真实照片；尺寸按 360dp 宽、1x CSS px = 1dp 渲染。

### 10.11 实施记录（2026-10-17）

按 v6.2 定稿实施；v6.4 修正 picker 返回语义与发送按钮配色。`GalleryPhotoSender` / 协议 / 服务端零改动；只改了 picker 后的触发流程与新增预览页。

| 层 | 实际改动 |
|---|---|
| State / Event | `ConversationUiState.galleryPreviewUri: Uri?`；`OpenGalleryPreview(uri)` / `DismissGalleryPreview`；`SendGalleryPhoto` 保留为预览「发送」事件 |
| VM | `OpenGalleryPreview` 写入预览 Uri；`DismissGalleryPreview` 清空；`SendGalleryPhoto` 先同步清空预览，再调用既有 `sendGalleryPhoto(uri)`（失败仍走 `cap_bubble_failed`） |
| Screen | picker 回调改为 `uri != null → OpenGalleryPreview(uri)`、`uri == null → DismissGalleryPreview`（v6.4 兜底）；在头像裁剪同区 early-return 渲染预览；`onSend` / `onCancel` / `onBackToPicker`（**左返回先 `DismissGalleryPreview` 关闭预览，再复用同一 `photoGalleryLauncher`**） |
| 预览页 | 新增 `GalleryPhotoPreviewScreen`：黑底；顶部左返回（`common_back`）/右取消（`common_cancel`）、无中间提示；Coil `AsyncImage`；`ImageViewerMath` 1x–5x + 拖动；底部悬浮「发送」**灰底白字**（`SurfaceVariant` + `TextPrimary`；禁用态灰暗，加载成功后启用）；加载/错误态；`BackHandler`=左返回（§10.9 P11）；Uri 变化重置缩放/平移/加载态 |
| i18n | 15 个 `values*/strings.xml` 新增 `conv_gallery_send`、`conv_gallery_preview_image`、`conv_gallery_preview_failed`；返回/取消复用既有键；无中间提示文案 |
| 测试 | 新增 `GalleryPhotoPreviewScreenTest` **5 例**（发送/Cancel/左返回、系统返回→gallery、加载失败禁用发送、Uri 变化重置、**pinch 缩放渲染差异 + 发送仍可用**）；VM 新增 3 例（Open 存 Uri / Dismiss 不发送 / Send 清预览且只调 sender 一次）；既有 v5.4 sender/VM 用例保留 |
| 版本 | **versionCode 未动**（项目惯例：仅在 git 提交时递增；本轮未提交） |

**验证证据**

- `:LivaChatApp:testDebugUnitTest` 全量：**94 suites / 934 tests / 0 failures / 0 errors / 4 skipped**（v7.1 复核新增 2 例后；v6.3 当时为 932）。
- 真机 `GalleryPhotoPreviewScreenTest`：**OK (5 tests)**（新增 pinch 用例）；`BottomToolbarTest`：**OK (21 tests)**（LIO-AN00 / Android 12）。
- `:LivaChatApp:assembleDebug`、`:LivaChatApp:compileDebugAndroidTestKotlin` 通过；主 app + test APK 已用 installDebug/installDebugAndroidTest 安装。
- **真机 E2E（2026-10-17）**：
  1. 「+」→ DocumentsUI → 选图 → 预览页：截图 `/tmp/livachat_preview_v64.png`（黑底、左「返回」、右「取消」、底部**灰底白字**「发送」、无中间提示、图片 `content-desc="图片预览"`）。
  2. 左「返回」/系统返回 → **先关闭预览（主屏为底）**，再打开 DocumentsUI；**picker 内系统返回/取消 → 停在主屏幕**（不会闪回旧预览，工具栏「+」可见）；右「取消」→ 主屏幕。
  3. 「发送」→ 预览关闭回 MainActivity；app log `D/LivaChat-GalleryPhoto: photo pushed 805x799 bytes=69672 omni=false`；AI 回复直接描述所选图（截图 `/tmp/livachat_preview_sent2.png`）。
- **边界 E2E（2026-10-17，v7.1 构建）已完成**：大图/EXIF 预览与 1440×1920 上行、双指缩放渲染差异、omni/translate/lecture/transcribe、screen recording（含录制中 gallery）、取消/左返回/连续发送、RTL；记录见 §11.10。旋转不适用（App 锁定竖屏）。

---

## 11. `camera_facing` 新增 `gallery`（v7 新增 2026-10-17；v7.1 已实施）

> **状态：v7.1 已实施并部署；真机 gallery E2E 通过（2026-10-17，§11.10）**。协议新增 `"gallery"` 并在图库照片路径使用；服务端只做校验/透传，无行为分支；发布顺序为**服务端先行**（本次已满足）。设计见 §11.3，决策见 §11.8，实施记录见 §11.10，锚点见附录 E。

### 11.1 需求与范围

1. `video` 消息的 `camera_facing` 增加第四个值 **`"gallery"`**，语义 = 用户从系统图库选择的照片（非实时摄像头、非屏幕抓取）。
2. 图库照片上行（§9/§10）的 `camera_facing` 从 `"screen"` 改为 `"gallery"`。
3. 相机 `front`/`back`、屏幕抓取 `screen` 保持原语义与行为。
4. 以 schema 为单一事实源重生成 Python/Kotlin 模型；服务端先行部署。

**范围内**：`packages/api-schema/common.json` 枚举、双端生成物、SDK 映射、App `GalleryPhotoSender`、服务端协议接受/回显、测试与协议文档。

**范围外**：服务端按 `gallery` 做行为分支（当前无消费方）；历史已发送的 `screen` 元数据回填；UI 变化。

### 11.2 现状取证（2026-10-17 v7.0 设计时快照）

> 下表为设计时（v6.4 代码）事实；v7.1 实施后的当前值见 §11.10（协议四值、App 发 gallery）。

| 事实 | 证据 |
|---|---|
| `CameraFacing` 枚举当前三值 `front` / `back` / `screen`；描述 "Which camera captured the frame. 'screen' = screen capture (not a camera)." | `packages/api-schema/common.json` `$defs.CameraFacing` |
| `c2s/video.json` 的 `payload.camera_facing` 与 `s2c/ai-text.json` 的 `image.camera_facing` 均 `$ref` 同一枚举 | 两个 schema 文件 |
| Python 生成物 `class CameraFacing(StrEnum)`：FRONT/BACK/SCREEN | `packages/sdk-python/.../generated/protocol_models.py` 96-101 |
| Kotlin 生成物 `enum class CameraFacing`：FRONT/BACK/SCREEN | `packages/sdk-kotlin/.../generated/ProtocolModels.kt` 53-57 |
| SDK 映射：`"back"→BACK`、`"screen"→SCREEN`、其他→FRONT（未知兜底） | `LivaChatSDK.kt` 368-371；`buildVideoPayload` 379-400 |
| App 图库照片当前发送 `CAMERA_FACING = "screen"` | `GalleryPhotoSender.kt` 119（v6.4） |
| 屏幕抓取仍发送 `"screen"`（不应改动） | `ScreenCaptureController.kt` push 调用点 639-644 |
| 服务端只把 `camera_facing` 入 K=1 buffer / 回显 final `ai_text`；无 prompt/分支/策略使用 | `orchestrator.py` 2053、1595、2261、2301；§9.10 F3 检索结论 |
| 服务端协议校验来自生成物 pydantic 枚举；未知枚举按非法帧处理（网关 error 1006、丢帧；连续 10 个非法帧才断连，音频帧会复位计数） | `services/gateway/connection.py` 218-227；`gateway/codec.py` 36、144-159；`fd_pipeline_video_process.md` §8.2.4 |
| 生成链路：schema → `scripts/generate_python_api.py` → `make generate-android-api`；CI 用 `make check-android-api-generated` 校验；生成物禁止手改 | `Makefile` 4-9；`scripts/generate_python_api.py` |
| 协议文档：c2s `camera_facing` 表列 front/back/screen；s2c ai-text `image.camera_facing` 表仍只列 front/back（已过时） | `docs/ws-protocol-design.md` 274、496 |
| SDK 测试 `mapCameraFacing_threeValues` 与 App `GalleryPhotoSenderTest` 断言 `"screen"` | `VideoTurnPayloadTest.kt` 16-25；`GalleryPhotoSenderTest.kt` |
| `camera_facing` 在服务端无 prompt/UI 消费；recording sink 只落 JPEG，不落 facing | §9.10 F3；`recording/session.py::_write_item_images` |

### 11.3 设计

#### 11.3.1 协议 schema（单一事实源）

`packages/api-schema/common.json`：

```json
"CameraFacing": {
  "type": "string",
  "enum": ["front", "back", "screen", "gallery"],
  "description": "Which camera captured the frame. 'screen' = screen capture, 'gallery' = user-selected gallery photo (both are not a live camera)."
}
```

- `c2s/video.json` / `s2c/ai-text.json` 通过 `$ref` 自动获得新值，无需新增字段。
- 不新增 `required`/`additionalProperties` 变化；仅枚举扩展。
- 重生成：
  - `python3 scripts/generate_python_api.py --schema packages/api-schema --out packages/sdk-python/src/livachat_protocol/generated`
  - `make generate-android-api`
- **CI 校验语义（易误用）**：`make check-android-api-generated` 依赖 `generate-android-api` 后检查 `git status --porcelain`；实现分支在生成物**尚未提交**时必然显示 modified，不能当作“生成即正确”的即时断言。应先提交生成物，再由 CI/提交后运行；Python `--check` 直接比对磁盘内容，可在提交前运行。
- 生成物（不手改）：Python `CameraFacing.GALLERY = 'gallery'`；Kotlin `@SerialName("gallery") GALLERY`。`s2c/ai-text` 的 `ServerAiTextImage.cameraFacing` 同枚举自动接受 gallery。

#### 11.3.2 SDK

`LivaChatSDK.kt::mapCameraFacing` 增加分支：

```kotlin
internal fun mapCameraFacing(facing: String): CameraFacing = when (facing) {
    "back" -> CameraFacing.BACK
    "screen" -> CameraFacing.SCREEN
    "gallery" -> CameraFacing.GALLERY
    else -> CameraFacing.FRONT   // 未知兜底保持旧行为
}
```

- `pushVideoJpeg`/`pushBitmap`/`buildVideoPayload` 签名与直通逻辑不变。
- 未知值兜底 FRONT 保持不变（旧行为/兼容）。
- `LivaChatAppMock` 不发送 gallery，零改动。

#### 11.3.3 App

- `GalleryPhotoSender.CAMERA_FACING`：`"screen"` → `"gallery"`（仅图库照片路径）。
- `ScreenCaptureController` 仍传 `"screen"`；`CameraController` 仍 `"front"`/`"back"`。
- 预览/发送流程（§10）不变；`GalleryPhotoSender` 的其余逻辑（1920/q70/EXIF/三态/守卫）不变。
- `camera_facing` 在客户端仅存入 `ChatRepository.imageCameraFacing`，不参与渲染（`ChatImageView` 只消费 orientation）。

#### 11.3.4 服务端

- 生成物重新生成后，pydantic 枚举接受 `gallery`；`_handle_video` 的存储（2053）/回显（2261/2301）逐字不变。
- 无任何行为分支：LLM prompt、avdetector、omni feed、recording tap 均不使用/不区分 `gallery`。
- final `ai_text.image.camera_facing` 回显 `"gallery"`；客户端只存不渲染。
- `camera_facing` 为 `StrEnum` 值，`ChatImageFrame.camera_facing` 是 `str`，服务端内部无需类型改动。

### 11.4 兼容性与发布顺序

| 组合 | 行为 |
|---|---|
| **旧服务端 + 新 App** | `gallery` 枚举被协议层拒绝：网关 error 1006，帧丢弃；连续 10 个非法帧才 4001 断连，音频帧复位计数 → 实际为**图库照片功能不可用**（相机/屏幕帧不受影响，均用旧枚举）。**必须先部署服务端** |
| 新服务端 + 旧 App | 旧 App 的图库照片仍发 `"screen"`；服务端接受并透传；final `ai_text` 回显 `"screen"`（旧客户端枚举支持），行为与现状一致 |
| 新服务端 + 新 App | 图库照片发 `"gallery"`；服务端接受/回显；final ai_text 带 gallery |
| 回滚（App） | App 回退到 `"screen"`；新服务端同时接受 screen/gallery，无脏状态 |
| 回滚（服务端） | 服务端回退后，新 App 的 gallery 帧被拒（同旧服务端+新 App）；需同时回滚 App 或暂不使用图库功能 |

- 发布顺序：**协议/服务端先行** → 验证服务端接受 `gallery` → 再发 Android App/SDK。
- mock server/工具（`tools/local-dev/livachat_mock_server` 等依赖生成物）随重生成自动跟进；CI `make check-android-api-generated` 保证生成物一致。
- 历史数据中的 `"screen"` 图库照片不回填（元数据仅存储/回显，无消费方；回填无收益）。
- **旧客户端 s2c 枚举边界（硬前提）**：旧 Android SDK 的 Kotlin `CameraFacing` 只有 front/back/screen，反序列化 `"gallery"` 会失败。新服务端对旧客户端会话的 `ai_text` 回显**严格来自该会话自己的帧**——旧 App 只会发 front/back/screen，因此不会收到 gallery。若未来服务端在旧客户端会话中注入/代发 gallery（例如跨会话共享帧、服务端代理轮次），旧客户端会丢失该 `ai_text`；届时需要客户端升级或服务端按 `clientVersion` 降级。

### 11.5 边界矩阵

| 场景 | 预期行为 |
|---|---|
| 图库照片发送 | `camera_facing="gallery"`；服务端接受/回显；AI 描述与回图不变 |
| 屏幕抓取 | 仍 `"screen"`；不受影响 |
| 相机前/后摄 | 仍 `"front"`/`"back"`；不受影响 |
| 未知/旧客户端值 | SDK 兜底 FRONT；服务端严格枚举校验 |
| Old server + new app | 帧被 1006 丢弃；会话不断（音频复位计数）；图库功能不可用 |
| New server + old app | 图库照片回显 `"screen"`；功能正常（旧客户端枚举支持） |
| 旧客户端会话收到 gallery ai_text | **不应发生**：回显只来自本会话帧，旧 App 只发 front/back/screen；若被注入，旧 Kotlin 枚举无 GALLERY，反序列化失败 |
| final ai_text 回带 | `camera_facing="gallery"`；客户端只存不渲染 |
| recording/avdetector/omni/prompt | 不使用/不区分 gallery；与 screen 等价 |
| 生成物漂移 | `make check-android-api-generated` / Python `--check` 失败；禁止手改生成物 |
| 协议文档 | c2s/ai-text 的 camera_facing 枚举表同步为四值 |

### 11.6 测试计划

**协议/生成物**
- `packages/sdk-python/tests/test_protocol_models.py`：`CameraFacing.GALLERY == "gallery"`；`video` payload 带 gallery 的序列化/反序列化往返；`ai-text` image 带 gallery 往返；非法值仍被拒绝。
- `python3 scripts/generate_python_api.py --check` 可直接在提交前运行并通过；`make check-android-api-generated` 在**生成物已提交后**/CI 中通过（该目标用 `git status` 比对，未提交工作区必然 modified）。生成物 diff 仅新增 gallery。

**SDK**
- `VideoTurnPayloadTest`：`mapCameraFacing("gallery") == CameraFacing.GALLERY`；四值映射 + 未知兜底 FRONT；`buildVideoPayload(..., cameraFacing="gallery")` 落线为 `"gallery"`。

**App**
- `GalleryPhotoSenderTest`：chat/omni 用例的 `cameraFacing` 断言从 `"screen"` 更新为 `"gallery"`（`pushVideoJpeg` mock 验证）。
- `ScreenCaptureController` 相关测试仍断言 `"screen"`；`CameraController` 仍 front/back。
- 真机：图库选图 → 发送 → 服务端收到 gallery；相机/屏幕抓取仍为原值（logcat `capture: facing=` / `tap capture pushed`）。

**服务端**
- 新增用例：`ClientVideoPayload(..., camera_facing=CameraFacing.GALLERY, text=...)`（生成物为 StrEnum，字符串 `"gallery"` 同样可被 pydantic 接受）→ ack `processed=True`、帧入 buffer、v3 文本回合正常、final ai_text 的 `camera_facing` 回显 gallery（`test_video_text_turn.py` 扩展或新用例）。
- 网关/codec：gallery 帧通过；未知值仍按非法帧（1006/丢帧）处理。
- 回归：services 全量；相机/屏幕/avdetector/omni 用例不变。

**端到端**
- ~~服务端先行部署 → App 图库照片日志/服务端日志出现 `camera_facing=gallery` → AI 描述并回图。~~ **已通过（2026-10-17，§11.10）。**
- 旧服务端 + 新 App 的负向验证（可选，确认 1006 丢帧不影响会话）。

### 11.7 实施影响清单

| 层 | 文件/命令 | 改动 |
|---|---|---|
| 协议 schema | `packages/api-schema/common.json` | CameraFacing 枚举 + `"gallery"` + 描述 |
| 生成物 | `packages/sdk-python/.../generated/*`、`packages/sdk-kotlin/.../generated/*` | 脚本重生成，禁止手改 |
| 生成/校验 | `scripts/generate_python_api.py`、`make generate-android-api`、`make check-android-api-generated` | 执行/CI 校验 |
| SDK Kotlin | `apps/android/LivaChatSDK/.../LivaChatSDK.kt` | `mapCameraFacing` 加 gallery 分支 + KDoc |
| App | `apps/android/LivaChatApp/.../media/GalleryPhotoSender.kt` | `CAMERA_FACING = "gallery"` |
| App 测试 | `GalleryPhotoSenderTest.kt` | 断言从 screen 改为 gallery |
| SDK 测试 | `VideoTurnPayloadTest.kt` | 四值映射 + gallery 落线 |
| 服务端 | 生成物重生成后无需业务代码改动 | 新增 gallery 接受/回显测试 |
| 服务端测试 | `services/tests/integration/orchestrator/test_video_text_turn.py` 等 | gallery 帧接受/回显用例 |
| 协议文档 | `docs/ws-protocol-design.md` | c2s L274 + s2c ai-text L496 枚举表同步四值 |
| 历史文档 | `docs/fd_pipeline_video_process.md` §8 | 注记 gallery 为第四值（历史设计文档，不改行为） |
| mock/工具 | 依赖生成物的 mock server | 随重生成自动跟进 |
| 版本 | `LivaChatApp/build.gradle.kts` | 按惯例仅在 git 提交时递增 versionCode |

**非目标**：服务端按 gallery 分支、历史 screen 回填、UI/文案变化。

### 11.8 决策点（review 定稿）

| # | 决策点 | 选项 | 推荐 |
|---|---|---|---|
| G1 | 新枚举值 | `"gallery"`（用户指定）；复用 `"screen"` | **`"gallery"`（用户指定）** |
| G2 | 未知 facing 兜底 | 保持 FRONT（旧行为）；改为报错 | **保持 FRONT**（兼容旧调用方） |
| G3 | s2c ai-text 回显 | 同一枚举 `$ref` 自动接受 gallery；单独映射 | **同一 `$ref`**（零额外字段） |
| G4 | 部署顺序 | 服务端先行；App 先行 | **服务端先行**（旧服务端拒未知枚举，见 §11.4） |
| G5 | 历史 screen 数据 | 不回填；回填为 gallery | **不回填**（无消费方，收益为零） |
| G6 | 协议文档 | 同步四值；只改 schema | **同步 `docs/ws-protocol-design.md` 两处表** |

**已按 G1–G6 实施（2026-10-17，v7.1；服务端已部署，真机 E2E 通过）。**

### 11.9 状态

- **v7.1 已实施并部署**：协议枚举四值（front/back/screen/gallery）；Python/Kotlin 生成物含 GALLERY；SDK `mapCameraFacing` 四值；App `GalleryPhotoSender` 发送 `camera_facing="gallery"`；屏幕抓取仍 `"screen"`、相机仍 front/back。
- **服务端部署已完成**：本机 `livachat-server`（uvicorn+watchfiles）于 17:35:57 重启 worker 加载含 gallery 枚举的生成物；真机 E2E 通过（§11.10）。旧服务端 1006 丢帧风险仅存在于“App 先于服务端”的发布顺序，本次未触发。
- UI 无变化，无 mock；versionCode 未动（未提交）。

### 11.10 实施记录（2026-10-17，v7.1）

| 层 | 实际改动 |
|---|---|
| 协议 schema | `packages/api-schema/common.json`：`CameraFacing` 枚举 + `"gallery"`，描述更新 |
| 生成物 | Python `CameraFacing.GALLERY='gallery'`；Kotlin `@SerialName("gallery") GALLERY`（脚本重生成，未手改） |
| SDK | `mapCameraFacing` 增加 `"gallery"→GALLERY`；未知仍兜底 FRONT |
| App | `GalleryPhotoSender.CAMERA_FACING = "gallery"`；`ScreenCaptureController` 仍 `"screen"`、相机仍 front/back |
| 服务端 | 生成物重生成后接受 gallery；无业务代码改动，final `ai_text` 原样回显 |
| 测试 | 协议新增 gallery 枚举/往返用例；服务端 `test_gallery_facing_accepted_and_echoed`；SDK 四值映射 + gallery 落线；App sender 断言改为 gallery |
| 文档 | `ws-protocol-design.md` c2s/s2c facing 表同步四值；`fd_pipeline_video_process.md` §8 增补注记 |

**验证证据**
- Python 协议测试：**6 passed**；`generate_python_api.py --check`：**up to date**。
- services 全量：**1623 passed / 1 skipped / 97 deselected**（含新增 gallery 用例）。
- LivaChatSDK：**186 tests / 0 failed**；LivaChatApp：**934 tests / 0 failed / 4 skipped**；`:LivaChatApp:assembleDebug` 通过。
- Kotlin `make check-android-api-generated` 未在未提交工作区运行（该目标用 `git status` 比对，提交后/CI 运行；见 §11.3.1 语义说明）；复核时直接运行 `scripts/generate_kotlin_api.py --check`：**up to date**。

**复核（2026-10-17，对抗式自查）**

- **修复 1（崩溃路径）**：`BitmapFactory.decodeStream` 解码大图分配的 `OutOfMemoryError` 属 `Error`，原 `catch (Exception)` 捕不到 → 直达协程/进程默认处理器崩溃，违背 §9.4「OOM 收敛为 `FAILED`」。已在 bounds 读取与像素解码两处补 `catch (OutOfMemoryError)`；新增 `send - pixel decode OOM fails without crash` 回归用例（Mock `ContentResolver`：第 1 次 bounds 读取返回真实字节，第 2 次像素解码抛 OOM → `FAILED` 且不 push）。App 全量单测 **934/0/4**。
- **补覆盖（EXIF 八朝向）**：§9.7.1 此前仅覆盖 90°；新增 `encode photo - all eight exif orientations map quadrants`——200×100 四象限纯色图（TL 红 / TR 绿 / BL 蓝 / BR 黄）逐朝向断言输出尺寸与四象限归属（JPEG 有损用最近参考色分类），覆盖 normal/flip-h/180/flip-v/transpose/90/transverse/270，全部通过；矩阵实现无 bug。
- **核对无问题**：Kotlin/Python 生成物 `--check` 均无漂移；服务端 `camera_facing` 仅入 buffer / avdetector 元组 / 回显 / recording 快照，无行为分支；App 侧 `ScreenCaptureController` 仍 `"screen"`、相机仍 front/back；旧服务端 1006/4001 语义与历史发布说明一致；预览 early-return 位于全部 launcher/LaunchedEffect 之后，模态期间采集与生命周期不受影响。
- **第二次全面复核（边界 E2E 后，2026-10-17）**：未发现新的严重问题（预览手势/状态重置、picker 返回、发送竞态、RTL、模式矩阵、录制并发均按设计工作）；补上 EXIF 八朝向像素级覆盖（见上「补覆盖」），全量 **934/0/4**、SDK 186/0（debug/release）、协议 6/6、预览 androidTest 5/5、`assembleDebug` 通过。

**真机 E2E（2026-10-17 17:54，LIO-AN00 / Android 12，debug versionCode 133）**

- **部署**：服务端为本机 `livachat-server`（uvicorn + watchfiles，监听 `https://0.0.0.0:8080`；App `server_address=<lan-host>:8080`）。生成物 mtime 17:35，watchfiles 于 **17:35:57** 重启 worker（PID 89814）→ 运行实例含 gallery 枚举。
- **流程**：工具栏「+ 摄像头」→ DocumentsUI 选图（2736×3648）→ 全屏预览（黑底、左返回/右取消、「发送」灰底白字）→ 点「发送」→ 回主屏。
- **App 日志**：`photo scaled 2736x3648 -> 1440x1920`；`photo pushed 1440x1920 bytes=349029 omni=false`；`send video | seq=1779`；`recv ai_video_ack | seq=242`；随后 `ai_text` 流式返回；无 1006/4001。
- **服务端**：`/opt/lvdata/chat_server.db` messages 新增 user/asr 行（`描述图片内容`，09:55:05Z）→ v3 文本回合成立、帧未被丢。
- **回显落库**：App Room 最新 AI 消息 `ai-msg_h66az543rnwz`（09:54:47Z，1440×1920）`imageCameraFacing=gallery`，文本完整描述所选照片（"这景色真美！……"）；此前同一设备的抓屏消息仍为 `screen`、相机为 `front`。
- **取证说明**：服务端不打印 `camera_facing` 日志；以「服务端回合成立 + final ai_text 原样回显落库（gallery）」闭环。线上值本身由 `VideoTurnPayloadTest`（gallery 落线）与 `GalleryPhotoSenderTest`（sender 断言 gallery）锁定。
- 截图：`/tmp/gallery_e2e_03_preview.png`、`/tmp/gallery_e2e_04_sent.png`（本地，不入库）。

**真机边界 E2E（2026-10-17 18:00–18:10，LIO-AN00 / Android 12，v7.1 debug）**

- **屏幕抓取（v7.1）**：录制授权 → Chrome 前台点悬浮按钮 → `tap capture pushed 940x1920` → ack；回显落库 `imageCameraFacing=screen`（AI 描述证书错误页）。App 前台点按按设计 `REJECTED_FOREGROUND`；华为 Launcher/设置以 `mForceHideNonSystemOverlayWindow` 隐藏悬浮按钮，第三方 App 前台可见。
- **录制中 gallery**：录制中选图发送 → `pushed 1440x1920 omni=false` → AI 描述并回图（`gallery`），录制红色指示持续；停止确认后 `projection stopped by system`。
- **大图 + EXIF**：4000×3000 / EXIF Orientation=6 → 预览正立（蓝条在右）→ 日志 `scaled 2000x1500 -> 1920x1440`、`pushed 1440x1920`；回显 `gallery`，AI 正确描述。
- **双指缩放**：`adb sendevent` 被 SELinux 拒绝；改用设备端 Compose 手势用例 `pinch_zooms_rendered_preview_and_send_still_works`（`GalleryPhotoPreviewScreenTest` **5/5**）：捏合前后 `captureToImage()` 采样差异断言通过，并保存渲染帧 `/tmp/gallery_pinch_before.png` / `_after.png`（目视 fit → 放大填满）；发送仍可用。
- **取消/返回/连续**：右取消、左返回→picker、picker 系统返回→主屏，均无 `GalleryPhoto` 推送；5s 内连续两图均推送并各自回图。
- **模式矩阵**：omni `pushed omni=true`，静音下 8s 无即时回合（仅视觉上下文）；translate/transcribe `pushed omni=false` + ack，无照片回合（服务端门控）；lecture `+` 常驻可见（同一 gate，未单独发送）。
- **RTL**：系统语言临时置阿拉伯语（已恢复中文）：工具栏「+」位于「الكاميرا」阅读起点侧（右）、预览返回在右/取消在左、发送文案 `إرسال`；picker 使用阿拉伯-印度数字。15 语言资源守卫单测通过。
- **旋转**：Manifest `screenOrientation="portrait"`，设备旋转不改变 App/预览布局（设计内）。
- 截图：`/tmp/e2e_exif_01_preview.png`、`/tmp/e2e_exif_03_sent.png`、`/tmp/e2e_omni_03_sent.png`、`/tmp/e2e_rapid_01.png`、`/tmp/e2e_rtl_04_app.png`、`/tmp/e2e_rtl_06_preview.png`、`/tmp/gallery_pinch_before.png`、`/tmp/gallery_pinch_after.png`（本地，不入库）。

**待做**
1. 旧客户端 s2c 兼容前提复核（本会话回显；旧 App 不会收到 gallery）。
2. versionCode 仅在 git 提交时递增（本轮未提交）。
3. 非阻塞：lecture 发送门控未单独实测（与 translate/transcribe 同一服务端 gate）；断网路径未在本轮重测。

---

## 附录 C：§9 代码锚点（2026-10-17 v5.4 实施后快照）

> v6（§10）实施后，`ConversationScreen` / `ConversationViewModel` / `ConversationUiState` 的行号有漂移；以符号锚点为准，§10 的当前行号见附录 D。

| 符号 | 文件 | 行号 |
|---|---|---|
| 底部工具栏三列 / caption 首词 | BottomToolbar.kt | 81-152、209-229 |
| `captionLeading` / `onCaptionClick`（麦克风、扬声器范式） | BottomToolbar.kt | 104-131、156-237 |
| 相机 ToolbarButton（+ 条件渲染 / 整行点击） | BottomToolbar.kt | 133-151 |
| `onPickGallery` 参数（必选） | BottomToolbar.kt | 69 |
| 头像图库 Photo Picker（单图） | ConversationScreen.kt | 126-133 |
| 会话图库 `photoGalleryLauncher` | ConversationScreen.kt | 136-143 |
| 会话页 BottomToolbar 接线 | ConversationScreen.kt | 532-545 |
| `ConversationEvent.SendGalleryPhoto` | ConversationEvent.kt | 23 |
| `toggleCamera()` | ConversationViewModel.kt | 835-859 |
| `bindCamera()` / onBindFailed | ConversationViewModel.kt | 758-772 |
| `SendGalleryPhoto` 分支 / `sendGalleryPhoto()` | ConversationViewModel.kt | 435、862-877 |
| `GalleryPhotoSender`（send / encode / 纯函数 / 三态） | GalleryPhotoSender.kt | 24-158 |
| `applyExifOrientation`（头像/图库共用） | ImageExif.kt | 16-49 |
| `canScreenRecord`（chat-only 先例；本设计入口**不做**模式门控，D5=常驻） | ConversationUiState.kt | 92-94 |
| `CaptureTurnCounter` | CaptureTurnCounter.kt | 14-27 |
| 相机 `processFrame` / push 调用 | CameraController.kt | 205-234 |
| `imageProxyToJpeg`（缩放/旋正/q70） | CameraController.kt | 236-260 |
| `scaledMaxLongSide`（长边硬闸纯函数） | CameraController.kt | 318-330 |
| 相机常量（1280 / q70 / 暖机 / 节流） | CameraController.kt | 292-301 |
| 屏幕抓取 push（text/facing/turnId） | ScreenCaptureController.kt | 639-644 |
| `MAX_LONG_SIDE=1920` / `JPEG_QUALITY=70` / 发图指令资源 `conv_image_action_text`（v7.2 已实施） | ScreenCaptureController.kt | 910-911、939 |
| `scaledCaptureSize`（长边 ≤1920，取偶） | ScreenCaptureController.kt | 985-996 |
| 头像解码 bounds/inSampleSize | AvatarCropScreen.kt | 72-99 |
| `applyExifOrientation`（八种朝向） | AvatarCropScreen.kt | 275-309 |
| SDK `pushVideoJpeg`（预编码直通） | LivaChatSDK.kt | 298-317 |
| `mapCameraFacing`（front/back/screen） | LivaChatSDK.kt | 367-371 |
| `buildVideoPayload`（quality/turnId/text 落线） | LivaChatSDK.kt | 379-400 |
| `videoQuality` 默认 70 / `videoMaxLongSide` 默认 1080（仅 pushBitmap 生效） | LivaChatConfig.kt | 41-44 |
| 断连丢帧 | ConnectionManager.kt | 101-105 |
| video 闸口外发送 | SessionManager.kt | 237-243 |
| 协议 video payload（required + turn_id/text） | `packages/api-schema/c2s/video.json` | — |
| `CameraFacing` 三值枚举 | `packages/api-schema/common.json` `$defs.CameraFacing` | — |
| `_handle_video`（buffer → text/turn 分支 → omni → avdetector → ack） | services/session/orchestrator.py | 2026-2289 |
| v3 文本回合 | services/session/orchestrator.py | 2073-2151 |
| v3 文本回合模式门控（omni/translate/transcribe/asr_only 排除） | services/session/orchestrator.py | 2084-2086 |
| turn_id 首帧直起轮 | services/session/orchestrator.py | 2153-2232 |
| omni 投喂（仅视觉上下文，不直起轮） | services/session/orchestrator.py | 2234-2249 |
| omni 下抓屏停发 text（同款约束先例） | ScreenCaptureController.kt | 642-643 |
| omni 免开口起轮 TODO-1（semantic_vad V1 失败 / manual V2 成功） | docs/fd_pipeline_omni_mode.md | §10 TODO-1 |
| avdetector 帧门控（`not text`） | services/session/orchestrator.py | 2251-2276 |
| `_snapshot_turn_images` | services/session/orchestrator.py | 2291-2307 |
| final ai_text 回图 | services/session/orchestrator.py | 1585-1596 |
| `feed_frame` / `request_video_check` | services/session/av_detector.py | 224-263 |
| 客户端图片落 Room | ChatRepository.kt | 105-148 |
| 图库编码/发送单测（14 例） | GalleryPhotoSenderTest.kt | 30-342 |
| VM FAILED/REJECTED 用例（2 例） | ConversationViewModelVideoTurnTest.kt | 194-208 |
| 工具栏「+」用例（4 例） | BottomToolbarTest.kt（androidTest） | 507-620 |
| 15 目录 key 齐平守卫 | I18nResourceFilesTest.kt | 61-84 |
| `conv_avatar_from_gallery`（可复用文案） | `values/strings.xml` / `values-*/strings.xml` | values:132（各目录同 key） |

---

## 附录 D：§10 代码锚点（2026-10-17 实施后快照）

| 符号 | 文件 | 行号 |
|---|---|---|
| picker 回调 → `OpenGalleryPreview` | ConversationScreen.kt | 136-143 |
| 预览 early-return 块（含 onSend/onCancel/onBackToPicker） | ConversationScreen.kt | 334-348 |
| 头像裁剪全屏覆盖层（early-return 先例） | ConversationScreen.kt | 352-360 |
| 预览 Uri 状态 `galleryPreviewUri` | ConversationUiState.kt | 45-46 |
| 头像裁剪 Uri 状态（旋转保持先例） | ConversationUiState.kt | 43-44 |
| `OpenGalleryPreview` / `DismissGalleryPreview` 事件 | ConversationEvent.kt | 23-25 |
| VM Open/Dismiss/Send 分支 | ConversationViewModel.kt | 435-442 |
| `sendGalleryPhoto`（发送后处理） | ConversationViewModel.kt | 870-885 |
| 把头像 Uri 写入 UiState（先例） | ConversationViewModel.kt | 674、478-479 |
| 预览页 `GalleryPhotoPreviewScreen`（B 方案） | GalleryPhotoPreviewScreen.kt | 61-190 |
| 预览页 BackHandler（系统返回=左返回） | GalleryPhotoPreviewScreen.kt | 68 |
| 预览页顶部左返回/右取消 | GalleryPhotoPreviewScreen.kt | 99、107 |
| 预览页 Coil `AsyncImage` / 底部发送按钮 | GalleryPhotoPreviewScreen.kt | 136、179 |
| 单图查看器骨架（Coil + 缩放 + 拖动 + BackHandler） | HotSlotFrameViewer.kt | 65-110、207-266 |
| 缩放/平移纯函数 | ImageViewerOverlay.kt | 277-315 |
| 缩放数学单测 | ImageViewerMathTest.kt | 1-145 |
| 全屏查看器黑底 + AsyncImage 先例 | FrameViewerOverlay.kt | 72-160、321-330 |
| 确认页顶部取消/底部全宽按钮样式 | AvatarCropScreen.kt | 143-150、221-268 |
| Coil 依赖（2.6.0） | apps/android/LivaChatApp/build.gradle.kts | 118-120 |
| 15 目录 key 齐平守卫 | I18nResourceFilesTest.kt | 61-84 |
| 预览 Compose 测试（5 例） | GalleryPhotoPreviewScreenTest.kt（androidTest） | 43-264 |
| VM Open/Dismiss/Send 用例（3 例） | ConversationViewModelVideoTurnTest.kt | 213-242 |
| 既有 `SendGalleryPhoto` 事件 | ConversationEvent.kt | 27 |

---

## 附录 E：§11 代码锚点（2026-10-17 设计阶段快照）

| 符号 | 文件 | 位置 |
|---|---|---|
| `CameraFacing` 三值枚举 + 描述 | `packages/api-schema/common.json` | `$defs.CameraFacing` |
| `video.payload.camera_facing` `$ref` | `packages/api-schema/c2s/video.json` | payload properties |
| `ai-text.image.camera_facing` `$ref` | `packages/api-schema/s2c/ai-text.json` | payload.image properties |
| Python 生成枚举 FRONT/BACK/SCREEN | `packages/sdk-python/src/livachat_protocol/generated/protocol_models.py` | 96-101 |
| Kotlin 生成枚举 FRONT/BACK/SCREEN | `packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated/ProtocolModels.kt` | 53-57 |
| SDK `mapCameraFacing`（back/screen/else front） | `apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatSDK.kt` | 368-371 |
| `buildVideoPayload` 落线 | 同上 | 379-400 |
| App 图库照片 `CAMERA_FACING="screen"`（v6.4） | `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/media/GalleryPhotoSender.kt` | 119 |
| 屏幕抓取 `"screen"`（不改） | `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/media/ScreenCaptureController.kt` | 639-644 |
| 服务端入 buffer / final 回显 / avdetector / 快照 | `services/session/orchestrator.py` | 2053、1595、2261、2301 |
| 网关非法帧处理（1006 / 丢帧 / 10 连续断连） | `services/gateway/connection.py`、`gateway/codec.py` | 218-227、36、144-159 |
| 生成命令 / CI 校验 | `Makefile`、`scripts/generate_python_api.py` | Makefile 4-9；脚本 `--check` |
| 协议文档 c2s / s2c facing 表 | `docs/ws-protocol-design.md` | 274、496 |
| SDK 映射测试（当前三值 + 未知兜底） | `apps/android/LivaChatSDK/src/test/kotlin/dev/livachat/sdk/VideoTurnPayloadTest.kt` | 16-25 |
| App 发送参数测试（当前断言 screen） | `apps/android/LivaChatApp/src/test/kotlin/dev/livachat/app/media/GalleryPhotoSenderTest.kt` | send 用例 |
| 服务端 v3 文本回合测试（可扩展 gallery） | `services/tests/integration/orchestrator/test_video_text_turn.py` | — |
| 服务端帧处理测试（可扩展 gallery） | `services/tests/integration/orchestrator/test_video_frame_processing.py` | — |

> 附录 E 为设计阶段快照；实施后按实际生成物/行号回填。
