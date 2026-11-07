# Android Timeline 图片画廊 — 现状实现

> 本文按当前代码整理，只描述已实现行为，不记录设计演进与修订历史。
> **范围**：服务端选图预计算与 BFF 出口；Android 时间线卡片照片、Gallery 屏、全屏查看器、保存到相册、编辑模式批量删除。
> **不在范围**：Web 端渲染、长按多选、服务端缩略图端点、period_summary 联动。

---

## 1. 链路总览

```
lvdata-mcp day_stats stage
  └─ derive_images(video events) → day_summary.day_stats.images
       只存帧选择结果（frame_id/ts_ms/event_ref/window_id/device_uuid/rsession_id），不存 media_url

bff GET /api/mylife/overview
  └─ 单表读 day_summary → _to_day_images 组装 media_url，并透出 window_id

Android
  OverviewDayDto.images
    └─ PeriodAggregator：跨日重组 + 轮转选取（cap day3 / week9 / month12 / year15）
        └─ PeriodCard：3 列缩略图 + 计数徽标
            └─ 点击缩略图 → Routes.MYLIFE_GALLERY
                └─ FrameGalleryScreen
                    ├─ GET /api/mylife/timeline?types=video（cursor 分页，40/页）
                    ├─ FrameGrid：3 列网格 + focus 定位 + 编辑模式选择
                    ├─ FrameViewerOverlay：全屏浏览 + 保存到相册
                    └─ 编辑模式 → POST /api/mylife/events/remove
```

Gallery 复用既有 `/timeline` 端点，没有新增 Gallery 专用端点；依赖均为已有库（Coil 2.6.0、Compose Foundation Pager、OkHttp 4.12.0、`ImageViewerMath`）。

---

## 2. 服务端数据与选图

### 2.1 关键帧事件

- `video` 事件等于 filter 判定的关键帧（`key_frame=true`；同一场景最多一帧；质量守卫、同窗同 `scene_id` 抑制见 `docs/video_analysis.md`）。
- 每个关键帧写一条事件，`event_ref = "{rsession_id}:video:{frame_id}"`；payload 含 `frame_id / window_id / caption / place / objects / ocr / people`。
- events 表主要字段：`event_id / event_ref（唯一）/ device_uuid / rsession_id / type / ts_ms / ts_utc / source / summary / payload`，索引包括 `idx_events_ts / idx_events_type / idx_events_rsession_ts / idx_events_updated`。
- `window` 事件另计（同一分析窗口一条，payload 含 `frame_count / key_frame_count`）。
- 帧图媒体 URL 由 BFF 单点组装：

```
/api/mylife/media/{device_uuid}/{rsession_id}/video/{frame_id}.jpg
```

media 端点按 `<data_root>/<device>/<session>/video/<filename>` 定位，校验 device/session UUID、kind、文件名，并防止路径穿越与白名单外设备访问。

### 2.2 `day_stats.images` 记录

每条记录字段：

| 字段 | 含义 |
|---|---|
| `frame_id` | 帧 id |
| `ts_ms` | 帧时间 |
| `event_ref` | 事件唯一键 |
| `window_id` | 窗口归属（可为空） |
| `device_uuid` / `rsession_id` | 媒体定位信息 |

不存 `media_url`：URL 在 BFF 出口组装，避免预计算数据绑定 host/端口。

### 2.3 选图算法（服务端预计算与客户端重建同规则）

输入：候选关键帧（`frame_id`、`rsession_id` 非空）+ 上限 N。

1. 分组：键 = `window_id`（空或缺失 → `event_ref`）；组内按 `ts_ms` 稳定升序。
2. 窗口序 =（各窗口首帧 `ts_ms`，分组键）。
3. 轮转：第 r 轮依次访问每个窗口，取该窗口第 r 张未取帧；取满 N 即停；某轮无新增则结束。
4. 输出按 `ts_ms` 稳定升序。

性质：

- 第 1 轮保证每个窗口至少一张，窗口数少时后续轮次补齐；
- 窗口数 > N 时尾序窗口不进结果；
- 在单日输入上，每个被选窗口的帧集合是该窗口 `ts_ms` 升序的前缀；服务端按日选图，客户端跨日重建是在逐日池拼接后的形状上重跑同一规则，等价性由服务端冻结基线与客户端“重建等价性”用例共同钉住。

服务端池上限：`DAY_IMAGE_POOL = 15`（`tools/lvdata-mcp/src/lvdata_mcp/consts.py`）。`derive_images` 只对 `frame_id` 与 `rsession_id` 非空的 video 事件选图。

### 2.4 day_stats 预计算 stage

`tools/lvdata-mcp/src/lvdata_mcp/summary/day_stats.py`：

- 常跑组、无 LLM、无外部调用；
- 目标时区 = 活跃时区集合；
- 候选日 = `events.updated_at > last_scan_at` 对应的本地日；日窗查询带 `updated_at <= T_scan` 快照上界；
- 已有 `day_summary` 行时只更新 `day_stats` 列，不碰 status/doc/model/generated_at/fail_count；
- 行不存在时插入 pending 占位行；若当日事件全部是被 LLM 输入去重的 audio，则不落新行；
- 每个时区处理完成后推进游标；整轮异常不提交；
- 删除接口使用 `recompute_days` 按指定 `(timezone, local_date)` 重算，与 stage 游标解耦。

`day_stats` JSON 结构（`counts` 只包含当日出现过的类型）：

