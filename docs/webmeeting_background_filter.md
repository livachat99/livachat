# Web Meeting 背景 Filter 预加载设计

> **状态**：已实施（2026-11-13）；P0/P1/P2 完成，P3 Chrome 桌面主要 E2E 通过；Android Chrome 158（Adreno 740）filter 实测通过；2026-11-14 修复 iPad Safari GPU 门控误判（Mac/iPad/iPhone Safari 验证通过）、None 生命周期（`stopProcessor()`）、WebGL context loss 恢复、Safari None 黑屏和 Huawei Browser 竖屏 Settings；Mali-G76 实测可通过门控，但约 20s 后发生 context loss，由恢复逻辑兜底；慢网/其他移动端手测待补。
> **日期**：2026-11-13
> **范围**：`apps/web/meeting` 会中 Settings 的 Blur / 虚拟背景功能
> **相关文档**：[`webmeeting_design.md`](webmeeting_design.md) §3.8.6、[`webmeeting_impl_plan.md`](webmeeting_impl_plan.md)、[`webmeeting_background_filter_impl_plan.md`](webmeeting_background_filter_impl_plan.md)

---

## 1. 背景与现状

当前实现是**纯懒加载**：

1. 用户在 Settings 中点击 Blur / Desk / Nature / Lake / Flower / config 追加图片；
2. `CameraSettings` 的 `effectsActive` 变为 true；
3. 创建 `BackgroundProcessor`；
4. `LocalVideoTrack.setProcessor()` 触发 `processor.init()`；
5. 此时才开始加载：
   - MediaPipe WASM（解码后约 9.4 MB，传输约 2.0 MB）；
   - `selfie_segmenter.tflite`（约 250 KB）；
   - 初始化 GPU delegate 和 `ImageSegmenter`；
6. 加载完成后才真正应用 filter。

相关代码：

- `apps/web/meeting/src/components/CameraSettings.tsx`
  - `backgroundType` / `effectsActive`
  - `processorRef` / `getProcessor()`
  - `switchTo()` + `setProcessor()`
  - `supportsBackgroundProcessors()` 结果已做模块级缓存
- `apps/web/meeting/src/config.ts`
  - `enableBackgroundEffects`
  - `mediapipeAssetBase`

问题：

- 从点击到看到效果包含“模型下载 + WASM 编译 + GPU 初始化”，延迟明显；
- 点击后当前没有 loading 提示，用户可能以为没生效而重复点击；
- 低端设备/慢网络下延迟更大。

## 2. 已确认决策

| 决策 | 结论 |
|------|------|
| 主方案 | 只做资源预加载，不 attach processor |
| 预加载时机 | 入会后异步进行，不要求摄像头已打开 |
| `backgroundEffectsPreload` 默认值 | `auto` |
| 模型 Blob URL | **不采用**；模型走普通 HTTP 缓存 |
| 摄像头关闭时 | **禁用所有非 None filter 卡片**，不允许点击 filter；可显示开启摄像头提示 |
| 完整预热（attach disabled processor） | **本期不做** |
| 加载文案 | 英文 `Loading filter model...` |
| 完整预热/服务端虚化 | 不在本期范围 |

## 3. 目标与非目标

### 目标

- 入会后、设备/网络能力允许时，只预加载资源，不创建/挂载 processor。
- 摄像头关闭时仍可预加载资源；但 background filter 按钮禁用。
- 用户点击 filter 时：
  - 资源已预加载：创建 processor，利用缓存；
  - 资源仍在加载：显示明确 loading 提示；
  - 完成后自动应用用户最后选择的效果。
- 不显著影响入会流程和音视频首帧。
- 低端设备、省流量模式不预加载，保留按需加载。
- 预加载失败不阻塞会议；用户主动选择时可回退到远程加载/重试。

### 非目标

- 不改变背景效果算法或模型供应商；
- 不做服务端虚化；
- 不改变当前 MediaPipe 模型；
- 不在本阶段实现 i18n（UI 仍为英文）；
- 本期不做 attach disabled processor 的完整预热。

## 4. 实测基线

测试环境：桌面 Chrome、macOS、摄像头 1280×720、默认 CDN（jsDelivr + Google Storage）。

### 4.1 冷缓存（浏览器缓存清空，首次点击）

