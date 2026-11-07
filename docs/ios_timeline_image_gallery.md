# iOS 时光机图片画廊（Gallery）设计

**状态**：已实现（2026-10-22）
**UI/UX 基线**：`docs/android_timeline_image_gallery.md` 与 Android 现行代码（`FrameGalleryScreen.kt`、`FrameGalleryViewModel.kt`、`FrameGrid.kt`、`FrameViewerOverlay.kt`、`PeriodAggregator.kt`）。
**共享基础**：`docs/ios_timeline_arch.md`。
**范围**：卡片照片区与计数角标、Gallery 屏、全屏查看器、保存到相册、编辑模式批量删除。
**不在范围**：服务端关键帧生产与 day_stats 预计算、长按多选、分享、缩略图端点、period_summary 联动。

---

## 1. 链路总览

```
lvdata-mcp day_stats stage
  → day_summary.day_stats.images（只存帧选择结果，不存 media_url）
  → BFF /overview 组装 media_url + window_id
  → iOS OverviewDay.images
      → PeriodAggregator：跨日轮转选取（cap day3 / week9 / month12 / year15）
          → PeriodCard：按行等分缩略图（每行最多 3 张）+ 图片总数角标
              → 点击 → TimelineRoute.gallery(from,to,label,focusFrameId,pending)
                  → FrameGalleryView
                      ├─ GET /timeline?types=video（cursor 分页，40/页）
                      ├─ FrameGrid：3 列网格 + focus 定位 + 编辑选择
                      ├─ GalleryViewerOverlay：全屏浏览 + 保存
                      └─ 编辑模式 → POST /events/remove
```

Gallery 复用 `/timeline`，没有 Gallery 专用端点；图片加载复用仓库已 vendored 的 `NetworkImage`（需加入 App target），不引入新的远程依赖。

---

## 2. 服务端数据与选图

### 2.1 关键帧事件

- `video` 事件 = filter 判定的关键帧（`key_frame=true`；同场景最多一帧）。
- 每个关键帧写一条事件，`event_ref = "{rsession_id}:video:{frame_id}"`；payload 含 `frame_id / window_id / caption / place / objects / ocr / people`。
- 帧图媒体 URL 由 BFF 单点组装：

```
/api/mylife/media/{device_uuid}/{rsession_id}/video/{frame_id}.jpg
```

- media 端点校验 device/session UUID、kind、文件名，防路径穿越与白名单外设备。

### 2.2 `day_stats.images`

| 字段 | 含义 |
|---|---|
| `frame_id` | 帧 id |
| `ts_ms` | 帧时间 |
| `event_ref` | 事件唯一键 |
| `window_id` | 窗口归属（可空） |
| `device_uuid` / `rsession_id` | 媒体定位信息 |

- 不存 `media_url`：URL 在 BFF 出口组装，避免预计算绑定 host/端口。

### 2.3 选图算法

输入：候选关键帧（`frame_id`、`rsession_id` 非空）+ 上限 N。

1. 分组：键 = `window_id`（空/缺失 → `event_ref`）；组内按 `ts_ms` 稳定升序。
2. 窗口序 =（各窗口首帧 `ts_ms`，分组键）。
3. 轮转：第 r 轮依次访问每个窗口，取该窗口第 r 张未取帧；取满 N 即停；某轮无新增结束。
4. 输出按 `ts_ms` 稳定升序。

性质：

- 第 1 轮保证每个窗口至少一张；窗口数少时后续轮次补齐；
- 窗口数 > N 时尾序窗口不进结果；
- 单日输入上，每个被选窗口的帧集合是该窗口 `ts_ms` 升序的前缀；
- 服务端池上限 `DAY_IMAGE_POOL = 15`；客户端跨日重建必须在逐日池拼接后的形状上重跑同一规则。

### 2.4 BFF Overview 出口