```json
{
  "schema_version": 1,
  "computed_at": "...",
  "counts": {"video": 16, "audio": 3},
  "hot_slots": [...],
  "images": [...]
}
```

### 2.5 BFF overview 出口

`services/bff/timeline/service.py` 的 `build_overview`：

- 单表读 `day_summary`，不逐日扫 events；
- `counts/hot_slots/images` 取自 `day_stats`（全设备合并口径）；
- `summary/topics` 仅在 `status='ok'` 时透出；
- `has_pending` 由 status/fail_count 派生；
- 支持 `limit/cursor` 的 keyset 分页（`cursor` = 上一页最后一天，严格更早），`total` 为总天数；
- `device_id` 参数保留但 overview 不再按设备过滤。

`_to_day_images` 负责：

- 把 `day_stats.images` 转成 `DayImage`；
- 用 `device_uuid/rsession_id/frame_id` 组装 `media_url`；
- 透出 `window_id`；
- 坏条目逐条丢弃，不整体 500。

`services/bff/common/schemas.py` 的 `DayImage` 字段：`frame_id / ts_ms / event_ref / media_url / window_id?`。

Android 侧不直接消费 BFF 的相对路径：`MylifeRepository.applyOverviewPage` 对 `overview.images[*].media_url` 统一 `absolutize()`（基址 + `token`），卡片拿到的 `DayImageDto.mediaUrl` 已是可加载的绝对 URL。

### 2.6 gzip

`services/app/application.py` 注册：

```python
GZipMiddleware(minimum_size=1024)
```

- 只压缩 ≥1024 字节且不在排除表中的响应；Gallery/overview 场景即 JSON；Starlette 默认排除 `image/*`、`audio/*`、`video/*` 与 206 分片响应；
- media/Range 路径不受影响；
- Web `apps/web/mylife` 不消费 `overview.images`，池大小变化无 Web 渲染影响。

---

## 3. Android 卡片缩略图

### 3.1 聚合与选图

`PeriodAggregator` 在客户端对 overview 的日粒度图做跨日重建：

| 粒度 | 上限 |
|---|---|
| DAY | 3 |
| WEEK | 9 |
| MONTH | 12 |
| YEAR | 15 |

- 候选 = 组内各天 `OverviewDayDto.images`（已是服务端按日选出的池，最多 15/天）。
- 重跑 §2.3 的轮转规则；分组键 = `windowId ?: eventRef`，空串 `windowId` 等价缺失。
- 输出 `PeriodImage(url / tsMs / eventRef / frameId / windowId?)`，按 `ts_ms` 升序。
- 池 15 ≥ 最大 cap 15，因此在单日关键帧足够时任意粒度都可由单日喂满；单日关键帧不足 cap 时返回实际帧数。
- `PeriodDigest.imageTotal` = 组内各天 `counts["video"]` 之和（video 事件总数），用于计数徽标；与 Gallery 的 `total` 同为 video 事件计数口径。
- **设备口径差异**：overview 的 `day_stats` 是全设备合并；Gallery 的 `/timeline` 按设备白名单可见性过滤。若数据库中存在白名单之外的设备，卡片的 `imageTotal`/缩略图可能与 Gallery 不一致。

`PeriodDigest.dateRange()` 生成 Gallery 请求范围：

| 粒度 | 范围 |
|---|---|
| DAY | key..key |
| WEEK | ISO 周周一..周日 |
| MONTH | 当月 1 日..月末 |
| YEAR | 当年 1 月 1 日..12 月 31 日 |

`key` 形状异常时返回 `null`，调用方跳过导航。

### 3.2 卡片 UI

`PeriodCard` 图片区：

- `digest.images.chunked(3)` 行渲染，3 列等宽方图，`aspectRatio(1f)`、`ContentScale.Crop`、圆角 8dp、间距 `Spacing.xs`；
- 占位/错误态 = `ColorPainter(MylifeColors.Panel)`；
- 单击图片 → `TimelineViewModel.onImageClick(image, digest)` → 发出 `GalleryNavigation`；
- 计数徽标：`imageTotal > images.size` 时，首行最后一张右上角叠 `Black@0.6` 胶囊 + `Star` 数字（`labelSmall`，横 6dp / 竖 1dp 内边距），`offset(x = +10.dp, y = -10.dp)`；
- 显示角标时图片区上方额外补 10dp；
- 角标整体可点，点击同样进入 Gallery；
- 角标数字 = 组内 video 事件总数；等于图片张数时不显示。

`GalleryNavigation` 字段：`from / to / label / focusFrameId / pending`。

- `label` = 卡片 `rangeLabel`（随 UI locale 本地化）；
- `focusFrameId` = 被点帧的 `frame_id`；
- `pending` = `digest.hasPending`。

卡片待分析态（`hasPending`）在卡片顶部显示脉冲点 + 本地化「分析中」文案，与 Gallery 顶栏的 pending 文案同语义。

---

## 4. Gallery 路由与导航

### 4.1 路由

`ui/navigation/Routes.kt`：

```kotlin
const val MYLIFE_GALLERY =
    "mylife/gallery?from={from}&to={to}&label={label}&focus={focus}&pending={pending}"

fun mylifeGallery(
    from: String,
    to: String,
    label: String,
    focusFrameId: String? = null,
    pending: Boolean = false,
) = "mylife/gallery?from=$from&to=$to&label=${Uri.encode(label)}" +
    "&focus=${focusFrameId.orEmpty()}&pending=$pending"
```