| 阶段 | 耗时 |
|------|------|
| 点击 → 首个 MediaPipe 请求 | 25 ms |
| loader JS | 205 ms |
| WASM 下载 + 流式编译 | **4,184 ms** |
| WASM 结束 → 模型请求 | 56 ms |
| `selfie_segmenter.tflite` 下载 | **3,382 ms** |
| 模型结束 → processor ready | 33 ms |
| processor ready → 首帧分割 | 227 ms |
| **点击 → 首帧效果** | **约 8.1 s** |

网络/WASM 阶段占约 93%；GPU/首帧处理约 0.26 s。

### 4.2 热缓存（资源已缓存，processor 未挂载）

| 指标 | 耗时 |
|------|------|
| 点击 → processor ready | **115 ms** |
| processor ready → 首帧分割 | **190 ms** |
| 点击 → 首帧效果 | **约 305 ms** |
| 首次分割回调 | 117.6 ms |
| 稳定分割耗时 | ~1.1 ms/帧 |
| WASM `instantiateStreaming` | 60 ms |

> 注意：0.3 s 是“第二次进入 / 浏览器已有缓存”的实测结果；
> 预加载方案还需验证 `fetch` 预取是否真的能产生同样的缓存命中，尤其是 loader JS。
> 该验证属于 §7 的验收项。

### 4.3 资源体积与缓存

表中“传输大小”是默认 CDN（jsDelivr）在 `Accept-Encoding: br` 下的**压缩后**字节数；“解码后大小”是磁盘上的原始字节数，也是浏览器解压后占用的内存体积。实测响应头：

```text
content-type: application/wasm
content-encoding: br
content-length: 2005451
```

| 资源 | 传输大小（Brotli） | 解码后大小 | 压缩比 | 缓存策略 |
|------|------------------|------------|--------|----------|
| `vision_wasm_internal.js` | 45,605 B | 209,826 B | ~4.6× | `immutable, max-age=31536000` |
| `vision_wasm_internal.wasm` | 2,005,751 B | 9,423,986 B | ~4.7× | `immutable, max-age=31536000` |
| `selfie_segmenter.tflite` | 249,537 B | 249,537 B | 1×（tflite 本身已压缩） | `max-age=3600`（1 小时） |

**压缩由静态服务器决定，自托管时必须显式配置**：

| 服务器 | wasm 是否默认压缩 | 配置 |
|--------|------------------|------|
| jsDelivr（默认 CDN） | 是（Brotli） | 无需配置 |
| Caddy | 是（2023-10 起 `encode` 默认内容类型已含 `application/wasm*`） | `encode zstd gzip` |
| nginx | **否**（`gzip_types` 默认只有 `text/html`） | `gzip on; gzip_types application/wasm;`；Brotli 需 ngx_brotli 模块 |
| Vite dev / preview | 否（无内置压缩） | 仅用于局域网测试，不能用来评估公网传输 |

- 未压缩自托管时，客户端会真的传输约 9.4 MB（约 4.7×）；gzip -9 实测 2,857,545 B（约 2.9 MB），Brotli 优于 gzip；
- 无论是否压缩，浏览器解码后的 WASM 都占约 9.4 MB；`fetchAsset` 预取消费 body 时会短暂持有这份内存；
- 自托管响应必须是 `Content-Type: application/wasm`，否则 §5.3.2 的 `fetchAsset` 校验会判定失败（nginx 需确认 `mime.types` 含 `application/wasm wasm;`）；
- `apps/web/meeting/public/vendor/mediapipe/` 约 19 MB，是因为同时放了 SIMD / non-SIMD 两套 WASM；`FilesetResolver` 按浏览器能力二选一，每个客户端只下载一套。

结论：

- 冷启动延迟主要是网络和 WASM 流式编译，不是 GPU；
- 只做资源预加载，预期把首次点击从 **8.1 s** 降到约 **0.3 s**；
- 完整预热可到 **13 ms**，但本期不做。

## 5. 主方案：资源预加载（不 attach）

### 5.1 核心思路

入会后只做三件事：

1. 解析当前浏览器应使用的 MediaPipe WASM 路径；
2. 预取 loader JS + 选中的 WASM binary；
3. 预取 `selfie_segmenter.tflite` 到 HTTP cache。

**不创建 `BackgroundProcessor`，不调用 `setProcessor()`，不初始化 GPU，不使用 Blob URL。**

用户点击 filter 时仍走原有懒加载逻辑，但资源命中缓存，预期点击 → 首帧约 0.3 s。

### 5.2 触发时机