- `_to_day_images` 把 `day_stats.images` 转成 `DayImage`，组装 `media_url`，透出 `window_id`；坏条目逐条丢弃。
- `DayImage` 字段：`frame_id / ts_ms / event_ref / media_url / window_id?`。
- iOS Store 出口把 `media_url` 绝对化并附 token；卡片拿到的已是可加载 URL。
- overview 的 `day_stats` 是全设备合并口径；`/timeline` 按设备白名单过滤。若库中存在白名单外设备，卡片 `imageTotal`/缩略图可能与 Gallery 不一致（Android 既知行为）。

---

## 3. 卡片照片区

### 3.1 跨日轮转重建

`PeriodAggregator` 对组内各天 `OverviewDay.images` 重跑 §2.3：

| 粒度 | 上限 |
|---|---|
| 日 | 3 |
| 周 | 9 |
| 月 | 12 |
| 年 | 15 |

- 候选 = 组内各天 images（服务端每日最多 15）。
- 分组键 = `windowId` 非空 ? windowId : eventRef（空串等价缺失）。
- 输出 `PeriodImage(url, tsMs, eventRef, frameId, windowId?)`，按 ts 稳定升序。
- 池 15 ≥ 最大 cap 15，单日关键帧足够时任意粒度都可由单日喂满；不足时返回实际帧数。
- `imageTotal` = 组内各天 `counts["video"]` 之和（video 事件总数），用于计数角标；与 Gallery 的 `total` 同口径（都按 video 事件计数），但设备口径可能不同（见 §2.4）。

iOS 实现要点：

- Swift 的 `sorted` 不保证稳定；必须用「原始下标」做次级排序键，保持与 Android/Kotlin 稳定排序一致。
- 分组字典需保序（数组 + 索引表，或按键排序），不能依赖 `Dictionary` 遍历顺序。

### 3.2 `dateRange()`

| 粒度 | 范围 |
|---|---|
| 日 | `key...key` |
| 周 | ISO 周周一...周日 |
| 月 | 当月 1 日...月末 |
| 年 | 当年 1 月 1 日...12 月 31 日 |

- `key` 形状异常 → 返回 `nil`，调用方跳过导航。

### 3.3 卡片 UI

- 图片区：`images.chunked(3)` 按行渲染；行内图片等分该行宽度（满行 3 张各 1/3，末行不足 3 张时拉伸等分），1:1，`scaledToFill`，圆角 8，间距 4。不要用固定 3 列 `LazyVGrid` 代替（末行会留空）。
- 占位/失败：Panel 灰底。
- 单击图片 → `TimelineRoute.gallery`，`focusFrameId = image.frameId`、`label = digest.rangeLabel`、`pending = digest.hasPending`。
- 计数角标：
  - `imageTotal > images.count` 时显示；
  - 位于首行末图右上角，黑 60% 胶囊 + Star 数字（`labelSmall`，水平 6 / 垂直 1），偏移 (+10, −10)；
  - 显示角标时图片区上方额外补 10pt；
  - 角标整体可点，点击同样进入 Gallery（focus 仍为该图）。
- 卡片待分析态（`hasPending`）在卡片顶部显示脉冲点 +「分析中…」，与 Gallery 顶栏同语义。

---

## 4. Gallery 路由与导航

### 4.1 参数

```swift
case gallery(
    from: String,          // 本地日 YYYY-MM-DD，闭区间
    to: String,
    label: String,         // 卡片范围文案，已本地化
    focusFrameId: String?, // 被点帧；可空
    pending: Bool          // 卡片进入时的待分析快照
)
```

- 范围 = 被点卡片所在粒度的时间范围。
- `label` 用于顶栏。
- `focus` 首屏命中则滚动定位并高亮；未命中（帧在后续页）停在顶部，不做「翻页直到找到」。
- `pending` 只作为顶栏「分析中…」快照；Gallery 不随 overview 轮询更新该值（与 Android 一致）。
- 请求携带设备时区 `TimeZone.current.identifier`。

### 4.2 导航

