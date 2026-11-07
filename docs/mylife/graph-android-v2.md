# MyLife Graph Android V2 实施契约（时间瀑布星图 · Final）

> **Status**: Final（唯一实施依据；由 `graph-android-v2-d4.md` 与 `graph-android-v2-k3.md` 双稿评审合并而成，替代两稿）
> **Date**: 2026-08-22
> **基线**: k3 终稿全文；评审修复项见 §11 决策记录 9–14 与文中 ★ 标注
> **Scope**：重构 MyLife **Graph tab**；★ 真机扩展：MyLife 宿主改**双 tab（时间轴/星图）**、
> 回放 tab 移除（§4.2）；Timeline、Replay 详情页、DI、SSL、媒体链路不变。
> BFF 独立改动：person 实体提取（§5.2）+ event `evt:` 详情兼容（§5.2b）。Web 版 mylife 不动。
>
> **⚠️ 已移除（2026-09-22，见 docs/android_timeline_hotslot.md §14 R3=C）**：Android 侧**星图 tab 与 graph/ 屏栈整栈删除**
> （含 `MylifeApi/Repository.graph|entity`、`GraphDto/EntityDto` 系 DTO、`mylifeKindColor`、GraphAnalytics DI 与全部相关测试；
> 该批同删 Replay 整栈、MyLife 首页收敛为单视图且标题改「时光机」）。**本文件保留为历史实施契约**（需恢复从该批之前的
> git 历史取回）；**Web 版星图不受影响**（`apps/web/mylife` 仍为 Timeline/Graph/Replay 三 tab）。

---

## 1. 背景与需求

### 1.1 V1 现状（代码事实，2026-08-22 核对）

Graph tab 当前为"力导向星图 + 控件条"结构（`ui/screens/mylife/graph/`）：

- **控件条两行**（`GraphScreen.kt`）：① kind 图例 chips（topic/emotion/app/event/object/scene，
  选中=删除线淡化，反选语义）；② 过滤行（起/止日期 `DatePickerDialog`、min_weight 数字输入、
  "列表/星图"切换、focus 中显示"✕ 退出聚焦"）
- **星图**：`ForceLayout`（纯 Kotlin d3-force 复刻：link 70/0.4、manyBody -60-6w、collide r+6、
  alphaDecay 0.05）+ `LifeGraphCanvas`（Compose Canvas：捏合 zoom k∈[0.2,4] 质心锚点、空白 pan、
  节点拖拽 fx/fy、抬起 <6dp 判定点击、focus 邻居高亮其余 alpha 0.1）
- **列表副视图**：`EntityTopList`（weight Top 50 LazyColumn）
- **单击语义**：event 节点 → Replay 深链；其余节点 → focus（ego 子图重请求 `?focus=`）+
  `EntityDetailSheet`（含"聚焦关系/退出聚焦"按钮 + appearances → Timeline 深链）
- **数据**：`GET /api/mylife/graph`（date_from/to、types、focus、depth=1、min_weight），
  `MylifeRepository` LRU 8 条 / 5min；`keepPreviousData`（失败保留旧图）
- **测试**：`ForceLayoutTest`、`GraphViewModelTest`（12 用例）

真实数据规模（`/opt/lvdata`，2026-08-22 实测）：48 session（14 已分析），
图 121 节点 / 1282 边，时间跨度 2026-07-24 ~ 08-22（29 天）；
kind 分布 event 14 / topic 51 / object 41 / app 7 / emotion 4 / scene 4，**无 person**。

### 1.2 V2 需求（逐条 → 设计决策）

| # | 需求 | 决策 |
|---|------|------|
| 1 | 去除日期起止这一行 | 控件条只保留 kind chips 行（§3.2）；VM 不再传 `date_from/date_to`；同行的 min_weight 输入与"列表/星图"切换一并移除（§11 决策 1/2） |
| 2 | kind 过滤行加入 people/person | chips 增加 `person`；**BFF 新增 person 提取**（§5.2：audio people 按 role/gender 确定性归桶，**weight 每 session 每桶 +1** ★）；Android 侧 `mylifeKindColor("person") = MylifeColors.Green` |
| 3a | entity 按时间顺序排列，如瀑布/星河，无行列对齐 | **TimeRiverLayout**：纵轴=时间（新在上，与 Timeline 倒序一致），横轴=黄金角确定性散布（§5.1）；`ForceLayout` 力导向废弃 |
| 3b | entity 之间不画线 | 默认不绘制边；边数据仍随图加载（供 3e ego 复用，零额外请求） |
| 3c | entity 划到屏幕中心附近显示缩略信息，离开隐藏 | ★ 真机落地为**随机 summary 轮播**：每 3 秒随机选一个当前页可见实体显示气泡（filter 生效；ego 激活暂停，§3.4）；中心信息带机制删除 |
| 3d | 单击 entity 行为不变 | event → Replay 深链；其余 → EntityDetailSheet（**删除"聚焦关系"按钮**，关系展示移交长按，§3.5） |
| 3e | 长按 entity（≥2s）→ 只画该 entity 到其他 entity 的连线 | **长按 ego 模式**：客户端从已加载 edges 过滤（无新请求），仅画选中节点 ↔ 邻居的边；邻居白环 + 强制 label，其余节点淡化（§3.5）；V1 的 `?focus=` ego 重请求在 Android 端废弃。★ 真机落地：阈值 **1s**、连线 **always 绘制**、退出**仅"关闭关系"按钮**、激活时**仅关系成员可点击**（§14 6/8/9） |
| 4 | zoom-in/out = 时间刻度比例缩放，影响每屏时间 range 与 entity 数目 | 捏合只缩放纵轴时间刻度：**连续缩放** `daysPerScreen ∈ [1, 366]`，默认 **30 天/屏**（月档，★ 真机反馈，§3.6），锚点=手势质心时刻（§3.6）；密度自适应截断（§3.6）；横轴不缩放 |

### 1.3 非目标（V2 不做）

- 不改 Timeline / Replay / BFF 既有端点语义（person 为 `ENTITY_KINDS` 扩展 + 提取分支，非新端点）
- 不做具名人物识别（person 为粗桶实体；具名抽取归分析管线 Phase 2，§10 风险 1）
- 不做力导向/物理模拟的任何保留形式（`ForceLayout` 删除，非改造）
- 不做横轴 pan/缩放（横轴仅散布展示，无信息维度）
- 不做 entity 多次出现的多点绘制（一实体一节点，定位于 first_seen；演进见 §10 风险 3）
- 不动 Web 版 mylife（Web 继续消费 `?date_from/to&focus=` 参数，BFF 参数全部保留）
- ★ 不保留 V1 双实现/Feature flag（回滚 = git revert，§8.4）

---

## 2. 视觉模型：从「力导向星图」到「时间瀑布」

```
V1:  节点=圆点，位置=力平衡结果（语义：关系拓扑），边=共现连线常显
     zoom = 视距缩放（k∈[0.2,4]，图形几何缩放）

V2:  节点=圆点，纵坐标=实体首次出现时刻（语义：时间），横坐标=黄金角散布（无语义）
     边默认隐藏；滚动=时间旅行；zoom = 时间刻度缩放（天/屏）
     随机轮播 = 每 3 秒点亮一个可见实体（★ 真机取代中心带）；长按 = 临时点亮一个实体的关系
```