```text
Room connected
  + 页面可见
  + 设备/网络能力允许
  + 延迟 2–3 秒
  + requestIdleCallback（不支持则 setTimeout）
        ↓
预取 loader JS + WASM + model
        ↓
resourcePhase = ready
```

说明：

- 不再要求 camera track live。
- 摄像头关闭时也可以预加载，因为不初始化 processor。
- 如果摄像头已打开且用户已经先点击 filter，走按需加载路径；资源预加载可以继续，但不能重复请求。
- 页面不可见时推迟，回到前台后继续。
- 预加载与 processor attach 完全解耦。

### 5.3 预加载实现

#### 5.3.1 解析 WASM 路径（必须显式传 base）

**不能**这样写：

```ts
// 错误：FilesetResolver 的默认 base 是空字符串，
// 会生成 /vision_wasm_internal.js 这类同源根路径，
// 与 track-processors 使用的 CDN 默认路径不一致。
const fileset = await FilesetResolver.forVisionTasks(undefined)
```

必须显式使用与 processor 完全一致的 base。默认 base 是
`@livekit/track-processors@0.8.1` 内部固定的
`@mediapipe/tasks-vision@0.10.14` CDN 路径：

```ts
import { FilesetResolver } from '@mediapipe/tasks-vision'

// 必须与 @livekit/track-processors 依赖的 tasks-vision 版本保持一致
const DEFAULT_TASKS_VISION_VERSION = '0.10.14'
const DEFAULT_WASM_BASE =
  `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${DEFAULT_TASKS_VISION_VERSION}/wasm`
const DEFAULT_MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite'

const wasmBase = mediapipeAssetBase
  ? `${mediapipeAssetBase}wasm`
  : DEFAULT_WASM_BASE

const fileset = await FilesetResolver.forVisionTasks(wasmBase)
```

> 默认使用 `cdn.jsdelivr.net`。部分网络会重置该 host（实测华为浏览器会报 `ERR_CONNECTION_RESET`，导致背景效果初始化失败）；此时改用自托管。

`fileset` 会给出当前浏览器应使用的：

```ts
fileset.wasmLoaderPath
fileset.wasmBinaryPath
```

**关键规则**：创建 `BackgroundProcessor` 时必须显式传入同一组
`assetPaths`，不要依赖 track-processors 自己的默认值：

```ts
// 只在用户点击 filter 后创建；mode 使用用户选择。
BackgroundProcessor({
  mode: 'background-blur', // 或 'virtual-background'
  assetPaths: {
    tasksVisionFileSet: wasmBase,
    modelAssetPath: modelUrl,
  },
})
```

否则预加载的 URL 和 processor 实际使用的 URL 可能不一致，缓存完全不命中。

另外需要把 `@mediapipe/tasks-vision` 加为 **直接依赖**，固定为
`0.10.14`（与 `@livekit/track-processors@0.8.1` 的依赖一致），不要依赖
transitive dependency。升级 `@livekit/track-processors` 时必须同步检查
`DEFAULT_TASKS_VISION_VERSION` 和 `DEFAULT_MODEL_URL`。

#### 5.3.2 预取 loader JS、WASM 和 model

```ts
async function fetchAsset(url: string) {
  const response = await fetch(url, {
    cache: 'force-cache',
    credentials: 'omit',
  })
  if (!response.ok) {
    throw new Error(`asset fetch failed: ${response.status} ${url}`)
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (url.endsWith('.wasm') && !contentType.includes('application/wasm')) {
    throw new Error(`unexpected wasm content-type: ${contentType}`)
  }
  if (contentType.includes('text/html')) {
    throw new Error(`asset response looks like HTML: ${url}`)
  }
  // 必须消费 body，否则浏览器可能不会完整下载/写入 HTTP cache。
  // arrayBuffer 只用于预热，不保留引用。
  await response.arrayBuffer()
  return response
}

const modelUrl = mediapipeAssetBase
  ? `${mediapipeAssetBase}selfie_segmenter.tflite`
  : DEFAULT_MODEL_URL

const results = await Promise.allSettled([
  fetchAsset(fileset.wasmLoaderPath),
  fetchAsset(fileset.wasmBinaryPath),
  fetchAsset(modelUrl),
])
```

说明：

- 只下载，不编译、不初始化 GPU；
- 后续 `processor.init()` 预期命中 HTTP 缓存；
- 模型不使用 Blob URL；创建 processor 时直接使用 `modelUrl`，依赖 HTTP cache；
- loader JS 由 MediaPipe 通过 script/worker 加载，`fetch` 预取是否被复用需要
  实测；如果发现加载器仍重新下载，可追加
  `<link rel="preload" as="script" href=...>`（同源/带 crossorigin）作为补充；
