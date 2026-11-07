# 会话页"正在录制"图标重设计 —— 红圈 + 实心圆角方块

> 状态：**已实施（2026-09-14）**。最终造型（review 二次修订，方案 E）：
> **红圈 + 实心圆角方块**（11×11，rx 1.5，色 = 主题新增的 `Red` 常量）。
> 造型演进：首版"显示器"被否 → 二版"手机轮廓 + home 条"（方案 C）已实施 → **当日二次修订
> 定为实心圆角方块（方案 E）**，手机轮廓版被替换（语义上更贴"屏幕录制中"的停止/状态语感）。
> 改动 4 个文件（drawable / `Color.kt` / `Theme.kt` 调色板预览 / `ConversationScreen.kt`），
> `:LivaChatApp:compileDebugKotlin` 通过 + Android 原生渲染管线像素级验证（§4）。
> 范围：`apps/android/LivaChatApp` 的 `res/drawable/ic_screen_capture_active.xml`
> + `ConversationScreen.kt` TopAppBar 录制按钮（tint 1 行 + 注释）。
> **不改**：未录制态图标（`Icons.Outlined.FiberManualRecord`）、点击行为（停止确认弹窗）、
> `contentDescription`、通知栏小图标、图标尺寸/位置、协议与服务端。
> 预览图（本地生成，未入库）：`/tmp/rec_icon_mock/mock_square.png`（候选 D/E/F 对比，
> 源码 `index.html` 同目录）。

## 1. 现状（改动前）

会话页 TopAppBar actions 的录制按钮（`ConversationScreen.kt:322-338`）两态：

| 状态 | 图标 | 颜色 |
|---|---|---|
| 录制中 | `R.drawable.ic_screen_capture_active`（自绘 vector） | `MaterialTheme.colorScheme.primary`（= `BrandIndigo #6366F1`） |
| 未录制 | `Icons.Outlined.FiberManualRecord`（Material） | `TextSecondary` |

`ic_screen_capture_active.xml`（24dp / viewport 24）：外圈空心圆（r=10，stroke 2，
round cap）+ 内部一条三峰波浪（stroke 1.4，x∈[7.5,16.5]）。

关键机制：path 的 `strokeColor`/`fillColor` 均为白色占位，Compose `Icon(tint = …)` 以
ColorFilter **整体单色化**——这也是"外圈改红"为什么只需改 tint 值即可。

相关背景：既有设计曾记「录制中标题栏可加计时/红点呼吸等更强指示（当前复用图标高亮两态）」
（该条目已随本设计同步更新）——本设计是对该方向的**轻量响应**（仅颜色强化，不含计时/动画）。

## 2. 设计目标（需求原文拆解）

1. 外圈：从目前的颜色（indigo）→ **红色**；
2. 内部：三峰波浪 → **实心圆角方块**（方案 E，review 二次修订；中间版本为"手机 screen
   轮廓"，对应"录制手机屏幕"语义，被方块替代）；
3. 隐含约束：保持 24dp 尺寸、TopAppBar 位置与点击行为、暗底对比度（本 App 为
   **暗色单主题**，Background `#0F172A`）。

## 3. 设计方案

### 3.1 几何

- **外圈不动**：`M12,2a10,10 0 1,0 0,20a10,10 0 1,0 0,-20z`，stroke 2，round cap。
  内净空半径 = 10 − 1 = **9**。
- **实心圆角方块**：**11×11，rx 1.5**，居中（x/y 6.5→17.5），**fill 白占位 + tint 红**，
  无描边。pathData（vector drawable 只有 `<path>`，无 `<rect>` 元素，用 arc 写法）：

  ```
  M8,6.5 h8 a1.5,1.5 0 0 1 1.5,1.5 v8 a1.5,1.5 0 0 1 -1.5,1.5
  h-8 a1.5,1.5 0 0 1 -1.5,-1.5 v-8 a1.5,1.5 0 0 1 1.5,-1.5 z
  ```

- **几何余量核算**：圆角弧心 (8,8) 距中心 5.66，+ rx 1.5 = **7.16 < 9**（实心无描边，
  不再加半线宽），与内圈留白 ≥ 1.8；直角假想角点距中心 7.78，留白 ≥ 1.2。
- **备选对照（review 已看，未采纳）**：D 直角实心方块（同 11×11 尺寸的直角版；再放大
  到 ~12 即贴环）；F 手机轮廓去掉 home 条。预览图 `/tmp/rec_icon_mock/mock_square.png`。
- 已否决（review 历史）：显示器/带支架显示器（首版）；手机轮廓 + home 条（二版实施后
  被方块替代）。

### 3.2 颜色（两个决策点）

**D1 —— 红用哪个值？（review 定：新增 `Red` 常量）**