- **纵轴语义**：新在上、旧在下（与 Timeline V2 倒序一致，向下滚动 = 回到过去）。
  实体定位于 `first_seen_ms`（实体进入生命的时刻；fallback `last_seen_ms`，§5.1）
- **横轴无语义**：确定性散布（黄金角），仅为避免重叠、营造"星河"质感；
  无行列对齐、无 lane 归属（需求 3a）。★ 横轴散布与视口状态完全解耦——
  zoom/滚动不改变任何节点的 xFrac，杜绝"滚动时星河抖动"（d4 稿 y-collide 依赖窗口的缺陷）
- **边的角色转变**：从"常显背景"变为"按需点亮"——长按单实体时才画出它的关系（需求 3b/3e）
- **zoom 的角色转变**：从"图形缩放"变为"时间刻度缩放"——每屏时间 range 与可见 entity
  密度随之变化（需求 4）

---

## 3. UI 设计

### 3.1 布局结构（上 → 下）

```
┌──────────────────────────────┐
│ ✦ MyLife            (既有头部) │
│ [topic][event][person][obj…] │  kind chips 行（★ 真机顺序；单行
│   [scene][emotion][app]      │  LazyRow，选中=下划线+高亮）
│ ──────────────────────────── │
│      2026年8月 (左上角日期标签)   │  左上角固定，视口顶缘日期（§3.7）
│ 8/19 ── ── ── ── ── ── ──  │  时间标尺刻度线（左缘标签，§3.7）
│      ●        ◆ topic·旅游  │  ●=节点（kind 色，半径∝√weight）
│   ●      ●   ╭──────────╯   │  ◆=随机轮播气泡（§3.4）
│ ●     ✦═══════╗  ●          │  ✦=长按 ego 中心，═=ego 连线（§3.5）
│        ●      ║    ●        │     （仅 ego 激活时绘制）
│ 8/12 ── ── ── ── ── ── ──  │
│   …（垂直滚动 = 时间旅行）     │
└──────────────────────────────┘
```

V1 的第二行控件（起/止日期、min_weight、列表/星图切换、退出聚焦）**整行删除**（需求 1）；
ego 激活时改由浮动"关闭关系"pill 退出（★ 真机：仅按钮退出；空白单击/返回键不退出，§3.5）。

### 3.2 控件条（kind chips 行）

- 保留单行 `LazyRow` + kind chips，**反选语义不变**（选中=下划线+高亮=从图中排除；
  空选择=全部显示；★ 真机反馈：多行 FlowRow/All 项测试后还原单行，删除线样式改下划线）
- chips 列表（★ 真机顺序）：`topic / event / person / object / scene / emotion / app`
  （`person` 新增，需求 2；单行 LazyRow 横向滑动）
- `person` 颜色：`MylifeColors.Green`（`#6EF5A8`，`mylifeKindColor` 新增分支；
  Green 为现有色板中唯一未被 kind 占用的色相，2026-08-22 核对 `MylifeTheme.kt:29,33-41`）
- chips 变更 → 重新请求图（防抖沿用 `@Named("graphDebounceMs")`，连击合并为一次请求）；
  **不再清除 ego/详情以外的状态**（V1 的 focus 清除逻辑随 focus 机制删除，§7）

### 3.3 时间瀑布画布（TimeRiverCanvas，替换 LifeGraphCanvas）

**坐标系**（世界坐标 = 时间毫秒，屏幕坐标 = px）：

```
pxPerMs  = screenHeightPx / (daysPerScreen × 86_400_000)   // 时间刻度（zoom 的唯一产物）
screenY  = (tTop − t) × pxPerMs                            // tTop = 视口顶缘对应时刻
```

- **滚动**：垂直滚动改变 `tTop`（向更早滚 → tTop 减小）；`detectTransformGestures` pan
  单手势链承担（★ 真机反馈：`scrollable` 在 Canvas 上与兄弟 pointerInput 事件竞争失效，
  V1 同构 pan 方案验证可用）；fling 惯性暂缺（遗留，后续以 velocityTracker 补充）
- **滚动边界**：顶缘 ≤ `tMax`（最新数据贴顶）；底部数据末之后可继续滚入约一屏空白页
  （`tTop` 下限 ≈ `tMin − offsetMs − radiusMs`，offset/radius 为顶部标签区与节点半径的
  时间补偿），滚过最老数据后空白页作"到底"视觉指示，`tTop < tMin` 时画布居中显示
  "已经到达底部"
- ★ **fling 语义声明**（fling 暂未实现，遗留项；实现时须遵守）：fling 保持**屏幕速度恒定**
  ——同一甩动速度在任何 daysPerScreen 下穿越的屏幕距离相同，穿越的时间跨度随刻度放大
  （缩小后一甩穿越更长时间，符合直觉）；zoom 改变 pxPerMs 的瞬间不进行速度换算/补偿
  （速度不连续可接受，手势已结束）
- **节点绘制**：仅绘制可视时间窗内节点（`TimeRiverLayout.visible`，有序列表 +
  二分定位，O(log n + k)）；圆色 = kind 色，半径 = √weight 映射 `[4dp, 22dp]`
  （沿用 V1 scaleSqrt 规则）；**屏幕空间恒定半径**（不随 zoom 缩放——zoom 只改变时间密度，
  不改变图形大小，需求 4 的"每屏 entity 数目随刻度变化"由此自然成立；
  ★ d4 稿"半径×scale"的 V1 几何语义残留明确废弃）；画布顶部保留 30dp 日期标签区，
  节点整体下移 `pad` 避免贴顶被裁（命中换算同步含 pad 补偿，§4.1/§14 14）
- **无模拟、无帧循环**：V2 无物理模拟，`withFrameNanos` 迭代整体删除；
  绘制是 viewport 状态的纯函数，滚动/zoom 状态变更即重组重绘

### 3.4 随机 summary 轮播（需求 3c 的 ★ 真机落地，取代中心信息带）

- **机制**：每 3 秒从当前视口可见实体中**随机**选一个显示气泡；★ 中心信息带机制
  （±15% 带 + 渐隐 + weight 降序贪心去重叠）整体删除，`TimeRiverLayout.centerLabels`
  已移除（§14 7）
- **气泡**：样式沿用 V1 拖拽气泡（`Void` 底 + `Line` 描边 + `Star` 文字，`label (weight)`），
  置于节点右侧（节点 x 超过屏宽 2/3 时置左侧）
- **生命周期**：kind filter 变化即重启并立即重选（过滤生效）；滚动/缩放不重启
  （3 秒节奏稳定，切换时读最新视口，旧选择滚出屏外不绘制）；**ego 激活时暂停**，
  退出后恢复
- 该节点同步暴露为 semantics 节点（§8.2，TalkBack 可读）

### 3.5 长按 ego 连线（需求 3e 的 ★ 真机落地）

- **触发**：节点上按下 **≥1000ms**（★ 真机反馈：原 2s 阈值体感过久，收敛为 1s；仍自实现
  计时，平台 `onLongPress` ~500ms 不满足）且位移 <6dp。计时期间节点外圈进度环提示
  （1s 填满即触发；≥500ms 上报遥测）；触发时振动反馈（`HapticController` 既有能力复用）
- **激活**：`egoId = 节点 id`；**客户端边过滤**（`edges.filter { source==id || target==id }`，
  边已随图加载，**零网络请求**；V1 `?focus=` ego 重请求废弃）