- 任一项失败时记录 `partialFailure`：loader/WASM 都失败则 `resourcePhase=error`；
  仅 model 失败则 `resourcePhase=partial`，processor 回退远程 model；
- 跨域资源需要允许 CORS；jsDelivr 和 Google Storage 当前可用；
- **CSP 影响**：预取使用 `fetch`，因此 demo CDN 模式下 `connect-src` 必须包含
  `https://cdn.jsdelivr.net` 和 `https://storage.googleapis.com`；
  只配置 `script-src`/`worker-src` 不够。自托管时 `connect-src 'self'`。
- 不使用 Blob URL，因此不需要在 CSP 中增加 `blob:`。
- 自托管时必须给 WASM/loader/model 配置长缓存（例如 `/meeting/vendor/*` 设
  `Cache-Control: public, max-age=31536000, immutable`），否则预加载不会稳定命中
  HTTP cache；同时确认响应 `Content-Type` 为 `application/wasm` 且服务器开启压缩
  （未压缩时 WASM 传输约 9.4 MB，Brotli/zstd 后约 2.0 MB，见 §4.3）。
- 自托管部署时把 WASM + model 放到 `apps/web/meeting/public/vendor/mediapipe/`；`config.example.json` 默认走 CDN，本地 `public/config.json` 可覆写为 `/meeting/vendor/mediapipe/`。
- **能力门控**：启动 filter 前按 MediaPipe `SegmentationPostprocessorGl::GlInit` 的实际条件检查（`mediapipe/tasks/cc/vision/image_segmenter/calculators/segmentation_postprocessor_gl.cc`）：
  - float32 路径：`EXT_color_buffer_float` + `OES_texture_float_linear` + `EXT_float_blend`；
  - half-float 回退：**只要求 `EXT_color_buffer_half_float`**；
  - 两者满足其一即可。
  注意 half-float 线性过滤在 WebGL2 是核心能力，`OES_texture_half_float_linear` 是 WebGL1 扩展、WebGL2 不会暴露；此前门控额外要求该扩展，导致 iPad Safari 被误判为不支持（该设备此前实测 filter 可用）。iPadOS 上 `EXT_float_blend` 的暴露率约一半（Web3D Survey ~49%），不少 iPad 只能走 half-float 回退，因此不能把 half 路径绑在 WebGL1 扩展上。
  Huawei Browser 14（Chromium 99）实测 `EXT_color_buffer_half_float`、`OES_texture_float_linear`、`OES_texture_half_float_linear` 均缺失，静态门控拒绝，不创建 processor。Mali-G76 的 Chrome 158 有 `EXT_color_buffer_half_float`，会通过门控；生产构建实测首次开启 Blur 后 4–8fps 持续约 20s（processing 30–85ms），随后 processor 的 WebGL context 丢失并以 30fps 持续输出黑帧。§5.7 的 context-loss 监听会停止 processor、回 None、恢复原始画面；首屏慢的问题需要性能守卫（本期未实现）。同一 Chrome 版本在 Adreno 740 上 F32 路径完整（`EXT_color_buffer_float` + `OES_texture_float_linear` + `EXT_float_blend`），Blur/图片背景/None 均实测通过。

### 5.4 设备与网络能力门控

资源预加载的主要成本是**网络流量**，不会初始化 GPU。

| 条件 | 行为 |
|------|------|
| `enableBackgroundEffects=false` | 不预加载 |
| `enableSettingsMenu=false` | 不预加载（没有可用的背景 UI） |
| `supportsBackgroundProcessors()=false` | 不预加载；背景控件仍显示，点击时提示 unsupported |
| `navigator.connection.saveData=true` | 不预加载 |
| `effectiveType` 为 `slow-2g` / `2g` / `3g` | 不预加载 |
| `hardwareConcurrency < 4` | 不预加载，节省低端机流量 |
| `deviceMemory < 4`（支持的浏览器） | 不预加载 |
| 页面不可见 | 推迟 |
| 摄像头关闭 | 仍然允许资源预加载 |

低端设备不是禁用背景功能，只是**不做预加载**；用户点击时仍按需加载，并显示 loading。

### 5.5 摄像头开关与 filter 按钮