`NavGraph` 中所有 Gallery 参数均为 `StringType`、nullable、默认 null：

- `focus` 空串 → null（`takeIf { it.isNotEmpty() }`）；
- `pending` 用 `toBooleanStrictOrNull() ?: false`；
- `from` / `to` 为本地日 `YYYY-MM-DD`（闭区间）；
- `label` 为展示文案，已 URL 编码。

### 4.2 导航链路

```
TimelineScreen 卡片点击
  → TimelineViewModel.onImageClick(image, digest)
  → galleryNavigation（SharedFlow）
  → TimelineScreen.collect
  → MylifeHomeScreen.onNavigateToGallery
  → NavGraph.push(Routes.mylifeGallery(...))
```

Gallery 是 App 级路由，独立全屏，返回键回到时间线卡片列表。

### 4.3 参数语义

- **范围**：from/to = 被点卡片所在粒度的时间范围。
- **label**：卡片范围文案，用于 Gallery 顶栏。
- **focus**：被点帧 id。首屏加载完成后命中则 `scrollToItem` 并金色描边高亮 2 秒；未命中（帧在后续页）保持停在顶部，不做“翻页直到找到”，避免大 period 下无界请求。
- **pending**：卡片进入 Gallery 时的待分析快照，用于顶栏「分析中…」。
- **时区**：Gallery 请求携带设备时区 `TimeZone.getDefault().id`，与 overview 同源。

---

## 5. Gallery 屏

### 5.1 界面结构

`ui/screens/mylife/gallery/FrameGalleryScreen.kt`：

- 独立路由，不经过 `MylifeHomeScreen`，因此自带 `MylifeTheme` + `MylifeColors.Abyss` 纯色底 + `statusBarsPadding`，不复制星空背景。`MylifeTheme` 同时注入共享 OkHttp 的 Coil `ImageLoader`，自签 TLS 场景下帧图加载依赖该 loader。
- 顶栏三区：
  - 左：返回箭头（Dust）+ `label`（Gold，`titleMedium`，单行 Ellipsis）；
  - 中：`Data` 时显示计数（Dust，`labelMedium`）；`pending` 时显示「分析中… · N 张」；
  - 右：`TextButton`「编辑 / 退出」；`Data` 且帧列表非空时出现（编辑模式下保持显示）；`deleting` 时禁用。
- 主体按 `FrameGalleryState` 分派：
  - `Loading` → 本地 `LoadingHint`；
  - `Empty` → `EmptyState(Icons.Filled.Info, "这段时间还没有画面", "分析完成后会自动出现")`；
  - `Error` → 本地 `ErrorHint` + 重试按钮；
  - `Data` → `FrameGrid`。
- 编辑模式底栏：居中 `OutlinedButton`，红色描边/文字；0 选时禁用；`deleting` 时显示 18dp 转圈 + 「删除中…」并禁用。
- 删除提示：屏幕级底部居中覆盖层（`Black@0.75` + `Star`，圆角 16dp，2 秒 TTL）；编辑模式时上移避开底栏。
- 删除确认：`AlertDialog`，title/正文/按钮均居中；「删除」红色；取消/点外部关闭。
- 用户文案基本走 `stringResource` / `pluralStringResource`；项目当前包含 17 个 locale 资源目录（含 th/vi，见 `docs/android_multilingual.md` §21）。查看器页码 `n / N` 是格式化字符串，数字经 `localizeDigits` 本地化；其余计数/提示按资源与 locale 复数规则解析。

### 5.2 状态模型

```kotlin
sealed interface FrameGalleryState {
    data object Loading
    data object Empty
    data class Error(val message: String)
    data class Data(
        val frames: List<FrameDto>,
        val total: Int,
        val nextCursor: String?,
        val loadingMore: Boolean = false,
    )
}

data class FrameGalleryUiState(
    val state: FrameGalleryState = FrameGalleryState.Loading,
    val viewerIndex: Int? = null,
    val imageSaveTip: String? = null,
    val savingFrameId: String? = null,
    val editMode: Boolean = false,
    val selectedFrameIds: Set<String> = emptySet(),
    val deleteConfirm: Boolean = false,
    val deleting: Boolean = false,
    val deleteTip: String? = null,
)
```

- `viewerIndex`：全屏查看器当前页；null = 关闭（覆盖层非路由）。
- `imageSaveTip` / `deleteTip`：页面级提示，2 秒 TTL。
- `savingFrameId`：保存中按钮转圈与重入守卫。
- `editMode / selectedFrameIds / deleteConfirm / deleting`：编辑模式状态。

### 5.3 网格与分页

`ui/screens/mylife/gallery/components/FrameGrid.kt`：

- 3 列 `LazyVerticalGrid`，方图 `aspectRatio(1f)`、`Crop`、圆角 8dp、间距 `Spacing.xs`；
- item key = `frameId`；
- 单页 40 张（`MylifeRepository.frames` / `MylifeApi.timeline` 默认值）；
- 滚动触底预取：可见最后一项索引 ≥ `frames.size - 6` 时触发 `loadMore`；
- **每个新触发只发一次**：
  - 失败后 `loadingMore` 回落不会立即重发，避免断网重试风暴；
  - 重新武装 = 滚离阈值区再回来，或上一页成功追加（`frames.size` 变化）；