- **绘制**：
  - **always 连线**（★ 真机反馈）：ego 中心 ↔ 各邻居的边一律绘制——邻居不在可视区内也画，
    线段延伸出窗外由 Canvas 裁剪；线宽 = `1dp + √weight`，Gold 70%，激活时 300ms 渐入
  - ego 中心：Gold 描边环；邻居：白环 + **强制 label**；其余节点 alpha 0.1
    （沿用 V1 focus 淡化语义）
- **交互过滤**（★ 真机反馈）：激活时**仅关系成员**（ego 中心 + 有连线的邻居）可点击，
  其余节点点击忽略；**单击关系成员仍打开其详情 sheet** 且不退出 ego（需求 3d）
- **退出**：**仅右上角浮动 "关闭关系" pill**（★ 真机反馈：空白单击/返回键不再退出，
  退出路径收敛为按钮单一路径）；长按另一节点 → 切换 ego 中心
- ego 激活中滚动/zoom 不退出（连线随节点位置实时重算）；**随机轮播暂停**，退出后恢复
- ★ **与详情 sheet 的叠加规则**（§4.3）：单击关系成员打开 sheet **不退出 ego**；
  sheet 展示期间 ego 连线在底层画布继续绘制（sheet 为 ModalBottomSheet，画布可见部分
  保持实时状态）；sheet 关闭后 ego 仍处于激活态，退出仍走 pill
- 已知边界：kind 反选过滤激活时，连线只含当前过滤子集内的边（§10 风险 4）

### 3.6 时间刻度 zoom（需求 4 的 ★ 真机落地）

- **手势**：双指捏合（`detectTransformGestures` 的 zoom 分量，pan 分量由同一手势链承担
  滚动）；zoom 因子 `z` → `daysPerScreen /= z`
- **缩放范围**（★ 真机反馈：**连续缩放**，1→366 天/屏实时反馈，丝滑无档位）：

  | 边界 | 取值 | 依据 |
  |------|------|------|
  | 最小（放大极限） | **1 天/屏** | 单日内 entity 已足够稀疏；再放大无信息增量 |
  | 最大（缩小极限） | **366 天/屏（固定封顶）** | ★ 真机反馈：不再按 `clamp(跨度×1.15, 7, 366)` 计算——跨度自适应上限与"一屏看全"直觉冲突；>1 年数据聚合视图归演进（§10 风险 5） |
  | 默认 | **30 天/屏（月档）** | ★ 真机反馈：默认月档一屏看全（原 7 天/屏在真实数据 29 天下首屏信息量不足） |

- **锚点**：手势质心时刻保持不动——`tAnchor = tTop − centroidY/pxPerMs`，
  zoom 后 `tTop' = tAnchor + centroidY/pxPerMs'`（与 V1 质心锚公式同构）
- **zoom 后视口修正**：`tTop` 重新 clamp 到滚动边界
- **密度自适应**：可视时间窗内节点数 >300 时，仅绘制窗内 weight Top 300
  （`TimeRiverLayout` 纯函数截断，确定性；真实数据 121 节点全量不触发，
  为 10× 增长预留）；label 与命中检测同样只作用于被绘制集合
- 视口状态（`tTop`、`daysPerScreen`）由 Screen 侧 `rememberSaveable` 持有
  （纯 UI 状态，不入 VM；tab 切换经宿主 entry 的 saveable registry 恢复）

### 3.7 时间标尺与顶部日期标签

- **标尺**：左缘横向刻度线（`Line` 色 alpha 0.5）+ 日期标签（`Dust`，labelSmall）；
  每条刻度线**左端标注自己的日期**（★ 真机反馈：顶部线/下一条线等特殊标注方案迭代后
  恢复初始方式；紧贴顶部标签区的刻度线不标注，由左上角日期代替）：

  | daysPerScreen | 刻度间隔 | 标签格式 |
  |---|---|---|
  | ≤ 3 | 1 天 | `M月d日` |
  | ≤ 21 | 7 天（周一对齐，`WeekFields.ISO`，minSdk 26 平台自带） | `M月d日` |
  | ≤ 120 | 1 月 | `yyyy年M月` |
  | > 120 | 1 年 | `yyyy年` |

- ★ **顶部日期标签（取代中心 pill）**：画布左上角固定显示当前视口**顶缘**日期
  （Gold，滚动/缩放实时更新；`daysPerScreen ≤ 60` 显示 `M月d日`，否则 `yyyy年M月`）；
  画布顶部 30dp 标签区同时为节点下移留空间。这是时间旅行的主要方位反馈
- 初始视口：`daysPerScreen = 30`，`tTop = tMax`（最新 entity 贴顶）

### 3.8 其他状态与适配

- **加载/空/错**：沿用 V1 文案（"加载星图…" / "暂无数据" / 错误文案），
  `keepPreviousData` 语义不变（VM 仅成功时替换 data）
- **EntityDetailSheet**：删除"聚焦关系/退出聚焦"按钮及 `isFocused/onToggleFocus` 参数
  （关系展示移交长按 ego）；★ 真机：event 与其他实体一样开详情，sheet 内追加 **Play 按钮**
  → Replay 深链（仅 event 提供）；其余不变（label + kind + 权重/出现次数 + appearances →
  Timeline 深链）
- ★ **person 推断标注**：`entity.kind == "person"` 时，在权重行（"权重 N · 出现 M 次"）
  下方追加一行 `Dust` 色 `bodySmall` 说明："由语音自动推断的粗粒度分桶，可能合并多人"
  （决策 10：避免用户把推断当事实；其余 kind 不显示该行）
- **主题/insets/触控**：全部沿用 `MylifeTheme`、星空背景、44dp 触控目标
  （命中半径 `max(节点半径, 22dp)` 规则不变）
- **pending 轮询**：无改动（Repository 层共享 60s 轮询；graph 数据不含 analysis 状态，
  不受 pending 影响）

---

## 4. 交互模型与深链

### 4.1 手势清单（TimeRiverCanvas 单手势处理链）

| 手势 | 判定 | 行为 |
|------|------|------|
| 垂直滚动 | 空白或节点上拖动，位移 >6dp | `detectTransformGestures` pan → `tTop` 变化（★ fling 惯性暂缺，§3.3）；按住节点的长按计时随移动取消 |
| 双指捏合 | `detectTransformGestures` | 连续时间刻度 zoom（§3.6），质心时刻锚定 |
| 单击节点 | 抬起位移 <6dp 且按住 <1s | 所有实体统一开详情 sheet；event 详情内 Play → Replay（★ 真机，§3.8）；ego 激活时仅关系成员可点（§3.5） |
| 长按节点 | 按住 ≥1s 且位移 <6dp | ego 连线模式（§3.5）；长按期间移动 >6dp 降级为滚动 |
| 单击空白 | ego 激活时 | ★ 不退出 ego（退出仅"关闭关系"按钮） |
| 返回键 | ego 激活时 | ★ 不退出 ego，正常返回 |

手势优先级：双指 > 长按计时 > 滚动 > 单击（与 V1 同一 `awaitEachGesture` 链式判定风格）。

### 4.2 路由与深链（★ 真机：双 tab 导航）

- ★ 真机：MyLife 宿主改**双 tab（时间轴/星图）**，回放 tab 移除（`ReplaySessionListScreen/ViewModel`
  删除）；Replay 深链入口 = 时间轴 hot slot / 星图 event 详情 **Play** 按钮；`Routes.MYLIFE`
  带 `tab` 参数，深链经 `launchSingleTop` 复用 entry 更新参数