| 选项 | 说明 | 评价 |
|---|---|---|
| **新增 `Red` 常量（选定）** | `Color.kt` 新增 `val Red = Color(0xFFEF4444)`（值同原视觉，零变化），调用点 `tint = Red` | 命名取色相、不携带语义——避免 `error`（错误）与录制（正常状态）混淆；`Error` 保持不变 |
| 复用 `Error` | 零新增 | 语义歧义：error 隐含"故障"，录制是正常状态（review 否决） |
| `Recording` 等语义名 | 语义更准 | 与 `Red` 同值则纯别名；命名绑定单一用途、复用性差 |

**D2 —— 单色红（A）还是红圈+白方块（B）？（review 选定 A）**

| 选项 | 观感 | 实现代价 |
|---|---|---|
| **A 单色红（选定）** | 圈+方块同红，符合"录制红"通用惯例（相机红点、直播红点）；与 TopBar 其余单色图标语言一致 | **最小**：drawable 仅换内部 glyph path，`tint` 一行由 `colorScheme.primary` 改 `Red` 常量（见 D1），机制不变 |
| B 双色（红圈 + 白方块 `#F8FAFC`） | 暗底上白色方块对比更强 | 单 tint 机制失效，需二选一：<br>B1 颜色固化进 XML + `tint = Color.Unspecified`；<br>B2 拆两个 drawable（圈 / 方块）在 `Box` 中叠放，各自 tint |

对比度：`#EF4444` on `#0F172A` ≈ **4.75:1**（非文本图形 ≥3:1 AA），两方案均达标。

### 3.3 预览图

本地产物（未入库，评审后可弃）：`/tmp/rec_icon_mock/mock_square.png` —— 现状（手机版）+
D/E/F 候选（24/48/96px 三档 + TopAppBar 真实尺寸上下文）。仓库 `docs/` 目前无图资产惯例，
如需长期留档再另议入库位置。

```
最终造型（E）
   ╭──────╮
   │      │   红圈 + 实心圆角方块（11×11 rx1.5）
   │  ■   │   色：#EF4444（Red 常量），SRC_IN tint 注入
   │      │
   ╰──────╯
```

## 4. 实施记录（2026-09-14，已完成）

1. `res/drawable/ic_screen_capture_active.xml`：三峰波浪 path → 实心圆角方块 path
   （`fillColor="#FFFFFFFF"` 占位 + tint 注入；最终版为单条 fill path，无描边）；
   文件头注释同步为"空心圆 + 实心圆角方块"。
2. `ui/theme/Color.kt`：新增 `val Red = Color(0xFFEF4444)`（通用红）；`Theme.kt` 调色板
   预览补 `ColorSwatch("Red", …)`。
3. `ConversationScreen.kt:324/330`：注释同步"红圈 + 实心方块"；
   `tint = MaterialTheme.colorScheme.primary` → `tint = Red`。

**验证**：`:LivaChatApp:compileDebugKotlin` 通过（资源 + Kotlin 均编译）；
图标经 Android 原生渲染管线像素级验证（Robolectric `@GraphicsMode(NATIVE)` 探针，跑完即删）——
红圈 + 实心圆角方块渲染正确（中心像素 `FFEF4444` 实心、圆角与内圈留白 ≥1.8）、
SRC_IN tint 着色正常（预览：`/tmp/rec_icon_mock/robo_264px.png`）。
部署物核对：`clean assembleDebug` 产物 APK 内编译资源经 `aapt2 dump xmltree` 确认
含方块 path；已 `adb install -r` 到真机并正常启动（19:03）。
**测试影响**：无。全仓检索 `ic_screen_capture_active` 仅此 1 处引用；屏录相关测试
（`ConversationViewModelScreenCaptureTest` 等）断言的是 VM 状态与事件，不涉及图标资源。

**真机自测**：
- ✅（2026-09-14，adb 自动化点按）：开始录制 → 按钮呈**红圈 + 实心圆角方块**（顶栏裁图核对：
  `/tmp/rec_icon_device.png`）；停止 → 恢复灰色空心圆；FGS 通知随停录消失；全程无崩溃
  （授权→启动→停止流程完整；屏幕帧是否上行未在本轮核验）。
- 录制中旋转/切前后台返回 → 红色态保持：待做（`isRecording` 驱动，机制未变）。

## 5. 影响面 / 兼容

- 无障碍：`contentDescription` 两态不变（"开始录制"/"停止录制"）。
- 视觉一致：未录制态仍为中性 `TextSecondary`，红 = 录制中，语义唯一。
- 与 `StatusIndicator` 的 DISCONNECTED 红点同主题色但形态不同，无歧义风险（§3.2 D1）。

## 6. 决策记录（review 2026-09-14）

| # | 决策 | 选定 |
|---|---|---|
| D1 | 红色取值：复用 `Error` vs 新增独立常量 | **新增 `Red` 常量**（#EF4444，视觉不变；review 修订：复用 `Error` 有语义歧义） |
| D2 | 单色红（A）vs 红圈+白方块（B） | **单色红** |
| D3 | 内部造型（三轮） | 显示器（否）→ 手机轮廓+home 条（C，实施后被替换）→ **实心圆角方块（E，最终）** |