- 加载中底部显示整行 `LoadingHint`；
- 翻页失败静默保留已加载页；
- append 按 `frameId` 去重：`/timeline` 游标是 `{localDate}|{offset}` 偏移语义，分析中插入新帧会导致页间重叠；
- 正常分页期间列表只增不减；查看器页数随列表增长，当前页索引不漂移；删除成功后的 `force` 重载会整体替换列表；
- `Data` 态的 `total` 使用服务端计数；`media_url`/`frameId` 为空的条目不进入列表，因此可见张数可能小于 `total`。

`FrameGalleryViewModel.load`：

- 首次由 Screen 的 `LaunchedEffect(from, to)` 触发；
- 同范围已成功加载且 state 为 `Data` 时跳过重拉；`force = true` 强制重载；
- 首屏失败 → `Error(message)`；
- 删除成功后用 `force = true` 重载。

`FrameGalleryViewModel.loadMore`：

- `nextCursor == null` 或 `loadingMore` 时 no-op；
- 成功追加去重后的帧，并更新 `total / nextCursor / loadingMore`；
- 失败只回落 `loadingMore`，保留已加载页。

### 5.4 focus 定位

`FrameGalleryScreen` 在取得首个 `Data` 状态后消费一次 focus：

- `frames.indexOfFirst { it.frameId == focusFrameId }`；
- 命中 → `gridState.scrollToItem(index)`，设置 `highlightedIndex`；
- 未命中 → 不主动滚动（通常停在顶部；用户已手动滚动则保持当前位置）；
- 高亮由独立 `LaunchedEffect` 在 2 秒后清除，避免 loadMore 等状态变化重启 effect 导致高亮残留；
- 高亮样式：图片边框 2dp `MylifeColors.Gold`。

### 5.5 编辑模式选择框

`FrameGrid` 在 `editMode = true` 时每格右下角绘制选择框：

- 视觉：22dp 方框、圆角 4dp；
- 未选：`White@0.85` 1.5dp 描边 + `Black@0.35` 半透明底；
- 已选：`MylifeColors.Gold` 实底 + `Icons.Filled.Check`（Abyss 色）；
- 无 `eventRef` 的帧：置灰禁用（防御；实际 video 事件 `event_ref` 恒非空）；
- 命中区经内边距外扩至 34dp；选择框 clickable 自消费 tap → `onToggleSelect`，不触发图片点击；
- 图片其余区域仍走 `onFrameClick` 打开查看器；
- 选择框带无障碍语义描述（本地化「选择照片」）。

---

## 6. 全屏查看器

`ui/screens/mylife/gallery/FrameViewerOverlay.kt`，拷贝式复用 conversation 查看器骨架，并直接复用 `ImageViewerMath`（同模块 `internal`）。

### 6.1 结构

- 覆盖层非路由，黑底全屏 Box，盖在 Gallery 之上；
- `HorizontalPager` 页数 = 已加载 `frames.size`；
- 每页 `AsyncImage(model = frame.mediaUrl)`，`ContentScale.Fit`；
- `ImageViewerMath` 提供：`MIN_SCALE = 1`、`MAX_SCALE = 5`、`fitSize`、`clampScale`、`offsetAfterZoom`、`clampOffset`。

### 6.2 手势

- 单击 → 关闭查看器；
- 双指捏合 → 1x–5x 缩放，以手指质心为锚；
- 放大态单指拖动 → 平移，越界夹取；
- fit 态单指横滑 → 交给 `HorizontalPager` 翻页；
- 放大态 `userScrollEnabled = false`，框架层禁止翻页；单击平移消费延迟到超过 touchSlop，保证单击退出可靠；
- 未加载完成或加载失败（`fitSize == Size.Zero`）时不做变换，空白页仍可翻页/单击退出；
- 图片 intrinsic 尺寸由 Coil `SuccessResult.drawable` 的 `intrinsicWidth/Height` 驱动；每页各自 `remember(frameId)`；
- 翻页落定（`settledPage`）后 scale/offset 重置回 fit 态。

### 6.3 叠层信息

- **顶部页码胶囊**：
  - 居中对齐 TopCenter；
  - `Black@0.35` 半透明底；
  - 文案仅 `n / N`，数字按 locale 本地化；
  - `N` = 当前已加载帧数（随分页增长），不是服务端 `total`；
  - `frames.size > 1` 才显示；
  - 不显示时间。
- **底部单一 bar**：
  - `[caption 文字列] + [48dp 保存图标]`；
  - 内容自适应宽度并居中；
  - `Black@0.6` 半透明底，圆角 12dp；无 caption 时用圆形底；
  - caption 文字色 `Star@0.85`，收起态单行省略；
  - bar 是 Pager 兄弟层，绘制在页面之上，点击不退出查看器、不翻页；bar 上的按下不会起手捏合/横向翻页。
- **caption 展开**：
  - caption 不为空且收起态被单行截断（`hasVisualOverflow`）时才可展开；
  - 行尾显示金色 `⌄`（展开态 `⌃`）；
  - 点击 bar 文字区切换展开/收起；展开态多行全显、不设内部滚动；
  - 展开态是本次全屏会话级状态：翻页保持，关闭后重新进入回到收起；
  - caption 为空或单行未截断时点击无展开效果，但事件仍被消费，不会误退出查看器。

### 6.4 查看器内预取

`LaunchedEffect(pagerState.currentPage, frames.size)`：

- `currentPage >= frames.size - 3` 时调用 `onNearEnd` → Gallery 侧 `loadMore`；
- `nextCursor == null` 时 VM 直接 no-op；
- 列表追加后 Pager 页数增长，当前页索引不漂移。