- Graph → Replay（event 详情 Play）与 Graph → Timeline（sheet appearance 单击）深链保持
- **删除的深链入口**：V1 详情 sheet 的"聚焦关系"按钮（`?focus=` 重请求）随按钮删除；
  BFF `focus/depth/date_from/date_to/min_weight` 参数保留（Web 仍消费），Android 端不再传参

### ★ 4.3 状态叠加规则（评审补充，统一仲裁口径）

任意时刻的 UI 状态为 `(egoId?, sheet open?)` 四格组合，规则如下：

| 当前状态 | 事件 | 结果 |
|---|---|---|
| 无 ego，无 sheet | 长按节点 | ego 激活 |
| 无 ego，无 sheet | 单击非 event 节点 | sheet 打开 |
| ego 激活 | 单击关系成员 | sheet 打开，**ego 保持**（连线继续绘制）；非关系成员点击忽略（§3.5） |
| ego + sheet | sheet 关闭（下滑/返回/点外部） | ego 仍激活 |
| ego + sheet | sheet 内 appearance 单击 → Timeline 深链 | 路由离开；返回 Graph 时 ego 状态随 tab 宿主 saveable 恢复 |
| sheet 打开 | 长按（sheet 遮挡，画布不可达） | 不可能发生，无需仲裁 |
| ego 激活 | "关闭关系" pill | 退出 ego（★ 唯一退出路径；空白单击/返回键不退出，§3.5） |
| ego 激活 | 长按另一节点 | 切换 ego 中心（§3.5），sheet 状态不受影响 |

---

## 5. 数据层设计

### 5.1 TimeRiverLayout（纯 Kotlin，可单测，新文件，替换 ForceLayout）

`ui/screens/mylife/graph/TimeRiverLayout.kt`——把图数据映射为时间瀑布上的可绘制/可命中
布局，**确定性、无 Android 依赖、无随机源**：

```kotlin
class TimeRiverLayout(
    nodes: List<GraphNodeDto>,
    edges: List<GraphEdgeDto>,
    providedTMin: Long = 0,       // ★ 显式指定（测试用）；≤0 时从节点计算兜底
    providedTMax: Long = 0,
) {
    val tMin: Long                // 全图最早时刻（数据到达即定）
    val tMax: Long                // 全图最晚时刻

    data class Placed(
        val node: GraphNodeDto,
        val t: Long,              // first_seen_ms ?: last_seen_ms ?: tMin（兜底排最底）
        val xFrac: Float,         // (0,1) 横向散布 ★含边缘内缩，见下
        val radiusDp: Float,      // √weight → [4,22]（沿用 V1 映射）
    )

    val placed: List<Placed>      // 按 t 降序（新→旧），滚动方向二分可查
    val spanDays: Int             // max(1, ceil((tMax-tMin)/DAY))；zoom 上限与标尺输入

    /** 可视时间窗裁剪（密度截断：窗内 >maxDraw 时按 weight 取 Top N，§3.6） */
    fun visible(tNewest: Long, tOldest: Long, maxDraw: Int = 300): List<Placed>

    /** 世界坐标命中（hitRadius 规则同 V1：max(节点半径, 22dp)）；
     *  ★距离闸门：归一化椭圆 d = (Δt/hitRadiusT)² + (Δx/hitRadiusX)² ≤ 1 才命中——
     *  横向超程不命中，否则"点空白"会被纵向命中带内的远处节点截获（§3.5 空白语义） */
    fun nodeAt(t: Long, xFrac: Float, hitRadiusT: Long, hitRadiusX: Float): Placed?

    /** ego 邻居（客户端边过滤，含边权；§3.5） */
    fun egoEdges(id: String): List<Pair<Placed, Int>>   // (邻居, weight)

    // ★ centerLabels（中心带 label）已移除——随机 summary 轮播取代（§3.4/§14 7）
}
```

- **横轴散布**（黄金角，确定性）：同 6h 时间桶（`bucket = t / 6h_ms`）内节点按 id 排序取
  序号 i，`xFrac = frac(hash01(id) + i × 0.618034)`；`hash01` = **FNV-1a 32 位 → [0,1)**
  （★ 真机修复：`id.hashCode()` 高 31 位由结构化 id 前缀主导，同一 kind 所有节点 xFrac
  聚拢成竖线；FNV-1a 雪崩充分，5000 样本均匀性 ≈ 期望 1/3，§14 1）。
  无行列对齐（需求 3a），同桶内错开，跨桶自然随机
- ★ **边缘内缩**（评审修复）：xFrac 消费处将 `[0,1)` 映射到 `[rPx, widthPx − rPx]`
  （r = 节点半径 + 2dp 余量），杜绝 xFrac→1 节点右缘画出屏外；xFrac 本身保持无量纲，
  宽度相关换算集中在 Canvas 一处
- **实体时刻**：`first_seen_ms ?: last_seen_ms ?: tMin`。语义 = 实体首次进入生命的时刻；
  跨天长寿命实体（真实数据最长 28.8 天）只在其首次出现处绘制一次（演进见 §10 风险 3）
- BFF 实测所有节点 first/last 均非空（`_touch` 恒写入），兜底分支仅为 DTO 可空性的
  防御，不产生线上路径

### 5.2 BFF：person 实体提取（★ 独立后端 MR；与 §5.2b 同为 V2 服务端改动）

**边界声明**（★ 吸收 d4 稿优点）：本改动为**独立后端 MR、独立验收、可先于 Android P1
任意排期合并**（BFF 先行是硬约束，§8 P0）；Android 不对 `types=person` 的 400 做任何
客户端容错分支（★ d4 稿的 message 子串匹配容错方案明确否决，§11 决策 11）。

**背景事实**（真实数据核对，§12）：`person` 在 Phase 1 被列为"kind 保留、无可靠数据源"
（video `people[]={count,description}` 无 name；audio `people[]={role,gender,description}`
name 基本为 null，description 自由文本）。直接以 description 建节点会产生单例爆炸，
Phase 1 已否决。但 audio 侧的 **role/gender 是结构化枚举**，可确定性归桶：

```
summary.json → people[] 逐条：
  role == "user"                          → person:user    label "我"
  role == "other" && gender == "female"   → person:female  label "女性联系人"
  role == "other" && gender == "male"     → person:male    label "男性联系人"
  其余（gender null 的 other）              → 跳过（实测多为 AI 助手：14 session 中 9 条
                                            description 为"AI助手/聊天助手/电子女声"等）
```

- ★ **weight 口径（评审修复，决策 9）**：对齐既有 object/scene 的 per-session 去重模式
  （`seen_obj`/`seen_scene`，`service.py:154-164,176-186`）——**每 session 每桶至多 +1**
  （`_accumulate` 内 `seen_person: set[str]`，同 session 同桶多条 people 只计一次）。
  理由：① 与 object/scene 的 presence 语义一致，√weight 半径映射在此口径下已调校；
  ② 按条计数会让 `person:user`（语音条数）增速远超其他 kind，迅速膨胀为全图最大节点。
  按条口径的实测基线为 user 15 / female 4 / male 1（14 session）；per-session 口径的
  数值基线由 P0 验收脚本对真实数据复算并记录于验收结论（预期 user ≈ 已分析 session 全覆盖）