```
PeriodCard 图片点击
  → TimelineHomeViewModel.onImageClick(image, digest)
  → TimelineFlowModel.push(.gallery(...))
  → FrameGalleryView
```

- Gallery 是时光机内的子页；返回回到首页原滚动位置。
- Gallery 打开全屏查看器是覆盖层，不是新路由。

---

## 5. Gallery 屏

### 5.1 结构

```
←  8月21日 · 周四          12 张            编辑
┌────────┬────────┬────────┐
│        │        │        │  3 列方图
├────────┼────────┼────────┤
│        │        │        │
└────────┴────────┴────────┘
             [删除 (N)]      // 仅编辑模式
```

- 自带 Abyss 纯色底、状态安全区，不复制星点。
- 顶栏三区：
  - 左：返回（Dust，44×44）+ label（Gold，16/24 medium，单行省略）；左区 `weight 1`；
  - 中：`Data` 时显示计数（Dust，12/16）；pending 时显示「分析中… · N 张」；
  - 右：`Edit / Done` 文本按钮（Gold）；`Data` 且帧非空时出现，编辑模式下保持；删除中禁用；右区 `weight 1` 且右对齐，保证中区真居中。
- 主体按状态分派：
  - `loading`：居中「加载中…」；
  - `empty`：空态图标 +「这段时间还没有画面」+「分析完成后会自动出现」；
  - `error`：错误文案 + 重试；
  - `data`：`FrameGrid`。
- 编辑模式底栏：居中描边按钮，Red 描边/文字；0 选禁用；删除中显示 18pt 转圈 +「删除中…」并禁用；底部加安全区留白。
- 删除提示：屏幕底部居中覆盖层（黑 75% + Star，圆角 16，2 秒 TTL）；编辑模式时上移避开底栏。
- 删除确认：居中的 Alert，标题「删除照片」，正文复数「确定删除选中的 N 张照片？」，取消/删除（红）。

### 5.2 状态模型

```swift
enum FrameGalleryState: Equatable {
    case loading
    case empty
    case error(message: String)
    case data(frames: [FrameItem], total: Int, nextCursor: String?, loadingMore: Bool)
}

struct FrameGalleryUiState {
    var state: FrameGalleryState = .loading
    var viewerIndex: Int?       // nil = 关闭
    var imageSaveTip: String?
    var savingFrameId: String?
    var editMode: Bool
    var selectedFrameIds: Set<String>
    var deleteConfirm: Bool
    var deleting: Bool
    var deleteTip: String?
}
```

- `viewerIndex` 指向已加载 `frames` 的下标。
- `total` 使用服务端计数；`media_url`/`frameId` 为空的条目不进入列表，因此可见张数可能小于 `total`。
- `pending` 由路由持有，不放进 `FrameGalleryState`。

### 5.3 分页

- 3 列网格，每页 40 张；item key = `frameId`；网格内容内边距 4，水平/垂直间距 4。
- 触底预取：可见最后一项索引 ≥ `frames.count - 6` 时触发。
- 每个新触发只发一次：滚离阈值区再回来，或上一页成功追加（`frames.count` 变化）后才重新武装。
- 失败后不立即重发；静默保留已有页。
- append 按 `frameId` 去重（offset 游标在分析中插入新帧会重叠）；每次成功续页用响应 `total` 更新顶栏计数。
- 正常分页列表只增不减；查看器页数随列表增长，当前页索引不漂移；删除成功后的强制重载整体替换列表。
- 加载中底部显示整行加载提示。

### 5.4 focus 定位

- 首次取得 `data` 后消费一次 focus：
  - `frames.firstIndex { $0.frameId == focusFrameId }`；
  - 命中 → `scrollToItem(index)`，金色 2pt 描边高亮 2 秒；
  - 未命中 → 不主动滚动（通常停在顶部；用户已滚动则保持）。
- 高亮清除使用独立计时，避免 loadMore 重启 effect 导致高亮残留。
- iOS 实现：`ScrollViewReader + LazyVGrid`；顶部锚点；高亮用 overlay 边框。