### 6.5 保存到相册

- 保存对象 = 点击瞬间当前页帧的 `frameId`；回调携带 id，VM 按 id 解析，不以页索引为准（权限回调可能晚于翻页）。
- 流程：

```
FrameViewerOverlay.onSaveClick(frameId)
  → FrameGalleryScreen.handleSaveFrame
      → API ≤ 28 且无权限：懒请求 WRITE_EXTERNAL_STORAGE
      → FrameGalleryViewModel.saveFrameById(frameId)
          → MylifeRepository.downloadFrame(mediaUrl)（失败收敛 null）
          → ImageGallerySaver.saveJpegBytes(bytes)
```

- `ImageGallerySaver`：
  - 校验 JPEG magic `FF D8 FF`；
  - API 29+ 走 MediaStore；API ≤ 28 走 legacy 存储；
  - 文件名 `LivaChat_yyyyMMdd_HHmmss_SSS.jpg`；
  - 成功 true，失败 false，不抛异常、不留半成品。
- 保存中：
  - 按钮换 18dp 转圈，不置灰（置灰会造成 tap 穿透误退出）；
  - VM 用 `savingFrameId` 做重入守卫，连点被吞；
  - 关闭查看器会取消在飞保存并清提示；
  - 重开查看器会清旧提示。
- 提示：
  - `Black@0.75` + `Star` 胶囊，显示在 bar 上方；
  - 文案：已保存 / 保存失败 / 保存失败：需要存储权限；
  - 2 秒 TTL，`tipJob` 取消防串台。
- 未实现：双击缩放、隐藏系统栏、分享；保存不读取 Coil 磁盘缓存，始终重新下载。

---

## 7. 数据层与 API

### 7.1 DTO

`data/remote/mylife/dto/MylifeDtos.kt`：

| DTO | 用途 |
|---|---|
| `DayImageDto(frameId, tsMs, eventRef, mediaUrl, windowId?)` | overview 日卡缩略图 |
| `TimelineResponseDto(days, nextCursor, total)` | `/timeline` 响应 |
| `TimelineDayDto(date, events)` | 日分组 |
| `TimelineEventDto(tsMs, type, role, text, summary, agentName, jobKey, mediaUrl, frameId, windowId, caption, eventRef, transcript, audioSummary, durationS, energy, energyV, frameMs, dbMin, dbMax)` | `/timeline` 事件；除 `ts_ms` 外均有默认值 |
| `FrameDto(tsMs, mediaUrl, frameId, windowId?, caption?, eventRef?)` | Gallery 帧模型 |
| `RemoveRequestDto(eventRefs)` / `RemoveResponseDto(...)` | 删除请求/响应 |

JSON 解析开启 `ignoreUnknownKeys = true`。

### 7.2 `MylifeApi`

| 方法 | 行为 |
|---|---|
| `timeline(dateFrom, dateTo, types = "video", limit = 40, cursor, timezone)` | `GET /api/mylife/timeline`，自动附 `token` |
| `downloadBytes(url)` | 下载帧图字节；非 2xx / 响应体缺失 / IO 失败抛异常；协程取消上抛 |
| `removeEvents(eventRefs)` | `POST /api/mylife/events/remove?token=...`，body `{"event_refs":[...]}`；30s 超时派生 client（read/write/call）；错误信封走 `parseError` |

统一错误类型：`MylifeApiException(status, code, message)`。

### 7.3 `MylifeRepository`

| 方法 | 行为 |
|---|---|
| `frames(from, to, cursor = null, limit = 40)` | 调 `/timeline`，flatten `days[].events`，跳过 `mediaUrl` 或 `frameId` 为空的条目，映射 `FrameDto`，按 `tsMs` 升序重排，`mediaUrl.absolutize()` 附 token；返回 `FramePage(frames, total, nextCursor)` |
| `downloadFrame(url)` | 失败收敛 `null`，取消照常上抛 |
| `removeEvents(eventRefs)` | 直通 API，无缓存 |
| `markPendingRemovalRefresh()` | 记录删除后的延迟刷新标记 |
| `flushPendingRemovalRefresh()` | 有标记时清标记，并在 repository 自有 scope 内 `refreshOverview(force = true, limit = 已加载窗口天数)`；异常就地兜底 |
| `refreshOverview(force, limit, cursor)` | overview 分页/轮询入口 |
| `pollWindowDays()` | 当前已加载窗口天数，clamp 到 1..400 |

### 7.4 `/timeline` 分页语义

- 服务端 SQL：`ORDER BY ts_ms, priority, event_id` 升序，`LIMIT/OFFSET` 下推；
- 第 1 页 = 范围内最早帧，逐页向新推进；
- `limit` 默认 200、最大 500；Gallery 固定传 40；
- `cursor` 形态 `{localDate}|{offset}`，实际按 offset 解析；
- 分析管线在任意位置插入新帧会导致页间重叠 → 客户端按 `frameId` 去重；
- `total` = 范围内全部 video 事件数（含后续被客户端跳过的空 `media_url`/`frame_id` 条目）。

---

## 8. 编辑模式与批量删除

### 8.1 UI 与交互