- **真实数据验证**（14 个已分析 session，按条口径）：产出 3 个 person 节点，无单例爆炸，
  无 AI 助手污染；per-session 口径不改变节点集合与建边拓扑，只改变 weight 数值
- **BFF 改动**（`services/bff/graph/service.py`，归后端任务，本文定契约）：
  1. `ENTITY_KINDS` 增加 `"person"`
  2. `_accumulate` 增加上述归桶提取（★ `seen_person` 每 session 每桶 +1；
     `_touch` 与建边逻辑与其余实体通用，零特判）
  3. `_entity_in_session` 增加 person 分支：同归桶规则判定 presence，**返回 1/0**
     （对齐 object/scene 的 presence 语义，service.py:333-343 既有模式），
     支撑实体详情 appearances 与"出现次数"展示
- **不提取 video 侧**：`{count, description}` 无稳定归桶键（description 自由文本），
  明确跳过（§10 风险 1）；具名人物归分析管线 Phase 2，届时 `person:{name}` 节点
  按 kind 通用逻辑零改动接入
- **schema/契约**：`GraphResponse`/`EntityResponse` 结构不变（`kind` 本为自由字符串）；
  Web 端 `EntityKind` union 不含 person——Web 对未知 kind 渲染默认色，不崩，不动
- **部署顺序约束**：BFF 未上线前 Android 勾选 person chip → `types=person` 触发
  400 `bad_request`。**P 阶段排序保证 BFF 先行**（§8）；Android 不对 400 做特判

### 5.2b BFF：event 实体详情（`evt:` 前缀兼容，★ 真机新增）

- event 节点 id 为 `evt:{sid前8}`（`build_graph` 既有格式）；★ 真机起 event 与其他实体
  一样单击开详情，`build_entity` 兼容 `evt:` 前缀：kind 映射 `event`，weight = 对应
  session 事件数，appearances = 1（该 session 一条）
- Android 详情 sheet 内 Play → Replay 深链（sessionId 取自节点/实体，§3.8/§7）

### 5.3 Repository / Api 变更

- `MylifeApi.graph()` 签名不变（BFF 契约不变）；**VM 调用点不再传**
  `dateFrom/dateTo/focus/minWeight`（恒 null/默认 1），LRU key 随之稳定
  （key 中这些槽位恒为 null/1，容量 8 不变）
- `MylifeRepository` 无结构改动；`entity(id)` 不变（person 详情同一端点）
- DTO 零改动（`GraphNodeDto.kind` 为 String，`person` 天然兼容）

### 5.4 删除清单（数据/逻辑层）

- `ForceLayout.kt` 及其测试 `ForceLayoutTest.kt`（力导向整体废弃）
- VM 状态：`dateFrom / dateTo / minWeight / focusId / isListMode` 及对应
  `onDateRangeChanged / onMinWeightChanged / onClearFocus / onToggleFocusForEntity /
  onToggleListMode`；`onNodeClick` 中的 focus 逻辑（保留 event 判定返回语义）

---

## 6. UI 层结构变更清单

```
ui/screens/mylife/graph/
├── GraphScreen.kt            # 重写：控件条（仅 kind chips 行，下划线高亮）+ TimeRiverCanvas + 详情 sheet + "关闭关系" pill
├── GraphViewModel.kt         # 精简重写（§7）
├── GraphUiState.kt           # 重写（§7，含 entitySessionId）
├── TimeRiverLayout.kt        # 新增：纯 Kotlin 时间瀑布布局（§5.1，可单测；FNV-1a 散布）
├── TimeRiverCanvas.kt        # 新增：自绘瀑布（pan 滚动/连续 zoom/单击/长按 1s 手势链 + 随机轮播 + ego 连线 + 标尺/顶部日期）
├── EntityDetailSheet.kt      # 修改：删除"聚焦关系"按钮与 isFocused/onToggleFocus 参数 ★+person 推断说明行 + event Play 按钮
删除：ForceLayout.kt / LifeGraphCanvas.kt / EntityTopList.kt / ReplaySessionListScreen.kt + ViewModel.kt（回放 tab 移除）
测试：删 ForceLayoutTest.kt；重写 GraphViewModelTest.kt；新增 TimeRiverLayoutTest.kt / TimeTicksTest.kt
```

`ui/screens/mylife/MylifeTheme.kt`：`mylifeKindColor` 增加 `"person" -> MylifeColors.Green`。
`MylifeHomeScreen`：★ 真机重写为**双 tab 宿主（时间轴/星图）**，回放 tab 移除；`Routes.MYLIFE`
带 `tab` 参数，深链经 `launchSingleTop` 复用 entry 更新（§4.2）。

---

## 7. ViewModel / UiState 设计

```kotlin
data class GraphUiState(
    // keepPreviousData：仅成功时替换（不变）
    val data: GraphDto? = null,
    val isLoading: Boolean = false,
    val error: String? = null,
    // kind 反选（含 person）；空 = 全部（语义不变）
    val selectedKinds: Set<String> = emptySet(),
    // 长按 ego（客户端边过滤，无请求）；null = 未激活
    val egoId: String? = null,
    // 实体详情抽屉（不变）
    val entityId: String? = null,
    val entity: EntityDto? = null,
    val entityLoading: Boolean = false,
    val entityError: String? = null,
    // ★ event 实体的 sessionId（详情 sheet 内 Play → Replay 用；非 event 为 null）
    val entitySessionId: String? = null,
)
```

`GraphViewModel` 职责：

- `loadGraph()`：仅随 `selectedKinds` 变化触发（防抖沿用 `graphDebounceMs`）；
  请求参数 `types = selectedKinds.csv`，其余恒默认（§5.3）
- `onToggleKind(kind)`：反选切换 + 重新加载；**不再触碰 ego/详情**
  （V1"过滤变更清 focus"逻辑针对服务端 ego 重请求，V2 ego 为客户端过滤，
  被排除的邻居只是不画线，无正确性问题——见 §10 风险 4）
- `onNodeClick(node)`：★ 真机：所有实体（含 event）统一 `openEntity(id)` 并记录
  `entitySessionId`（event 在详情 sheet 内 Play → Replay，§3.8）；**不再设置 focus、
  不再重新请求图**
- `onNodeLongPress(id)`：`egoId = id`（幂等；已激活时切换中心，不退出）
- `onEgoClear(reason)`：`egoId = null`；★ 真机：仅"关闭关系"pill 调用（空白/返回键不再退出）
- `openEntity / closeEntity`：不变（person 详情同一端点）
- 视口状态（`tTop/daysPerScreen`）不入 VM（§3.6，Screen `rememberSaveable`）

---

## ★ 8. 质量横切（评审新增章：性能 / 无障碍 / 遥测 / 回滚）

### 8.1 性能预算（P2 验收标准）

| 指标 | 预算 | 保障机制 |
|---|---|---|
| 滚动/缩放帧率 | 中端机（如骁龙 7 系）稳定 60fps，单帧绘制 <8ms | 无帧循环（§3.3）；visible() 二分裁剪 O(log n + k)；300/窗绘制上限；轮播单 label |
| 首屏可交互 | 数据在缓存命中时 <500ms；冷请求时取决于网络（loading 文案兜底） | 图数据全量一次拉取，无二次请求；布局为一次性纯函数计算 |
| 内存 | 全量图持有 ≤ 数千节点（DTO 常驻，无可回收大对象） | 无 bitmap/无粒子对象；Placed 列表与 DTO 同量级 |