---

## 6. 全屏查看器

### 6.1 结构

- 覆盖层非路由；黑色全屏，盖在 Gallery 之上。
- 页数 = 已加载 `frames.count`；支持横向翻页。
- 每页远程 JPEG，`scaledToFit`。
- 复用 `ImageViewerMath`：1x–5x、fit/offset 夹取。
- iOS 15 无 `PagingScrollView`；沿用 `ImageViewerOverlay` 的「当前页 + 左右邻页 + 归位动画」实现，或使用 `TabView(.page)` 隐藏索引 + 自定义手势；推荐前者，保持与现有查看器一致的单击/缩放行为。

### 6.2 手势

- 单击 → 关闭查看器（fit 与放大态）。
- 双指捏合 1x–5x，以质心为锚。
- 放大态单指拖动 → 平移，越界夹取。
- fit 态单指横滑 → 翻页；放大态禁止翻页。
- 翻页落定后 scale/offset 重置回 fit。
- 未加载/加载失败（无 intrinsic size）时不做变换；空白页仍可翻页/单击退出。
- 图片 intrinsic 尺寸由 `NetworkImage` 的 `idealSize` 提供；每页独立缓存。

### 6.3 叠层信息

- 顶部页码胶囊：居中，黑 35%，只显示 `n / N`；`N` = 当前已加载帧数（随分页增长），不是服务端 `total`；单帧不显示；不显示时间。
- 底部单一 bar：
  - 内容自适应宽度并居中；黑 60%，圆角 12；无 caption 时圆形底；
  - caption 单行省略；被截断时尾部金色 `⌄`，展开后 `⌃`；展开为本次全屏会话级，翻页保持，关闭重进回到收起；
  - caption 无/单行未截断时点击不展开，但事件被消费，不会误退出；
  - 右侧保存按钮 44pt，保存中 18pt 转圈，不置灰。
- 保存提示在 bar 上方，黑 75% 胶囊，Star 文字，2 秒。
- bar 是页面的兄弟层，绘制在页面之上；bar 上的按下不翻页、不退出。

### 6.4 预取

- 当前页 ≥ `frames.count - 3` 时调用 `loadMore`；
- `nextCursor == nil` 时 no-op；
- 列表追加后页数增长，当前页索引不漂移。

### 6.5 保存

- 保存对象 = 点击瞬间当前页帧的 `frameId`；回调按 id 解析，不以页索引为准（权限回调可能晚于翻页）。
- 流程：

```
Viewer.onSaveClick(frameId)
  → FrameGalleryViewModel.saveFrame(id:)
      → MylifeStore.downloadMedia(mediaUrl)     // 失败抛错，调用方收敛为「保存失败」
      → 校验可解码图片（ImageIO）
      → PhotoLibrarySaver.save(jpeg:)
```

- `savingFrameId` 重入守卫；连点吞掉。
- 关闭查看器取消在飞保存并清提示；重开清旧提示。
- 提示：已保存 / 保存失败；2 秒 TTL，先取消上一个计时器再显示。
- 未实现：双击缩放、隐藏系统栏、分享；保存不复用 NetworkImage 缓存（重新下载）。

---

## 7. 编辑模式与批量删除

### 7.1 选择框

- 编辑模式每格右下角绘制选择框：
  - 22pt 方框，圆角 4；
  - 未选：白色 85% 1.5pt 描边 + 黑 35% 半透明底；
  - 已选：Gold 实底 + Abyss 色勾；
  - 无 `eventRef` 的帧置灰禁用（防御；video 事件恒有 eventRef）；
  - 命中区外扩到 34pt；选择框点击自消费，不触发图片点击；图片其余区域仍打开查看器；
  - 无障碍 label「选择照片」。
- 选中上限 50；第 51 张不选中并提示「最多选择 50 张」。
- 选择集合跨页保持；只能选择已加载帧。

