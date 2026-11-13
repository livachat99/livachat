# Web Meeting 背景 Filter 预加载实施与测试计划

> **依据**：[`docs/webmeeting_background_filter.md`](webmeeting_background_filter.md)
> **目标**：资源预加载（不 attach processor）；`backgroundEffectsPreload` 默认 `auto`；不使用 Blob URL；摄像头关闭时禁用 filter；完整预热本期不做。
> **状态**：P0/P1/P2 完成；P3 Chrome 桌面主要 E2E 完成（含缩略图/config 图片）；Android Chrome 158 在 Adreno 740 上 filter 实测通过，在 Mali-G76 上按能力门控禁用；慢网失败回退、低端模拟、其他移动端手测待补
> **原则**：先配置/资源加载器，再 UI 集成，最后 E2E；每一步可构建、可测试、可回滚。

---

## 1. 阶段总览

| 阶段 | 内容 | 状态 | 验收 |
|------|------|------|------|
| P0 | 配置字段、依赖、单测基线 | ✅ 完成 | `npm run check` / `npm test` |
| P1 | `backgroundAssets`：显式 WASM base、loader/WASM/model fetch、无 Blob URL | ✅ 完成 | 6 个测试文件 / 31+ 单测 |
| P2 | 预加载 hook、loading、摄像头关闭禁用 filter | ✅ 完成 | 类型检查 + 浏览器冒烟 |
| P3 | E2E / 弱网 / 缓存命中 / 文档更新 | 🟡 部分完成 | 主要路径通过；慢网/移动端待补 |

---

## 2. P0：配置与基线

- [x] `config.ts` 增加 `backgroundEffectsPreload: 'auto' | 'off'`，默认 `auto`
- [x] `config.example.json` 增加字段
- [x] `config.test.ts`：默认值、显式 `off`、非法值
- [x] 添加 `@mediapipe/tasks-vision@0.10.14` 直接依赖
- [x] `npm run check`、`npm test` 基线通过

---

## 3. P1：backgroundAssets

- [x] 定义 `DEFAULT_TASKS_VISION_VERSION=0.10.14`、`DEFAULT_WASM_BASE`、`DEFAULT_MODEL_URL`
- [x] `resolveBackgroundAssetPaths(mediapipeAssetBase)`：显式默认 base，不允许 `undefined`
- [x] `preloadBackgroundAssets()`：
  - [x] `FilesetResolver.forVisionTasks(wasmBase)`
  - [x] fetch loader JS / WASM / model
  - [x] `cache: 'force-cache'`，消费 body
  - [x] `response.ok`、WASM content-type、HTML fallback 校验
  - [x] **不创建 Blob URL**
  - [x] AbortSignal 支持
  - [x] `partialFailure` / `essentialFailure`
- [x] `shouldPreloadBackgroundEffects()`：
  - [x] `auto/off`
  - [x] `saveData`、2G/3G、`hardwareConcurrency<4`、`deviceMemory<4`
  - [x] unsupported / hidden
- [x] 单元测试：
  - [x] 默认 base 是 CDN，不是 `undefined`
  - [x] fetch 消费 body
  - [x] model 失败 → partial；loader+WASM 失败 → essential failure/error
  - [x] 能力门控分支

---

## 4. P2：预加载 hook 与 UI

- [x] `useBackgroundPreload`：
  - [x] 入会 connected + visible 后延迟 2.5 秒启动
  - [x] 摄像头关闭时仍然预加载
  - [x] 用户已点击 filter 时取消未启动的预加载
  - [x] preload 超时 15 秒后回退
  - [x] 超时后不 abort in-flight fetch，允许 processor 复用缓存/进行中的响应
  - [x] 卸载/离开时 abort
- [x] `CameraSettings`：
  - [x] 创建 processor 时始终显式传 `assetPaths`（同一 wasmBase/modelUrl）
  - [x] 资源 preloading 时点击 filter：loading，等待后创建 processor
  - [x] `setProcessor` in-flight 去重，避免 assets 到达/重连时重复初始化 MediaPipe
  - [x] `Loading filter model...` inline 提示
  - [x] 摄像头关闭：所有非 None filter disabled + `Turn on camera...` 提示
  - [x] camera off 时预加载继续
  - [x] `None` 在 loading 中可点击取消
  - [x] 首帧 (`onFrameProcessed`) 后清除 loading