真实数据（121 节点/1282 边）远低于全部阈值；千级节点复测点见 §10 风险 5。

### 8.2 无障碍（本期范围声明）

- 自绘 Canvas 对 TalkBack 天然不可见；EntityTopList（唯一文本浏览路径）随决策 2 删除后，
  **屏幕阅读器用户无法浏览实体**——这是本期已知且被接受的回归，产品已知会（评审记录）。
- 本期最小缓解（必须实现，成本低）：
  1. `TimeRiverCanvas` 根语义：`contentDescription = "时间瀑布星图，共 N 个实体，当前显示
     {中心日期} 前后"`，随视口更新；
  2. ★ 随机 summary 轮播展示的节点同步暴露为 semantics 节点
     （`label，kind，权重，日期`；单节点，TalkBack 可读，§3.4）
- 完整逐个节点导航/虚拟视图归演进（千级节点语义树本身需虚拟化设计，与风险 5 一并考虑）。

### 8.3 遥测（占位接口，不引入新 SDK）

**事实**：App 内无既有 analytics/telemetry 设施（2026-08-22 全仓 grep 确认）。
本期不引入任何上报 SDK，定义注入式接口占位，通道后续统一接入：

```kotlin
interface GraphAnalytics {
    fun onGraphOpen(nodeCount: Int, spanDays: Int)
    fun onZoom(daysPerScreen: Float)                 // 手势结束时上报一次
    fun onLongPressStart()                           // 按下 ≥500ms（进度环出现）
    fun onEgoEnter(nodeId: String)                   // 长按 ≥2s 触发成功
    fun onEgoExit(reason: String)                    // blank_tap / back / pill / switch
    fun onCenterLabelShown(count: Int)               // 滚动停止时采样
}
```

- DI 提供 `NoOpGraphAnalytics`（debug 构建打 log）；`MylifeModule` 绑定，可替换
- **核心用途**：§10 风险 2 的长按复核依赖 `onLongPressStart` vs `onEgoEnter` 的转化率
  （"按住到触发"的完成率）；无此数据复核无法闭环
- 所有事件不含 label 文本/日期等隐私内容（仅计数与档位）

### 8.4 回滚策略

- V2 为删除式重构（ForceLayout/LifeGraphCanvas/EntityTopList 物理删除），**不保留 V1
  双实现、不加 feature flag**（决策 14：双实现维护成本高于回滚收益，且 V2 无新权限/
  新数据通路，爆炸半径限于 Graph tab 单页）
- 回滚路径 = `git revert` V2 MR + BFF person 向后兼容（`ENTITY_KINDS` 多一个 kind 对 V1
  客户端无害：V1 不传 types=person，图中 person 节点走 else 默认色 Dust，不崩）

---

## 9. 实施计划（分阶段，各自独立可交付）

| 阶段 | 内容 | 依赖 | 验收 |
|------|------|------|------|
| **P0 BFF person**（★ 独立后端 MR） | `ENTITY_KINDS` + `_accumulate` 归桶（★ per-session 每桶 +1）+ `_entity_in_session` person 分支（1/0）+ BFF 单测 | 无 | 真实 `/opt/lvdata` 出 3 个 person 节点（user/female/male）；★ weight 数值基线复算并记录；`types=person` 200；entity 详情 appearances 正确；web 回归不崩 |
| **P1 Android V2 主体** | TimeRiverLayout + TimeRiverCanvas（pan 滚动/连续 zoom/随机轮播/长按 1s ego/标尺/顶部日期）+ VM/UiState 重写 + 控件条单行化（+person chip，下划线高亮）+ EntityDetailSheet 删按钮（★+person 说明行 + event Play）+ 删除 ForceLayout/LifeGraphCanvas/EntityTopList + 双 tab 导航（回放 tab 移除）+ ★ GraphAnalytics 接口与 NoOp 绑定 + ★ 最小无障碍语义 | P0（person chip 依赖；无 P0 时可先藏 person chip 开发） | ★§4.1 手势表 + §4.3 叠加状态表逐条过；zoom 连续缩放 [1,366] 锚点正确；随机轮播 3s 节奏与 ego 暂停；长按 1s ego 只画中心↔邻居连线、"关闭关系"退出；event 详情 Play 深链；单测全绿 |
| **P2 打磨** | 长按进度环动画、label 淡入淡出曲线、ego 连线渐入、空态/首启引导文案、密度截断阈值实机调优、★性能预算实测（§8.1）、★长按复核数据评估（§10 风险 2） | P1 | 真实数据手动冒烟：滚动/zoom/单击/长按/深链三向；§8.1 预算达成 |

---

## 10. 风险与开放问题

1. **person 是粗桶而非具名人物**：桶内混多个真实人物（"女性联系人"可能是不同人），
   且 gender-null 的真人会被跳过、video 侧不提取；gender 为 ASR 推断，童声/误分会
   静默归桶。★ 缓解已落地：详情 sheet 推断说明行（§3.8）；产品需确认该精度可接受。
   具名人物依赖分析管线产出（Phase 2），届时 BFF 按 kind 通用逻辑零改动接入，
   Android 零改动
2. **长按阈值与平台惯例（~500ms）冲突**：★ 真机已将阈值收敛为 **1s**（§14 6），1s 进度环
   视觉承诺 + 触发振动已落地；转化率复核（`onEgoEnter / onLongPressStart`，§8.3）仍是
   遗留项——若数据表明 1s 仍过高，进一步降为双击方案（改动仅限常量与进度环时长）
3. **实体定位于 first_seen 的语义 tradeoff**：长寿命实体（如贯穿整月的 topic）只在
   首次出现处可见，滚到它"最近被讨论"的日期看不到它。演进路径：按 appearance
   多点绘制（entity 详情已有 appearances ≤50）或改定位 last_seen——V2 单点
   first_seen，记录不解决
4. **kind 反选时 ego 连线为过滤子集**：被反选排除的 kind 不出现在图中，其边也不画
   （即使与 ego 中心共现）。语义自洽（"只画可见实体间的连线"），但与"全部关系"
   有认知差；如需全集连线，后续可改为 ego 激活时临时豁免过滤（记录，不做）
5. **极端数据增长**：千级节点时默认 30 天/屏仍可能密集；密度截断（300/窗）+
   label 上限（8/屏）已兜底绘制性能与可读性，但"全部一屏"缩放下节点必然重叠
   （星河质感可接受；★ span>318 天后 366 封顶使全量一屏不再可能，§3.6 已声明为
   可读性取舍）；超出阈值后的进一步聚合（如 topic 簇）归演进
6. **`rememberSaveable` 视口恢复**：进程重建后 `tTop` 恢复为保存值，若此时图数据
   尚未加载（tMin/tMax 未知），视口 clamp 延迟到数据到达后执行——实现时注意
   空数据期不应用 clamp（否则恢复到非法位置）
7. **时间戳时区**：布局用 epoch ms 原值，不涉及本地日切分；标尺"周对齐"用
   `WeekFields.ISO` + 设备时区（与 BFF `_local_date` 的 tz 语义差异仅在刻度线
   视觉对齐，不影响数据）

---

## 11. 决策记录