- 摄像头关闭（`isCameraEnabled=false`）时：
  - 所有非 None filter 卡片 **disabled**；
  - 显示提示：`Turn on camera to use background effects.`；
  - None 可以保持可点击；
  - 资源预加载照常进行。
- 摄像头打开后，filter 按钮恢复可用。
- 摄像头在使用 filter 期间关闭：
  - 已选择的 filter 保持；
  - 按钮禁用；
  - processor 仍在 muted track 上（LiveKit 关闭摄像头是 mute，不是 unpublish）；
  - 重新打开摄像头后效果恢复。
- 摄像头关闭时不记录 pending filter，因为按钮不可点击。

### 5.6 用户点击流程

| 当前状态 | 用户点击 filter 后的行为 |
|----------|--------------------------|
| 摄像头关闭 | 按钮不可点击；显示开启摄像头提示 |
| 资源已 preload | 创建 processor；显示 loading，资源命中缓存，约 0.3 s 后生效 |
| 资源 preload 中 | 记录 `pendingMode`，显示 loading；等待共享 preloadPromise（带超时） |
| 未启用预加载 | 启动按需加载，显示 loading |
| preload 失败 | 直接使用远程 URL 创建 processor；仍失败则 toast + 重置 None |

等待 preload 时必须有超时/回退，不能让用户无限等待：

- 最多等待 10–15 秒；
- 超时后不再等待，直接用远程 URL 创建 processor；
- 预加载失败/超时都不应阻塞用户选择，只在最终 processor 初始化失败时 toast。

即使资源已预加载，仍建议显示 loading，因为实测：

- `setProcessor` / processor ready 约 115 ms；
- 首帧分割约 190 ms；
- 合计约 305 ms。

加载中用户点击 **None**：

- 取消待应用目标；
- 隐藏 loading；
- 如果 processor 已创建，调用 `LocalVideoTrack.stopProcessor()` 销毁它并恢复原始摄像头 track（真正停止处理，见 §5.7）；
- 资源预加载可以继续，但不会创建新的 processor。

加载中用户切换其他 filter：

- 只保留最后一次选择（latest wins）。

### 5.7 状态模型

资源预加载和 processor 生命周期解耦：

```ts
type ResourcePhase =
  | 'idle'
  | 'preloading'
  | 'ready'
  | 'partial'
  | 'error'

type EffectPhase =
  | 'none'
  | 'loading'
  | 'active'
  | 'error'
```

关键规则：

- 同一个 `preloadPromise` 只存在一个，避免重复请求；
- 资源预加载不需要 attach processor；
- processor 按需创建；在 Blur / 图片背景之间复用同一实例 + `switchTo()`；
- **None 必须 `stopProcessor()`**：`@livekit/track-processors@0.8.1` 的
  `switchTo({ mode: 'disabled' })` 不会停止分割/模糊（`isDisabled` 初始为
  false，`??` 不会回退到 `backgroundDisabled`，且 `blurRadius` 仍保留），
  必须销毁 processor 并恢复原始 track；下次选 filter 时重建；
- **Safari/WebKit 重新挂载 workaround**：`stopProcessor()` 后 LiveKit 在同一个
  MediaStream 内把已停止的 processed track 换回原始 track；Safari 不会重绘，本地
  预览黑屏（Chrome/Firefox 正常）。停止后需要把命中原始 track 的 `<video>`
  的 `srcObject` 置空再重新赋值并 `play()`，强制重绘；
- **WebGL context loss**：监听 processor canvas 的 `webglcontextlost`，触发时
  停止 processor、回 None、toast；track-processors 不会自行恢复，否则会持续输出黑帧；
- 组件卸载/离开会议时忽略迟到的 preload/processor promise；
- 摄像头关闭时不发起新的 filter 请求；已经进行中的 processor 请求可以完成，
  但 UI 保持 cameraRequired，不进入 active；重新打开摄像头后再恢复。

### 5.8 UI 与文案

在背景效果区域显示：

```text
[None] [Blur thumbnail] [Desk thumbnail] [Nature thumbnail] [config thumbnails...]

// 资源加载中且用户已选择：
[spinner] Loading filter model...

// 摄像头关闭：
Turn on camera to use background effects.
```