- [x] `messages.ts` 增加文案
- [x] filter 缩略图卡片：Blur 静态效果图、Desk/Nature/Lake/Flower 照片、config `backgroundImages` 追加本地/remote 图片
- [x] 类型检查通过

---

## 5. P3：测试矩阵

### 自动化

- [x] 单测：config、asset base、fetch body、partial/essential、能力门控
- [x] 类型检查
- [x] 构建（`build:demo` 通过；`dist/` 已删除）
- [ ] 慢网路自动回退（仅手动/路由模拟过，未完全自动化）

### 浏览器 E2E（Chrome 桌面）

- [x] 入会 + 摄像头：延迟后出现 MediaPipe 请求
- [x] 点击前 `cameraTrack.getProcessor()` 为 undefined（不 attach）
- [x] 预加载完成后点击 Blur：loading → 效果
- [x] 预加载未完成时点击：loading 提示出现
- [x] 摄像头关闭：按钮 disabled + 提示；预加载仍发生
- [x] 打开摄像头：按钮恢复
- [x] 缓存命中：预加载与 processor 资源均为 transferSize 0 / 快速
- [ ] 加载中点击 None：代码路径已实现，未单独 E2E
- [ ] 资源失败：单测覆盖 partial/essential；浏览器 toast/fallback 未单独 E2E
- [ ] 低端/节流模拟：纯函数测试覆盖，浏览器未单独模拟

### 手测

- [ ] iPhone/iPad Safari
- [x] Android Chrome（Chrome 158.0.8127.36；Adreno 740 上 Blur/图片背景通过；Mali-G76 因缺少 float linear 被门控）
- [ ] 弱网/省流量
- [ ] 长时间会议内存

---

## 6. E2E 记录（2026-11-13）

环境：Vite dev `http://localhost:5174/meeting/`、桌面 Chrome、真实摄像头、默认 CDN。

| 项目 | 结果 |
|------|------|
| 预加载触发 | 入会后约 2.5 秒开始；未点击 filter 前已出现 loader/WASM/model 请求 |
| 不 attach | 点击前 `cameraTrack.getProcessor()` 为 `undefined`，无 processor |
| 缓存命中 | 热缓存下 loader/WASM/model 资源 `transferSize: 0` |
| 点击后效果 | 热缓存：Blur 点击 → loading 出现 → 首帧约 **265 ms**；processor `mode=background-blur`，分割约 1.1 ms/帧 |
| 摄像头关闭 | 所有非 None filter `disabled=true`，None 可点击；设置页显示 camera-off 提示 |
| 摄像头打开 | Blur 恢复 `disabled=false` |
| 摄像头关闭时预加载 | 摄像头关闭后资源请求仍发生（缓存命中） |
| 慢网模拟 | 路由延迟模型/WASM 时，点击后显示 `Loading filter model...`；预加载超时后回退路径可进入 processor 加载 |
| 冷缓存 | 测试环境 CDN 波动较大，WASM 曾超过 12 s；preload timeout 后 loading 保留，未在测试窗口内完成效果，需后续专项验证 |

### 背景图片与缩略图 E2E（2026-11-13）

环境：Vite dev、桌面 Chrome、真实摄像头、demo LiveKit server；临时新增本地测试图片，并通过本地 CORS server 提供一个 remote URL；测试后已恢复 `public/config.json` 并删除临时资源。