### 7.2 编辑流程

- 顶栏「编辑 / 退出」切换；进入/退出均清空已选。
- 底栏「删除 (N)」：0 选禁用；点击打开确认弹窗；取消/点外部关闭，不发请求，保留选择。
- 确认删除：
  - 关闭弹窗，`deleting = true`；
  - 调 `POST /events/remove`；
  - 成功：`removed + skipped(not_found)` 计为已删除，其余计失败；提示「已删除 N 张」或「N 张已删除，M 张失败」；退出编辑模式并清空选择；`markRemovalRefreshPending()`；强制重载当前范围（清列表 → Loading → 首页 40 张）；
  - 失败：超时 →「删除超时，请稍后重试」，其他 →「删除失败」；保留编辑模式与选择，可直接重试（服务端幂等）；
  - `finally` 复位 `deleting`。
- 删除中：
  - 底栏转圈 +「删除中…」；
  - 顶栏返回禁用（自定义栈本身不启用系统边缘返回手势）；
  - 网格图片点击、选择框、退出编辑、再次删除全部 no-op；
  - 全屏查看器不可打开。
- 成功后的卡片刷新：
  - Gallery 内不触发 overview 刷新，只记标记；
  - 退出 Gallery（弹栈）时由 `MylifeStore.flushRemovalRefreshIfNeeded()` 强刷已加载窗口；
  - Store 的 60s pending 轮询独立运行，删除本身不触发画廊内刷新；
  - 删除流程不重新生成 LLM 日总结，历史 summary 可能仍提及已删除内容，客户端不处理。

### 7.3 删除接口

请求：

```
POST /api/mylife/events/remove?token=<wire>
Content-Type: application/json

{"event_refs": ["{rsession_id}:video:{frame_id}", ...]}
```

- 1–50 个非空字符串；strict 模式；不传 `device_id`（与 Android 一致）。
- BFF 先按可见设备预检：不存在/不可见 → `not_found`；非 video → `not_video`；其余转发 lvdata-mcp。

响应（200，部分成功语义）：

```json
{
  "removed": ["..."],
  "skipped": [{"event_ref": "...", "reason": "not_found | not_video"}],
  "days_recomputed": [...],
  "images_removed": ["frame_id"],
  "images_pending": ["frame_id"]
}
```

- 每个 ref 恰出现于 `removed` 或 `skipped` 之一。
- `not_found` 视作已删除；重复提交幂等。
- 400 仅格式非法；401 unauthorized；502 upstream_unavailable；503 removal_disabled。
- 超时 30 秒（派生 URLSession 配置）。

---

## 8. 数据层

### 8.1 `MylifeStore.frames`

```
GET /api/mylife/timeline
  ?date_from=<from>&date_to=<to>
  &types=video&limit=40&cursor=<opaque>&timezone=<device>&token=<wire>
```

- 展平 `days[].events`；
- 跳过 `mediaUrl` 或 `frameId` 为空的条目；
- 映射 `FrameItem{tsMs, mediaUrl(绝对化+token), frameId, windowId?, caption?, eventRef?}`；
- 按 `tsMs` 稳定升序；
- 返回 `frames / total / nextCursor`。
- `total` 为服务端范围内 video 事件数，包含被客户端跳过的条目。

### 8.2 删除

`MylifeStore.removeEvents(eventRefs:)` 直通 API，无缓存；返回 `removed`/`skipped`。

### 8.3 延迟刷新

- `markRemovalRefreshPending()`：设置内存标记；
- `flushRemovalRefreshIfNeeded()`：清除标记并在 Store 自有 Task 内强刷已加载窗口；失败静默；
- 触发点：Gallery 弹栈（ViewModel 销毁/onDisappear），不是 App 退后台。

---

## 9. 边界行为