- None 保持文字按钮；Blur 使用 96×54 缩略图卡片，文字居中；Desk / Nature / Lake / Flower / config 图片只显示缩略图，不显示文字（label 仅用于 `aria-label`/`title`）；
- Blur 卡片使用 `src/assets/backgrounds/blur.svg` 静态效果图；Desk/Nature/Lake/Flower 使用照片 WebP + 独立缩略图（`desk.webp`/`desk-thumb.webp`、`nature.webp`/`nature-thumb.webp`、`lake.webp`/`lake-thumb.webp`、`flower.webp`/`flower-thumb.webp`）；
- 摄像头关闭时禁用所有非 None 卡片；
- loading 提示位于 `Background effects` 标题下方；使用 `role="status"` + `aria-live="polite"`；
- 加载期间禁用所有非 None 卡片，避免重复选择；
- **None 保持可点击**，用于取消；
- 选中态按钮保持 pressed，提示用户当前选择；
- 加载失败时使用现有 `backgroundEffectError` toast，并重置到 None。

文案（当前 UI 为英文）：

```ts
backgroundLoading: 'Loading filter model...'
backgroundCameraRequired: 'Turn on camera to use background effects.'
```

不引入中文文案；后续需要时统一纳入 i18n。

### 5.9 配置

```json
{
  "backgroundEffectsPreload": "auto",
  "backgroundImages": [
    {
      "label": "Office",
      "image": "backgrounds/office.jpg",
      "thumbnail": "backgrounds/office-thumb.jpg"
    }
  ]
}
```

类型：

```ts
type BackgroundEffectsPreload = 'auto' | 'off'

type BackgroundImageConfig = {
  label: string
  image: string
  thumbnail?: string
}
```

- `auto`：默认值，按设备/网络能力决定是否预加载；
- `off`：完全关闭资源预加载，恢复纯按需加载；
- `backgroundImages`：默认 `[]`，追加在内置 Desk/Nature/Lake/Flower 之后；本地文件放 `public/backgrounds/`，相对路径基于 `BASE_URL` 解析；remote 需 CORS，`thumbnail` 缺省使用 `image`。详见主设计文档 §3.8.6。

### 5.10 建议的代码结构

建议新增资源加载模块：

```ts
// backgroundAssetLoader.ts
type BackgroundAssets = {
  wasmBase: string
  modelUrl: string
  fileset?: WasmFileset
  partialFailure?: boolean
}

async function preloadBackgroundAssets(options: {
  mediapipeAssetBase: string
  signal?: AbortSignal
}): Promise<BackgroundAssets>
```

职责：

- `FilesetResolver.forVisionTasks(wasmBase)` 解析路径；
- fetch loader JS / WASM / model（消费 body）；
- 失败时返回可回退的远程 URL；
- `AbortSignal` 支持离开会议时取消；
- 不创建 Blob URL。

再抽一个 hook：

```ts
const {
  backgroundType,
  selectBackground,
  resourcePhase,
  loading,
  cameraRequired,
  error,
} = useBackgroundEffects({
  enabled: enableBackgroundEffects,
  mediapipeAssetBase,
  cameraTrack,
  isCameraEnabled,
  localParticipant,
  preload: config.backgroundEffectsPreload,
})
```

职责：

- 资源预加载调度；
- processor 按需创建、attach、switchTo；
- reconnect / track 替换后的幂等重挂；
- pending mode、loading、cameraRequired 状态；
- 错误与重试。

`CameraSettings` 只负责渲染。

### 5.11 日志与指标

建议记录：

- `preload-start` / `preload-ready` / `preload-failed` / `preload-partial`；
- 各项资源 duration / transferSize / cache hit；
- `filter-click`；
- `processor-ready` / `first-frame` / `effect-applied`；
- 点击到首帧耗时。

可使用 `performance.mark()` / `performance.measure()`，开发阶段输出 `console.debug`。正式指标上报不在本期范围。

## 6. 方案对比

| 方案 | 点击后延迟 | 摄像头依赖 | 额外开销 | 结论 |
|------|------------|------------|----------|------|
| 不预加载 | ~8.1 s | 否 | 无 | 现状 |
| **资源预加载（本方案）** | **~0.3 s** | **否** | 约 2.3 MB 下载 | **采用** |
| 完整预热（attach disabled processor） | ~13 ms | 是 | 下载 + GPU 初始化 + passthrough + track 替换 | 本期不做 |
| 保持现状，只加 loading UI | 延迟不变 | 否 | 无 | 不满足目标 |
| 服务端虚化 | 客户端无负担 | 否 | 需要 Egress/服务端视频链 | 不采用 |

## 7. 边界情况