- 顶栏右侧「编辑 / 退出」切换；进入/退出均清空已选。
- 每格右下角选择框（见 §5.5）。
- 底部删除条仅编辑模式显示；0 选禁用。
- 选中上限 = 50；第 51 张不选中，并显示提示「最多选择 50 张」。
- `eventRef` 为空的帧不可选（防御）。
- 编辑模式下图片其余区域仍可进入全屏查看。
- 点「删除 (N)」→ 确认弹窗；取消/点外部关闭弹窗、不发请求、保留选择。
- 确认删除 → 调删除接口。
- 删除中：
  - 底栏转圈 + 「删除中…」；
  - 顶栏返回箭头禁用；
  - 系统返回键被 `BackHandler` 吞掉；
  - 网格图片点击、选择框、退出编辑、再次删除全部 no-op；
  - 全屏查看器不可打开。
- 删除成功：
  - 显示「已删除 N 张」或「N 张已删除，M 张失败」；
  - 自动退出编辑模式并清空选择；
  - 整页强制重载（清列表 → Loading → 首页 40 张）。
- 删除失败：
  - 保留编辑模式与选择；
  - 超时 → 「删除超时，请稍后重试」；其他失败 → 「删除失败」；
  - 服务端幂等，可直接重试。

### 8.2 ViewModel 流程

`FrameGalleryViewModel`：

- `enterEditMode()`：`editMode = true`，清空选择。
- `exitEditMode()`：`deleting == true` 时 no-op；否则关闭编辑模式、清空选择与确认弹窗。
- `toggleSelect(frame)`：非编辑模式 / deleting / 无 `eventRef` → no-op；已选则取消；未选且已满 50 → 提示；否则加入。
- `requestDelete()`：编辑模式、有选择、非 deleting → 打开确认弹窗。
- `dismissDeleteConfirm()`：关闭弹窗。
- `deleteSelected()`：
  1. 先关闭确认弹窗；
  2. guard：非编辑模式 / deleting / 选择为空 / 无可用 `eventRef` → return；
  3. `deleting = true`；
  4. `repository.removeEvents(refs)`；
  5. 成功：`gone = removed + skipped(not_found)`，`failed = 其余 skipped`；提示；直接置 `editMode = false`、清空选择（不调用 `exitEditMode()`，因为 deleting 期间它是 no-op）；`markPendingRemovalRefresh()`；`load(from, to, force = true)`；
  6. 失败：`InterruptedIOException` → 超时文案，其他 → 失败文案；保留选择与编辑模式；
  7. `finally` 落 `deleting = false`。
- `onCleared()`：
  - 调 `repository.flushPendingRemovalRefresh()`；
  - 返回栈弹出才触发，屏幕旋转不触发。

### 8.3 删除接口

**请求**

```
POST /api/mylife/events/remove?token=...
Content-Type: application/json

{"event_refs": ["{rsession_id}:video:{frame_id}", ...]}
```

- 1–50 个非空字符串；
- strict 模式：元素必须是字符串，拒绝隐式类型转换；
- Android 不传 `device_id`，与 overview 一致；
- BFF 先按 `list_sessions(principal.device_id)` 的可见设备预检：不存在或不可见统一 `not_found`，非 video 为 `not_video`，其余才转发 lvdata-mcp。

**响应（200，部分成功语义）**

```json
{
  "schema_version": 1,
  "removed": ["..."],
  "window_events_removed": ["..."],
  "images_removed": ["frame_id"],
  "images_pending": ["frame_id"],
  "days_recomputed": [{"timezone": "Asia/Shanghai", "local_date": "2026-08-21"}],
  "skipped": [{"event_ref": "...", "reason": "not_found | not_video"}],
  "vectors_pending": true
}
```

- `vectors_pending` 仅在为 true 时出现；
- 每个 ref 恰好出现于 `removed` 或 `skipped` 之一；
- `not_found` 视作已不可见（并发已删/重复提交），客户端按已删除处理；
- 重试幂等：已删 ref → `skipped: not_found`；
- 400 仅格式非法（请求体非 JSON 对象 / 空 / 超 50 / 元素非字符串）；401 `unauthorized`（token 校验失败）；
- 502 `upstream_unavailable`；503 `removal_disabled`（BFF 未配置 `lvdata_api_url`）。

**服务端级联**

`tools/lvdata-mcp/src/lvdata_mcp/remove.py`：

- T1 主事务：删除 video events → 删除归零 window events → 删除 `search_docs`（表存在时）→ 重算受影响日的 `day_stats`；
- 删除空日时：`status='ok'` 的行保留并写入空派生；非 ok 行删除；
- T2：向量删除（失败/未完成时 `vectors_pending = true`）；
- 文件面：删除 jpg 与 files 行（未完成时进入 `images_pending`）；
- 重复 ref 先去重（保序），响应中每个 ref 至多出现一次。

### 8.4 卡片刷新时机

- 删除成功时**不在 Gallery 内触发 overview 刷新**，只记标记；
- 有删除标记时，Gallery 关闭（`onCleared`）后由 repository 在自有 scope 内强刷「已加载窗口」；
- **例外**：repository 的 60s pending 轮询与页面无关——若已加载窗口内仍有 `has_pending`，overview 仍可能在 Gallery 停留期间被轮询更新；删除本身不触发画廊内刷新；
- 删除流程本身不重新生成 LLM 日总结，已删除内容可能仍出现在历史 summary 中，客户端不处理。

---

## 9. 边界行为