| 项目 | 结果 |
|------|------|
| Filter 卡片 | None 保持文字按钮（71×54）；Blur/Desk/Nature/Lake/Flower/config 图片均为 96×54，`background-size: cover` |
| 卡片文字 | Blur 文字居中，字号与 None 一致；Desk/Nature/Lake/Flower/config 图片不显示文字，保留 `aria-label`/`title` |
| 内置缩略图 | Blur 使用 `blur.svg` data URL；Desk/Nature/Lake/Flower 使用照片 WebP + 独立缩略图 |
| config 本地图片 | `backgrounds/e2e-office.svg` + 独立缩略图正常显示；点击后 51 ms 进入 `virtual-background`，实际路径与配置一致 |
| config thumbnail 缺省 | `e2e-library.svg` 无 thumbnail，卡片回退使用完整图片；点击后 52 ms 生效 |
| config remote 图片 | `http://127.0.0.1:8125/...`（CORS `*`）卡片与 processor 均正常；点击后 101 ms 进入 `virtual-background` |
| Blur | 点击后 loading 出现，159 ms 进入 `background-blur`；`transformer.options.blurRadius=16` |
| 选中态 | 卡片 `aria-pressed=true`，蓝色 outline `rgb(31, 140, 249) solid 2px` |
| 摄像头关闭 | 所有非 None 卡片（含 config 图片）`disabled=true`，None 可点击；显示 camera-off 提示 |
| 摄像头重新打开 | 卡片恢复可点击；预处理后的 camera track 仍工作 |
| None | processor `mode=disabled`，None 获得选中态 |
| Desk 照片替换 | 设置页请求 `desk-thumb.webp`；点击后加载 1280×720 `desk.webp`，virtual background 与人物分割正常；卡片无文字，`aria-label`/`title=Desk` |
| Nature 照片替换 | 设置页请求 `nature-thumb.webp`；点击后加载 1280×720 `nature.webp`，virtual background 与人物分割正常；卡片无文字，`aria-label`/`title=Nature` |
| Nature v2 替换 | 新的 1280×720 PNG 直接转 WebP q82；缩略图与主图加载正常，virtual background 与人物分割正常 |
| Lake 新增 | 设置页第 5 个 filter；请求 `lake-thumb.webp`；点击后加载 1280×720 `lake.webp`，virtual background 与人物分割正常；卡片无文字，`aria-label`/`title=Lake` |
| Flower 新增 | 设置页第 6 个 filter；请求 `flower-thumb.webp`；点击后加载 1280×720 `flower.webp`，virtual background 与人物分割正常；卡片无文字，`aria-label`/`title=Flower` |
| 自托管 + Huawei Browser | 本地 WASM/model 加载正常；能力探测发现 Huawei Browser 缺少 float linear 支持；Background effects 按钮正常显示，点击后显示 unsupported 提示，不创建 processor |
| 控制台 | 0 errors；Huawei 测试不再触发 GPU abort 或 CPU fallback |

### Android Chrome E2E（2026-11-13）

环境：Huawei Android 12 设备（Mali-G76）、Chrome for Android 158.0.8127.36（arm64-v8a，versionCode 812703604）、Vite dev `http://localhost:5174/meeting/`、`adb reverse`、自托管 MediaPipe。

| 项目 | 结果 |
|------|------|
| 安装 | 从 APKMirror 下载 arm64-v8a standalone APK（292,694,724 bytes，SHA-256 `46a95440...`）；与设备原有 Chrome 证书一致，`adb install -r` 成功 |
| 入会/摄像头 | `http://localhost` 安全上下文；LiveKit 连接成功，camera 采集与本地预览正常 |
| WebGL2 能力 | `EXT_color_buffer_float`、`EXT_color_buffer_half_float` 可用；`OES_texture_float_linear`、`OES_texture_half_float_linear` 均缺失；renderer `ANGLE (ARM, Mali-G76, OpenGL ES 3.2)` |
| 点击前 | Background effects 控件正常显示；无 unsupported 提示；无 MediaPipe WASM/model 请求 |
| 点击 Blur | 显示 `Background effects are not supported on this device.`；无 loading、无 processor、无 MediaPipe 资源请求 |
| 自托管资源 | `/meeting/vendor/mediapipe/...` 均可访问（model 249,537 bytes；WASM 9,423,986 bytes）；因能力门控未触发下载 |
| 控制台 | 无 GPU abort、无未捕获异常 |

结论：升级到最新 Chrome 不能绕过该限制；Mali-G76 驱动不暴露 float linear，能力门控按设计拒绝 filter。要在此类设备启用背景效果，需要改用不依赖 float-linear 的 GPU 路径，或接受用户已拒绝的 CPU delegate。

### Android Chrome + Adreno E2E（2026-11-13）

环境：Xiaomi Android 16 设备（Adreno 740）、Chrome for Android 158.0.8127.36、Vite dev `http://localhost:5174/meeting/`、`adb reverse`、自托管 MediaPipe。