- **摄像头关闭**：资源预加载照常进行；filter 按钮禁用并显示提示；不创建 processor。
- **使用 filter 时关闭摄像头**：filter 保持选中，按钮禁用；processor 留在 muted track；重新打开摄像头后恢复。
- **摄像头重新发布 / full reconnect**：不影响资源预加载；processor 的正常重挂逻辑继续使用 `LocalTrackPublished`。
- **用户在加载中离开会议**：abort preload、忽略迟到的 promise。
- **preload 超时后点击**：processor 已用远程 URL 启动，迟到完成的 preload 结果应被丢弃。
- **模型/loader 请求失败**：后台预加载静默失败；用户点击时回退到远程 URL，仍失败则 toast + None。
- **快速连续点击**：只保留最后一次选择。
- **点击 None**：`stopProcessor()` 销毁 processor 并恢复原始摄像头 track；之后 `cameraTrack.getProcessor()` 为 undefined，不再有后台分割/模糊开销。Safari/WebKit 需要额外重置本地预览 `<video>` 的 `srcObject`，否则同一个 MediaStream 内换回原始 track 后画面保持黑屏（Chrome/Firefox 不需要）。
- **WebGL context loss**：track-processors 的 canvas context 丢失后不会自行恢复，会以 30fps 持续输出黑帧；监听 `webglcontextlost`，停止 processor、回 None、toast，并恢复原始画面。Mali-G76 实测开启 Blur 约 20s 后会发生。
- **Settings 关闭后重新打开**：组件常驻，ResourcePhase 和 EffectPhase 应保持。
- **页面不可见**：推迟预加载；返回前台后继续。
- **跨域/CORS**：fetch loader/WASM/model 需要 CORS；失败时回退到 processor 原生加载路径。
- **模型缓存过期**：模型只有 `max-age=3600`；超过后 processor 可能重新请求，但只在用户点击时发生，loading UI 覆盖。

## 8. 测试与验收

### 自动化

- 纯函数测试：`shouldPreloadBackgroundEffects` 的 saveData、弱网、低核数、低内存分支。
- Playwright：
  1. 入会后延迟一段时间才出现 MediaPipe loader/WASM/model 请求，且不需要摄像头；
  2. 预加载完成后、点击前，`cameraTrack.getProcessor()` 仍为 undefined，且没有新增 video/processor（确认“不 attach”）；
  3. 预加载完成后点击 Blur，显示 loading，约 0.3 s 内出现效果；
  4. 用 route 延迟资源请求，加载中点击 Blur，显示 `Loading filter model...`；
  5. 加载中/已生效后点击 None，最终保持无背景，且 `cameraTrack.getProcessor()` 为 undefined；
  6. 模拟 `hardwareConcurrency=2` / `saveData`：入会不预加载，点击后才加载；
  7. 资源请求失败：点击时回退；仍失败则 toast + 回到 None；
  8. 摄像头关闭时预加载仍发生，但所有非 None filter 卡片 disabled；打开摄像头后恢复可点击；
  9. full reconnect 后背景效果仍可用；
  10. 强制触发 processor canvas 的 `webglcontextlost`：处理器停止、UI 回 None、原始画面恢复，不出现持续黑帧。
- 资源断言：
  - 预加载请求使用显式 `DEFAULT_WASM_BASE`，不是同源 `/vision_wasm_internal.js`；
  - preload 请求确实被后续 processor 复用（资源缓存命中 / transferSize 0）；
  - processor 的 `assetPaths.modelAssetPath` 是普通 HTTPS URL，不是 `blob:`。

### 手测

- iPhone/iPad、Android 低端机、桌面 Chrome/Safari；
- 入会过程中音视频首帧是否受影响；
- 弱网/省流量模式下不预加载；
- 摄像头关闭/打开时 filter 按钮状态；
- 内存/流量是否可接受。

### 验收标准

- 能力允许的设备：入会后资源预加载完成；点击 filter 约 0.3 s 内生效。
- 未完成预加载时点击：立即显示 loading 提示，完成后自动应用。
- 低端/省流量设备：不预加载，点击后才加载。
- 摄像头关闭时 filter 按钮 disabled，并显示提示；预加载仍继续。
- 预加载失败不影响会议，用户可回退/重试。
- 点击 None 后 processor 被销毁，`getProcessor()` 为 undefined，原始摄像头 track 恢复。
- Safari（Mac / iPad / iPhone）Blur→None 后本地预览恢复正常，不出现黑屏。
- WebGL context loss 后自动停止处理并恢复原始画面，不出现持续黑帧。
- 不因预加载产生 processor、GPU 或 sender track 的额外开销。
- 不使用 Blob URL，不要求 CSP 放开 `blob:`。