| 场景 | 行为 |
|---|---|
| period 无关键帧 | Gallery 空态；卡片侧仍由 `hasPending` 脉冲提示兜底 |
| 单帧 | 查看器无页码胶囊；仍可单击退出 |
| 帧数 > 40 | 滚动触底自动加载下一页 |
| 加载中再次触底 | `loadingMore` 互斥，不重复请求 |
| 首屏网络失败 | `ErrorHint` + 重试 |
| 翻页失败 | 静默保留已加载页；滚离阈值区再回来或下一页成功追加后才重试 |
| 帧图 404 / 解码失败 | 网格显示 Panel 灰底；查看器空白页仍可翻页、单击退出 |
| 放大态左右拖动 | 不翻页 |
| 返回键 | 查看器打开时先关查看器；编辑模式先退编辑；删除中吞掉；最后退出 Gallery |
| 跨午夜 / 跨时区 | from/to 用本地日期，请求携带设备时区 |
| 旧数据无 `window_id` | 按 `eventRef` 视作独立窗口 |
| 同一窗口跨日 | 客户端按 `windowId` 跨日归组后统一轮转 |
| 窗口全部取尽仍不足 cap | 返回实际帧数 |
| 当日窗口数 > 池（15） | 轮转取满即停，尾序窗口当日无候选；客户端重跑同一形状结论一致 |
| 多帧同 `ts_ms` | 稳定排序保持轮转序 |
| 分析中插入新帧导致游标页重叠 | 客户端按 `frameId` 去重 |
| `media_url` / `frameId` 为空 | Repository 跳过该条；Gallery `total` 可能大于可见张数 |
| 查看器接近已加载末尾 | 提前 3 页触发 loadMore；无 `nextCursor` 时停住 |
| `focus` 帧不在首屏 | 停在顶部，用户自行下滑 |
| caption 为空 | 底部 bar 退化为圆形保存按钮 |
| caption 单行未截断 | 无法展开；点击被消费、不退出查看器 |
| caption 展开后翻页 | 保持展开；关闭重进回到收起 |
| 保存中再次点击保存 | 重入守卫吞掉 |
| 保存中关闭查看器 | 取消在飞保存并清提示；下载阶段取消不落库，已进入相册写入的极端情况可能仍完成 |
| 帧图显示失败但点保存 | 独立下载仍可能成功，正常入册 |
| 权限回调晚于翻页（API ≤ 28） | 按点击时的 `frameId` 保存该帧 |
| 选满 50 再点第 51 张 | 提示上限，不选中 |
| 跨页选择 | 选择集合跨页保持；只能选择已加载帧 |
| 部分删除成功 | `removed + not_found` 计为已删除；其余计失败；整页重载 |
| 全部 not_found | 视作全部已删；重载 |
| 删除超时 / 断网 / 5xx | 保留选择与编辑模式，可幂等重试 |
| 删除后该 period 无帧 | 重载后进入空态 |
| 删除后旋转屏幕 | `onCleared` 不触发，删除后的强刷延后到退出 Gallery；pending 轮询不受影响 |
| 编辑模式退出 | 清空选择；已选不保留 |
| 查看器内 | 不提供选择/删除，交互与普通浏览一致 |

---

## 10. 测试现状

### 10.1 服务端（pytest）

- `tools/lvdata-mcp/tests/test_day_stats.py`
  - `test_images_pool_cap_and_no_url`：20 帧单窗 → 取 15、ts 升序、不存 `media_url`；
  - `test_equivalence_with_bff_golden`：与冻结基线 `tests/fixtures/day_stats_golden.json` 逐字段等价（counts / hot_slots / images 字段与 media_url 构造规则）；
  - 游标推进、快照上界、幂等、去重空日、固定偏移时区等 stage 用例同文件。
- `services/tests/unit/bff/test_timeline_api.py`
  - overview images 透出（`window_id` 字段、BFF 组装 `media_url`）；
  - overview 分页、时区、pending 派生、timeline 过滤/排序/分页等。
- `services/tests/unit/bff/test_gzip.py`
  - 大 JSON 被 gzip；`image/jpeg` 不被压缩；206 Range 响应不被压缩。
- `services/tests/unit/bff/test_remove_api.py`
  - 删除接口部分成功、错误码、参数校验。

### 10.2 Android JVM

- `PeriodAggregatorTest`：轮转规则、`windowId ?: eventRef` 兜底（null/空串）、cap 3/9/12/15、跨日同窗、同 ts 稳定序、全部取尽不足 cap、服务端池截断下的重建等价性、`imageTotal`、`dateRange()` 四粒度与跨年/闰月边界。
- `TimelineViewModelTest`：图片点击发出 `GalleryNavigation(from, to, label, focusFrameId, pending)`。
- `FrameGalleryViewModelTest`：首屏成功/空/失败重试；分页 append 升序与 `frameId` 去重；翻页互斥；游标耗尽不再请求；失败保留已有页；保存链（成功/失败/权限/重入/未知 id/关闭取消/重开清提示）；编辑模式选择与上限；确认弹窗；删除成功/部分成功/全部 not_found/异常；删除中全拦截；`onCleared` flush。
- `MylifeRepositoryTest`：frames 映射（跳空、升序、绝对化）、请求参数、`downloadFrame` 失败收敛。
- `MylifeApiTest`：timeline、downloadBytes（成功/非 2xx）、removeEvents（POST body/token/错误信封）、cancelAgentJob。
- `ImageGallerySaverTest`：文件名毫秒精度、JPEG magic 校验、非法 magic 在触达相册写入前短路。
- `ImageViewerMathTest`：查看器缩放/平移数学（Gallery 查看器复用同一实现）。

### 10.3 androidTest