| 场景 | 行为 |
|---|---|
| period 无关键帧 | Gallery 空态；卡片侧由 `hasPending` 脉冲兜底 |
| 单帧 | 查看器无页码胶囊；仍可单击退出 |
| 帧数 > 40 | 滚动触底自动加载下一页 |
| 加载中再次触底 | `loadingMore` 互斥，不重复请求 |
| 首屏失败 | 错误态 + 重试 |
| 翻页失败 | 静默保留已加载页；滚离阈值区再回来或下一页成功追加后重试 |
| 帧图 404/解码失败 | 网格 Panel 灰底；查看器空白页仍可翻页/单击退出 |
| 放大态左右拖动 | 不翻页 |
| 返回 | 查看器打开时先关查看器；编辑模式先退编辑；删除中吞掉；最后弹栈 |
| 跨午夜/跨时区 | from/to 来自 overview 生效时区的日卡 key，请求携带设备时区；设备时区与 overview 时区不一致时范围可能对应不同日界（Android 既知行为） |
| 旧数据无 window_id | 按 eventRef 视作独立窗口 |
| 同一窗口跨日 | 按 windowId 跨日归组后统一轮转 |
| 窗口取尽仍不足 cap | 返回实际帧数 |
| 当日窗口数 > 池 15 | 轮转取满即停，尾序窗口无候选 |
| 多帧同 ts | 稳定排序保持轮转序 |
| 分析中插入新帧导致游标页重叠 | 按 frameId 去重 |
| media_url/frameId 为空 | Store 跳过；Gallery total 可能大于可见张数 |
| 查看器接近已加载末尾 | 提前 3 页触发 loadMore；无 cursor 停住 |
| focus 帧不在首屏 | 停在顶部，用户自行下滑 |
| caption 为空 | 底部 bar 退化为圆形保存按钮 |
| caption 单行未截断 | 无法展开；点击被消费、不退出 |
| caption 展开后翻页 | 保持展开；关闭重进回到收起 |
| 保存中再次点击 | 重入守卫吞掉 |
| 保存中关闭查看器 | 取消在飞保存并清提示 |
| 选满 50 再点第 51 张 | 提示上限，不选中 |
| 跨页选择 | 选择集合跨页保持；只能选择已加载帧 |
| 部分删除成功 | removed + not_found 计为已删除；其余计失败；整页重载 |
| 全部 not_found | 视作全部已删；重载 |
| 删除超时/断网/5xx | 保留选择与编辑模式，可幂等重试 |
| 删除后该 period 无帧 | 重载后进入空态 |
| 删除后关闭 Gallery | 弹栈时强刷首页窗口 |
| 编辑模式退出 | 清空选择；已选不保留 |
| 查看器内 | 不提供选择/删除 |

---

## 10. 本地化与可访问性

- key：
  - `mylife_loading`、`mylife_load_failed`、`mylife_gallery_empty_title`、`mylife_analysis_pending`、
  - `mylife_frames_count`（复数）、`mylife_analyzing_frames`（复数）、
  - `mylife_edit`、`mylife_edit_exit`、`mylife_select_photo`、
  - `mylife_delete_count`、`mylife_delete_photos`、`mylife_delete_confirm`（复数）、`mylife_deleting`、
  - `mylife_max_selection`、`mylife_delete_timeout`、`mylife_delete_failed`、
  - `mylife_photos_deleted`（复数）、`mylife_photos_deleted_partial`、
  - `common_back`、`common_retry`、`common_cancel`、`common_delete`、`common_save_image`、`common_saved_to_gallery`、`common_save_failed`。
- 页码 `n / N` 用格式化字符串；中文数字直接使用系统 locale。
- 无障碍：
  - 网格图片 label「第 N 张照片，共 M 张」；
  - 选择框 label「选择照片」+ `.isSelected`；
  - 顶栏编辑/删除/返回均可读；
  - 删除提示出现时通过 accessibility announcement 播报（可选）。

---

## 11. 测试计划

### 11.1 单元