| # | 议题 | 备选 | **终稿** | 依据 |
|---|------|------|----------|------|
| 1 | 日期行删除后 min_weight 输入去留 | 保留并移入 chips 行 | **一并删除** | 需求明确"这一行不需要"；zoom 时间刻度已承担密度调节（需求 4），min_weight 与 zoom 功能重叠；BFF 参数保留，恢复成本为一行 UI |
| 2 | 列表/星图切换（EntityTopList）去留 | 保留 weight Top50 列表 | **一并删除** | 时间瀑布本身即有序列表式浏览，副视图冗余；随过滤行删除无处安放；★ 无障碍回归已在 §8.2 显式记录并知会产品；异议可在评审提出，恢复成本低 |
| 3 | person 提取方式 | description 建节点 / role+gender 归桶 / 不做 | **role+gender 归桶（3 桶）** | description 自由文本→单例爆炸（Phase 1 已否决）；归桶确定性、真实数据验证 3 节点无噪声；不做则不满足需求 2 |
| 4 | person gender-null other | 建"其他联系人"桶 | **跳过** | 实测 9 条全为 AI 助手类描述，建桶会被 AI 污染 |
| 5 | ego 连线数据来源 | `?focus=` 重请求（V1 语义） | **客户端过滤已加载 edges** | 边随图加载零成本；长按是高频探索手势，每次重请求 + keepPreviousData 闪烁体验差（★ d4 稿"长按→clearFocus→全图重取→进 ego"的串联网络依赖与竞态由此消除）；V1 focus 重请求本质是为邻居高亮取 ego 子图，V2 全图已在本地 |
| 6 | 纵轴方向 | 旧在上（顺叙）/ ★横轴时间（d4 稿） | **新在上（倒叙）纵轴** | 与 Timeline V2 倒序一致；初始视口即最新生活；★ 竖向滚动是手机主手势，横轴时间与垂直 pan 拆分会割裂手势认知（d4 稿方案） |
| 7 | zoom 缩放维度 | 双轴几何缩放（V1） | **仅纵轴时间刻度** | 需求 4 明确"时间刻度比例缩放"；横轴无语义，缩放无意义；★ 节点半径屏幕空间恒定（d4 稿"半径×scale"为 V1 几何语义残留，废弃） |
| 8 | 长按阈值 | 平台默认 ~500ms | **2000ms + 进度环** | 需求原文（2 秒以上）；★ 复核机制闭环：P2 依据遥测转化率决策降 1s/改双击（§10 风险 2） |
| ★9 | person weight 口径 | 按 people 条数 +1 | **每 session 每桶 +1（seen_person 去重）** | 对齐 object/scene 既有 presence 口径（service.py:154-164,176-186）；按条计数下 person:user（语音条数）增速远超其他 kind，√weight 半径映射下会膨胀为全图最大节点 |
| ★10 | person 推断的 UI 表达 | 不标注 | **详情 sheet 加推断说明行** | gender 为 ASR 推断、桶合并多人，无标注会让用户把推断当事实；成本一行文案 |
| ★11 | person chip 的后端未就绪容错 | Android 捕获 400 + message 子串匹配（d4 稿） | **不做客户端容错，部署排序保证 BFF 先行** | message 文案匹配与后端硬耦合，脆弱；上线"点了必报错"的 chip 是反模式；P0 独立 MR 先行消除整个分支 |
| ★12 | 时间方位反馈 | 无（d4 稿） | **标尺 + 中心日期 pill** | zoom 被定义为时间刻度缩放，用户必须有渠道知道当前时间范围，否则需求 4 不闭环 |
| ★13 | 遥测 | 不做 / 引入 SDK | **GraphAnalytics 占位接口 + NoOp** | 全仓无既有埋点设施（2026-08-22 grep 确认）；不引入新依赖，接口占位供长按复核（风险 2）与后续通道接入 |
| ★14 | 回滚策略 | V1/V2 双实现 + feature flag | **删除式重构，回滚=git revert** | 爆炸半径限于 Graph tab 单页；双实现维护成本高于回滚收益；BFF person 对 V1 客户端向后兼容 |

---

## 12. 事实基线锚点（2026-08-22 核对）

| 事实 | 锚点 |
|------|------|
| V1 Graph 控件条两行/手势/单击语义 | `ui/screens/mylife/graph/GraphScreen.kt:65-148,211-222` |
| V1 力导向参数与停止条件 | `ui/screens/mylife/graph/ForceLayout.kt:11-16,227-234` |
| V1 zoom k∈[0.2,4]/质心锚/命中半径/6dp 点击阈值 | `ui/screens/mylife/graph/LifeGraphCanvas.kt:33-41,85-86,124-133` |
| V1 VM 全量状态（date/minWeight/focus/list/防抖/keepPreviousData） | `ui/screens/mylife/graph/GraphViewModel.kt:34-54,76-82`；`GraphUiState.kt` |
| 详情 sheet"聚焦关系"按钮（删除对象）与 ★权重行位置（person 说明行插入点） | `ui/screens/mylife/graph/EntityDetailSheet.kt:87-105` |
| GraphNode 字段（first_seen_ms/last_seen_ms/session_id）与边 kind | `data/remote/mylife/dto/MylifeDtos.kt:122-139`；`services/bff/common/schemas.py` |
| graph LRU（8 条/5min）与 API 参数 | `data/repository/MylifeRepository.kt:85-119`；`data/remote/mylife/MylifeApi.kt:45-63` |
| BFF ENTITY_KINDS 无 person、types 未知 kind → 400 | `services/bff/graph/service.py:17,60-65` |
| 边构建（event↔实体、实体↔实体 session_cooccur）与 ego 子图 | `services/bff/graph/service.py:188-193,221-246` |
| ★ 节点 weight 异质口径现状（event=事件数/topic=mentions/object、scene=per-session 去重/边=共现 session 数） | `services/bff/graph/service.py:132-193`（seen_obj/seen_scene 模式 :154-164,176-186；`_bump_edge` 去重 :210-218） |
| person 数据源形态（audio role/gender/description；video count/description）与 Phase 1 否决结论 | `docs/mylife/mylife-design.md:110-116,245-264` |
| person 桶真实数据分布（按条口径 user15/female4/male1/skip9；14 session） | 2026-08-22 对 `/opt/lvdata/derived/*/*/summary.json` 实测脚本统计；★ per-session 口径基线由 P0 验收复算 |
| 图规模真实数据（121 节点/1282 边/29 天；kind 分布） | 2026-08-22 `build_graph` 对 `/opt/lvdata` 实测 |
| graphDebounceMs DI | `di/MylifeModule.kt:69-72` |
| kind 色板与 chips 样式（★ Green #6EF5A8 未被 kind 占用，逐行核对） | `ui/screens/mylife/MylifeTheme.kt:16-41` |
| scope/定位 pill 样式先例（ego "✕ 关系" pill 复用） | `docs/mylife/timeline-android-v2.md:153-155,187-188` |
| 深链路由与 launchSingleTop 机制（不变） | `ui/navigation/Routes.kt:13-21`、`NavGraph.kt:105-149` |
| V1 测试基线（ForceLayoutTest 删除、GraphViewModelTest 12 用例重写） | `src/test/.../graph/ForceLayoutTest.kt`、`GraphViewModelTest.kt:64-182` |
| HapticController 既有能力（长按触发反馈复用） | `media/HapticController.kt` |
| `WeekFields.ISO` 平台自带（minSdk 26，标尺周对齐无新依赖） | `docs/mylife/timeline-android-v2.md:576` 同一事实 |
| ★ App 内无既有 analytics/telemetry 设施（§8.3 占位接口的依据） | 2026-08-22 全仓 grep `(analytics|telemetry|logEvent|trackEvent)` 无匹配 |