- `FrameGridLoadMoreTest`：失败后同一次触底不重复请求；上一页成功追加后恢复续填。
- `FrameGridEditModeTest`：选择框 tap 只触发选择、不打开查看器；图片区域点击打开查看器；非编辑模式无选择框。

---

## 11. 实现位置

| 文件 | 职责 |
|---|---|
| `tools/lvdata-mcp/src/lvdata_mcp/summary/day_stats.py` | `derive_images` 轮转选图；day_stats stage；删除后 `recompute_days` |
| `tools/lvdata-mcp/src/lvdata_mcp/consts.py` | `DAY_IMAGE_POOL = 15` |
| `tools/lvdata-mcp/src/lvdata_mcp/remove.py` | video 事件删除级联（events/window/search_docs/day_stats/vectors/files） |
| `services/bff/common/schemas.py` | `DayImage`、`RemoveRequest`、timeline/overview 响应模型 |
| `services/bff/timeline/service.py` | `build_overview`、`_to_day_images`、`_video_media_url`、`build_timeline` |
| `services/bff/timeline/router.py` | `/overview`、`/timeline` |
| `services/bff/remove/router.py` / `service.py` | `/events/remove` 与上游转发 |
| `services/app/application.py` | `GZipMiddleware(minimum_size=1024)` |
| `data/remote/mylife/dto/MylifeDtos.kt` | `DayImageDto`、timeline DTO、`FrameDto`、删除 DTO |
| `data/remote/mylife/MylifeApi.kt` | `timeline`、`downloadBytes`、`removeEvents`、30s 删除 client |
| `data/repository/MylifeRepository.kt` | `frames`、`downloadFrame`、`removeEvents`、延迟刷新标记 |
| `ui/screens/mylife/timeline/PeriodAggregator.kt` | 云端池的客户端重建、cap、`PeriodImage`、`dateRange()`、`imageTotal` |
| `ui/screens/mylife/timeline/TimelineViewModel.kt` | `GalleryNavigation`、`onImageClick` |
| `ui/screens/mylife/timeline/TimelineScreen.kt` | 导航事件收集、卡片接线 |
| `ui/screens/mylife/timeline/components/PeriodCard.kt` | 3 列缩略图、计数徽标、占位/错误态 |
| `ui/screens/mylife/MylifeHomeScreen.kt` | `onNavigateToGallery` 透传 |
| `ui/navigation/Routes.kt` / `NavGraph.kt` | Gallery 路由、参数解析、宿主接线 |
| `ui/screens/mylife/gallery/FrameGalleryScreen.kt` | Gallery 屏、顶栏、底栏、弹窗、提示、focus、权限 |
| `ui/screens/mylife/gallery/FrameGalleryViewModel.kt` | 分页、查看器索引、保存链、编辑/删除、`onCleared` |
| `ui/screens/mylife/gallery/FrameGalleryUiState.kt` | 状态模型 |
| `ui/screens/mylife/gallery/components/FrameGrid.kt` | 网格、触底预取、选择框、本地 Loading/Error |
| `ui/screens/mylife/gallery/FrameViewerOverlay.kt` | 全屏查看器、手势、caption、保存入口、预取 |
| `media/ImageGallerySaver.kt` | JPEG 字节直存 MediaStore/legacy |
| `ui/screens/conversation/ImageViewerOverlay.kt` | `ImageViewerMath`（Gallery 查看器复用） |

---

## 12. 当前限制与注意事项

1. **网格下载原图**：每格下载整张 JPEG（典型 960×1280 / 55–101KB），40 张/页约 2–4MB；尚无服务端缩略图端点。
2. **focus 降级**：帧在后续页时不主动滚动，停在原位置（通常为顶部）；当前没有按 `frame_id` 跨页定位。
3. **选图规则双实现**：服务端 `derive_images` 与客户端 `PeriodAggregator.rotateSelect` 必须同步修改；等价性由服务端 golden 基线与客户端“重建等价性”用例共同钉住。池大小必须 ≥ 最大 cap（15）。
4. **`ImageViewerMath` 位置**：当前是 conversation 包内的 `internal` object，同模块直接 import；若未来模块化拆分需上移到共享包。
5. **保存未复用 Coil 缓存**：查看器保存会重新下载图片；55–101KB/次，当前接受。
6. **overview 流量**：已有 gzip + 客户端 `limit` 分页；尚未实现 ETag/304。
7. **数据源差异**：卡片图片来自 overview 快照，Gallery 来自实时 `/timeline`；分析中新增的关键帧可能“卡上没有、Gallery 有”。
8. **删除后的日总结**：删除流程本身不重新生成 LLM 日总结，历史 summary 可能仍提及已删除内容；客户端不处理。
9. **删除后卡片刷新**：删除本身不触发画廊内刷新，退出 Gallery 时才强刷已加载窗口；若窗口内仍有 `has_pending`，repository 的 60s 轮询仍可能独立更新 overview。
10. **编辑范围**：仅支持删除 `video` 事件；`not_video` 会出现在 `skipped` 中。
11. **未实现交互**：长按多选、查看器内删除、分享、双击缩放、隐藏系统栏。
12. **Web 不消费 overview images**：池上限变化不影响 Web 渲染。

---

## 相关文档

- `docs/video_analysis.md`：关键帧口径与 scene_id 规则
- `docs/bff_service_interfaces.md`：day_stats 预计算与 overview 分页
- `docs/bff_service_remove.md`：删除接口契约与级联语义
- `docs/android_timeline_audio_gallery.md`：共享的 loadMore 重试语义