- 轮转选图：分组键 windowId/eventRef、cap 3/9/12/15、跨日同窗、同 ts 稳定、池不足、服务端池截断下的重建等价性。
- `imageTotal`；`dateRange()` 四粒度与跨年/闰月边界。
- Gallery VM：首屏成功/空/失败；分页 append 升序与 frameId 去重；翻页互斥；游标耗尽；失败保留；focus；保存链（成功/失败/重入/关闭取消/重开清提示）；编辑选择与上限；确认弹窗；删除成功/部分成功/全部 not_found/异常；删除中全拦截；弹栈触发刷新。
- Store：frames 映射（跳空、升序、绝对化）、请求参数、downloadMedia 抛错、removeEvents 映射。

### 11.2 UI

- 卡片图片与角标进入 Gallery，focus 命中时定位/高亮；
- 3 列网格、计数、空态/错误；
- 查看器翻页/缩放/单击关闭/页码/caption 展开/保存提示；
- 编辑选择只切换选择不打开查看器；图片区域打开查看器；
- 删除确认/取消/成功提示/失败保留选择；
- 删除中返回被拦截；
- 大字号与 iPad 600pt。

### 11.3 真机

- 自签 TLS 下帧图加载；保存到相册权限允许/拒绝；
- 大 period（>40 帧）分页与查看器预取；
- 删除后的卡片角标/缩略图刷新（退出 Gallery 后）。

---

## 12. 当前限制

1. 网格下载完整 JPEG（无缩略图端点），40 张/页约数 MB。
2. focus 帧在后续页时不主动滚动，停在顶部。
3. 选图规则客户端/服务端双实现，必须同步修改；池大小必须 ≥ 最大 cap。
4. 保存不复用图片缓存，重新下载。
5. 数据源差异：卡片来自 overview 快照，Gallery 来自实时 `/timeline`；分析中新增帧可能「卡上没有、Gallery 有」。
6. 删除不重新生成日总结；客户端不处理历史 summary。
7. 仅支持删除 video 事件；`not_video` 归入 skipped。
8. 未实现长按多选、查看器内删除、分享、双击缩放、隐藏系统栏。
9. iOS 无 Android legacy 存储权限分支；相册 `.addOnly` 被拒时直接「保存失败」。

---

## 13. 代码锚点

### Android

| 文件 | 职责 |
|---|---|
| `tools/lvdata-mcp/src/lvdata_mcp/summary/day_stats.py` | `derive_images` 轮转选图；day_stats stage |
| `tools/lvdata-mcp/src/lvdata_mcp/consts.py` | `DAY_IMAGE_POOL = 15` |
| `services/bff/timeline/service.py` | `build_overview`、`_to_day_images`、`_video_media_url` |
| `services/bff/remove/router.py`、`service.py` | `/events/remove` |
| `.../data/repository/MylifeRepository.kt` | `frames`、`downloadFrame`、`removeEvents`、延迟刷新 |
| `.../timeline/PeriodAggregator.kt` | 轮转重建、cap、`dateRange()`、`imageTotal` |
| `.../timeline/components/PeriodCard.kt` | 3 列缩略图、计数角标 |
| `.../gallery/FrameGalleryScreen.kt` / `FrameGalleryViewModel.kt` | Gallery 屏/VM |
| `.../gallery/components/FrameGrid.kt` | 网格、触底、选择框 |
| `.../gallery/FrameViewerOverlay.kt` | 全屏查看器 |

### iOS（拟）

- `Core/Mylife/MylifeStore.swift`（frames/remove/download/flush）
- `Features/Timeline/Gallery/FrameGalleryView.swift`、`FrameGalleryViewModel.swift`、`FrameGrid.swift`、`GalleryViewerOverlay.swift`
- `Features/Timeline/TimelineHomeViewModel.swift`（图片点击导航）
- `Features/Conversation/ImageViewerOverlay.swift`（`ImageViewerMath`）
- `Core/PlatformAdapters/PhotoLibrarySaver.swift`
- `Resources/Localizable.xcstrings`