---

## 13. 测试计划

- **`TimeRiverLayoutTest`**（新增，重点；12 用例）：
  - 排序与时刻：`first_seen` 优先、`last_seen` 兜底、双空归 `tMin`；按 t 降序
  - x 散布确定性：同输入同输出；同 6h 桶黄金角错开（相邻序号 xFrac 差 ≈0.618）；
    ★ xFrac 与视口状态无关性（不同 tTop/daysPerScreen 下 Placed 不变）；★ FNV-1a 均匀性
  - `visible`：窗口边界裁剪、>300 时 weight Top 截断（确定性次序）
  - `nodeAt`：命中半径规则（max(半径, 22dp) 等效世界坐标换算）、最近节点选取、
    ★ 归一化椭圆距离闸门（横向超程不命中）
  - `egoEdges`：只含 source/target 为该 id 的边、边权透传、未知 id 空列表
  - ★ `centerLabels` 用例已删（机制移除，§3.4）；★ 新增 `providedTMin/TMax` 显式参数用例
  - `spanDays`：单点数据 span=0 → 1（zoom 上限计算不死）
- **`GraphViewModelTest`**（重写，10 用例）：
  - 保留适配：init 加载、kind 过滤 types csv、keepPreviousData、**event 点击开详情
    （断言不再带 focus 重请求、entitySessionId 记录）**
  - 新增：`onNodeLongPress` 设置/切换 egoId、`onEgoClear(reason)`、kind 切换不清 ego/详情
  - 删除：日期范围、minWeight、focus/退出聚焦、列表切换相关 5 用例
- **BFF `test_graph_api`**（追加）：person 归桶（user/female/male/跳过 gender-null）、
  ★ per-session weight 去重（同 session 同桶多条 people 仅 +1）、
  `types=person` 过滤、person entity 详情计数（presence 1/0）、未知 kind 仍 400
- **UI 冒烟（androidTest）**：滚动改变顶部日期标签；捏合 zoom 连续改变节点密度；
  长按 1s 出现连线、"关闭关系" pill 退出；随机轮播 3s 出现气泡、ego 激活暂停；
  ★ ego 激活中开 sheet、关 sheet 后 ego 仍在（§4.3 叠加表）
- **手工验收**：真实 `/opt/lvdata` 数据；1s 长按体感（含进度环提示有效性）；
  极缩（1 天/屏）与极放（全跨度/屏）两端可用性；★ §8.1 性能预算实测；
  ★ TalkBack 下画布根语义与中心带 label 可朗读

---

*Design version: 2.1-final | Created: 2026-08-22 | 由 d4/k3 双稿评审合并：以 k3 为基线，修复 weight 口径、person 推断标注、状态叠加仲裁、fling 语义、边缘裁剪、遥测占位、无障碍范围、回滚策略；BFF person 为独立先行 MR。文中 ★ = 相对 k3 终稿的评审增量。*

---

## §14 实施记录（2026-08-22 真机反馈后的最终行为）

本文档为实施契约，以下为实施完成后的**最终行为**（含真机反馈迭代），与正文差异以此为准：

| # | 项目 | 最终实现 | 与正文差异 |
|---|------|----------|-----------|
| 1 | 横轴散布 hash | **FNV-1a 32 位**替代 `id.hashCode()` | §5.1 公式对结构化 id（topic:/object:/evt:…）实测聚拢成竖线，FNV-1a 雪崩充分（5000 样本 >2/3 占比 0.34 ≈ 1/3），确定性语义不变 |
| 2 | 滚动 | `detectTransformGestures` pan 单手势链 | `scrollable` 在 Canvas 上与兄弟 pointerInput 事件竞争失效；V1 同构 pan 方案验证可用；fling 惯性暂缺（后续 velocityTracker） |
| 3 | zoom | **连续缩放** `daysPerScreen ∈ [1, 366]`，质心时刻锚定 | 真机要求丝滑连续（非三档离散）；默认 **30 天/屏**（月档，一屏看全） |
| 4 | 顶部时间标注 | **左上角**固定显示当前视口顶缘日期（Gold，滚动/缩放实时更新） | 中心 pill 移除；画布顶部 30dp 标签区同时为节点下移留空间 |
| 5 | 滚动边界 | 数据范围内 `[tMin + 一屏, tMax]`（offsetMs/radiusMs 补偿节点不贴边被裁）+ **底部多留一屏空白页** + 居中"已经到达底部"提示 | 数据范围外不留空白（真机）；底部空白页作"到底"视觉指示 |
| 6 | 长按 ego | 阈值 **1s**；**退出仅"关闭关系"按钮**（空白单击/返回键不退出）；长按另一节点切换中心 | §3.5 的 2s 与四退出路径收敛 |
| 7 | entity summary | **随机轮播**：每 3 秒从当前页可见实体随机选一个显示气泡（filter 生效）；ego 激活时暂停，退出后恢复 | §3.4 中心带机制（±15% 带 + 渐隐 + 贪心去重叠）删除；`TimeRiverLayout.centerLabels` 移除 |
| 8 | ego 连线 | **always 绘制**（邻居不在可视区也画线，线段延伸窗外由 Canvas 裁剪） | §3.5 窗内过滤移除 |
| 9 | ego 交互 | 激活时**仅关系成员**（ego 中心 + 有连线邻居）可点击，其余忽略 | §3.5"单击邻居开详情"扩展为关系成员过滤 |
| 10 | event 实体 | **单击开详情**（与其他实体一致），详情 sheet 内 **Play 按钮** → Replay 深链 | §4.2 event 单击直连 Replay 改为详情内 Play；BFF `build_entity` 兼容 `evt:` 前缀（kind 映射 event），event 详情 weight = 对应 session 事件数、appearances = 1 |
| 11 | kind chips | 单行 LazyRow 横向滑动；顺序 **topic/event/person/object/scene/emotion/app**；选中 = **下划线 + 高亮** | 多行 FlowRow/All 项经真机测试后还原单行；删除线样式改下划线 |
| 12 | 导航 | MyLife **双 tab**（时间轴/星图）；回放 tab 移除，Replay 从时间轴 hot slot / 星图 event Play 深链进入 | `ReplaySessionListScreen/ViewModel` 删除 |
| 13 | 刻度标注 | **每条刻度线左端标注自己的日期**（除贴顶标签区的线） | 顶部线/下一条线等特殊标注方案经真机迭代后恢复初始方式 |
| 14 | 命中换算 | 与绘制一致含顶部标签区偏移：`t = tTop − (y − pad) / pxPerMs` | 无 pad 补偿时点击/长按偏位约 90px 落空（真机 bug） |
| 15 | Replay 列表 | `distinctBy { sessionId }` 去重 | BFF overview 跨天 session 多天出现 → LazyColumn key 冲突 crash（真机） |
| 16 | 遥测 | `onCenterLabelShown` 接口保留、不再调用（中心带机制删除） | §8.3 |

**测试基线**：BFF `test_graph_api.py` 23 用例（含 person 归桶/去重/详情、event evt: 详情）；Android `TimeRiverLayoutTest` 12、`TimeTicksTest` 5、`GraphViewModelTest` 10。

**遗留**：fling 惯性、§8.1 性能预算实机复测、§10 风险 2 长按转化率复核（阈值已按真机改为 1s）。