| 项目 | 结果 |
|------|------|
| 安装 | MIUI 的 `adb install` 被 `INSTALL_FAILED_USER_RESTRICTED` 拦截；将 APK 推送到手机 `Download/` 后手动安装成功 |
| WebGL2 能力 | `EXT_color_buffer_float`、`EXT_color_buffer_half_float`、`OES_texture_float_linear`、`EXT_float_blend` 可用；renderer `ANGLE (Qualcomm, Adreno (TM) 740, OpenGL ES 3.2)`；能力门控通过 |
| 预加载 | 点击前已加载自托管 `vision_wasm_internal.js`、`vision_wasm_internal.wasm` 和 `selfie_segmenter.tflite`；点击时无额外模型下载 |
| Blur | 点击后出现 `Loading filter model...`，预加载命中下约 0.5 s 内消失；背景虚化生效，人物边缘正常 |
| Lake | 点击后切换到 `lake.webp`；背景替换和人物分割正常 |
| None | 恢复原始摄像头画面 |
| 布局 | 手机竖屏 Settings 面板宽度等于 392px viewport，占满全屏 |
| 控制台 | 无 GPU abort、无未捕获异常；仅有自动播放 `AudioContext` 提示，与 filter 无关 |

结论：满足 float/half-float color-buffer + 对应 linear filtering 的 Android Chrome 可以正常使用 Background Processor；Adreno 740 通过，Mali-G76 被门控拒绝，符合按 GPU 能力判断的设计。

### 已知非阻塞问题

- 冷缓存下 CDN 吞吐波动明显：同一 WASM 在 curl 约 4 s，浏览器 preload 曾超过 12 s；这是网络/CDN 因素，preload 超时回退按设计生效。
- preload 超时后现在不 abort 在途请求；processor 可复用缓存/在途响应，避免立即重复下载。
- 加载中点击 None、资源失败回退、低端设备浏览器模拟尚未单独 E2E。

---

## 7. 实施记录

| 日期 | 阶段 | 状态 | 备注 |
|------|------|------|------|
| 2026-11-13 | 计划 | 完成 | 本文 |
| 2026-11-13 | P0 | 完成 | `backgroundEffectsPreload` + tasks-vision 直接依赖；25 单测 |
| 2026-11-13 | P1 | 完成 | `backgroundAssets.ts` + 7 个新测试；共 32 单测 |
| 2026-11-13 | P2 | 完成 | `useBackgroundPreload` + CameraSettings loading/camera-off UI + setProcessor in-flight 去重；类型检查/build 通过 |
| 2026-11-13 | P3 | 部分完成 | Chrome 桌面主要 E2E 通过；Android Chrome 158 在 Adreno 740 上 Blur/Lake/None 实测通过，Mali-G76 因缺少 float linear 被门控；慢网/低端/其他移动端待补 |
| 2026-11-13 | 背景 filter UI | 完成 | 96×54 缩略图卡片；Blur 文案居中、图片卡片无文字；config `backgroundImages` 支持本地/remote |
| 2026-11-13 | 背景图片 E2E | 完成（主要路径） | 真实摄像头 + demo LiveKit；本地/remote config 图片、缩略图回退、Blur、camera-off、None 均通过；测试资源已清理 |
| 2026-11-13 | Desk 替换 | 完成 | Desk 改为 1280×720 WebP 照片 + 独立缩略图；真实入会验证 virtual background 正常 |
| 2026-11-13 | Nature 替换 | 完成 | Nature 改为 1280×720 WebP 照片 + 独立缩略图；真实入会验证 virtual background 正常 |
| 2026-11-13 | Nature v2 替换 | 完成 | 新 1280×720 图直接转 WebP q82 + 独立缩略图；真实入会验证 virtual background 正常 |
| 2026-11-13 | Lake 新增 | 完成 | 4:3 源图 cover 裁到 16:9，输出 1280×720 WebP + 独立缩略图；真实入会验证 virtual background 正常 |
| 2026-11-13 | Flower 新增 | 完成 | 4:3 源图 cover 裁到 16:9，输出 1280×720 WebP + 独立缩略图；真实入会验证 virtual background 正常 |
| 2026-11-13 | 自托管 MediaPipe | 完成 | 加入本地 WASM/model；Huawei Browser 因缺少 float linear 在点击时提示 unsupported、不创建 processor，避免 CPU fallback |
| 2026-11-13 | Android Chrome 158 | 完成（设备限制） | 安装 Chrome 158.0.8127.36；入会/摄像头正常；Mali-G76 缺少 float linear，点击 filter 显示 unsupported，不创建 processor |
| 2026-11-13 | Android Chrome + Adreno 740 | 完成（主要路径） | 自托管 WASM/model 预加载；Blur、Lake、None 实测通过；Settings 竖屏全宽；Mali-G76 仍按能力门控拒绝 |