## 9. 已确认决策汇总

1. `backgroundEffectsPreload` 默认 `auto`。
2. 不采用模型 Blob URL；模型走普通 HTTP 缓存。
3. 摄像头关闭时禁用所有非 None filter，不允许点击；显示开启摄像头提示。
4. 完整预热本期不做。
5. 加载文案使用英文 `Loading filter model...`。
6. 预加载延迟 2–3 秒；点击时最多等待 preload 10–15 秒后回退。
7. 设备门控按推荐值：`saveData`、2G/3G、`hardwareConcurrency < 4`、`deviceMemory < 4` 时跳过预加载。
8. 不使用服务端虚化，不改变模型供应商。

## 10. 实施顺序

1. 新增 `backgroundEffectsPreload` 配置与校验、单测。
2. 将 `@mediapipe/tasks-vision` 固定为直接依赖 `0.10.14`，与 `@livekit/track-processors@0.8.1` 的依赖保持一致。
3. 新增 `backgroundAssetLoader`：FilesetResolver + fetch loader/WASM/model；默认 CDN 必须显式传 `DEFAULT_WASM_BASE`，不能传 `undefined`；不使用 Blob URL。
4. 抽出 `useBackgroundEffects`：ResourcePhase / EffectPhase、pending mode、loading、cameraRequired、错误回退。
5. `CameraSettings` 接入 loading UI、摄像头关闭禁用 filter、开启摄像头提示。
6. Playwright 覆盖预加载、slow-model、低端设备、失败回退、摄像头关闭场景、无 attach 断言。
7. 更新 `webmeeting_design.md`、`webmeeting_impl_plan.md` 和浏览器矩阵。

## 11. 修订记录

- 2026-11-13：初稿，提出入会后异步预热、能力门控、loading 提示与待 review 决策。
- 2026-11-13：实测冷启动约 8.1 s、热缓存约 0.3 s；方案 v2 改为只做资源预加载、不 attach processor。
- 2026-11-13：决策确认：`backgroundEffectsPreload` 默认 `auto`；不采用模型 Blob URL；摄像头关闭时禁用 filter；完整预热本期不做；补充显式 WASM base、fetch body 消费、CSP、超时回退和“不 attach”验收项。
- 2026-11-13：实施完成（P0/P1/P2）；Chrome 桌面主要 E2E 通过；慢网/移动端手测待补。见 [`webmeeting_background_filter_impl_plan.md`](webmeeting_background_filter_impl_plan.md)。
- 2026-11-13：Android Chrome 158.0.8127.36 实测：Mali-G76 缺少 float/half-float linear，点击 filter 显示 unsupported；Adreno 740 支持 float linear，自托管资源预加载后 Blur、Lake、None 均正常；确认门控应按 GPU 能力而非浏览器版本判断。
- 2026-11-14：澄清 §4.3 的传输体积是 CDN Brotli 压缩后大小（解码后约 9.4 MB）；补充自托管时各服务器的压缩配置、`application/wasm` content-type 校验和 `/meeting/vendor/*` 长缓存要求。
- 2026-11-14：修正 §5.3.2 GPU 能力门控：按 MediaPipe `SegmentationPostprocessorGl::GlInit` 的实际条件，half-float 回退只要求 `EXT_color_buffer_half_float`，不再要求 WebGL1 的 `OES_texture_half_float_linear`；修复 iPad Safari 被误判为不支持（此前实测 filter 可用）。Mali-G76 的运行时行为需复测。
- 2026-11-14：修正 processor 生命周期：None 改为 `stopProcessor()` 销毁并恢复原始 track（`@livekit/track-processors@0.8.1` 的 `switchTo({ mode: 'disabled' })` 不会停止分割/模糊）；新增 WebGL context loss 监听与恢复。Mali-G76 生产构建实测开启 Blur 约 20s 后 context loss，原实现会持续输出黑帧；修复后自动停止并恢复画面。
- 2026-11-14：修复 Safari（Mac/iPad/iPhone）Blur→None 后本地预览黑屏：`stopProcessor()` 会在同一个 MediaStream 内换回原始 track，Safari 不重绘；停止后重置命中原始 track 的 `<video>` 的 `srcObject` 并重新 `play()`。Chrome/Firefox 不受影响。
