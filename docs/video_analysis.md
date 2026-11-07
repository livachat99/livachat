# 视频分析 prompt 精简：filter 阶段（已实施）+ extract 字段精简与 place

> 状态：**filter 阶段已实施**（2026-09-18，提交 `5ed88a9`；S1–S4 完成、两包测试
> 全绿含 review 修复、dev 实跑验证——见 §8 收尾记录）；**extract 阶段（§9）
> S0–S3 已实施**（探针 3 连轮通过 → 生产改造 + 两包全绿；S4 dev 验证待拍板）。
> filter 范围：lvdata-mcp vision filter 阶段（prompt + `FRAME_KEYS`/normalize +
> 窗口事件条件 + **bff 日卡排序替换** + 测试 + 文档）；不动窗口分组/预算/重试
> 机制、不改 events 表结构。

> **filter schema 第二轮（§10：`duplicated`/`key_frame`）——已实施**（2026-10-07；
> 拍板 D1–D6 见 §10.0、收尾见 §10.8；§0–§9 中"5 键 schema / 派生 is_key_frame"
> 的描述由 §10 取代）。

> **filter schema 第三轮（§11：`duplicated` → `scene_id`）——已实施**
> （2026-10-07；提案/探针/决策/收尾见 §11.1–§11.3/§11.4/§11.7）；
> **跨窗口 scene 支持计划已入档 §12（未排期：先不实现，未来再 plan）**。

> **提案来源（用户，2026-09-18）**：① "屏幕截图" → "图片"；② window_caption 描述
> "屏幕内容" → "图片内容"；③ 帧层 schema：`quality_issues`(list) → `quality`
> (low|high)、**移除 `novelty`**、**新增 `value`(low|high)**（模糊/低质/镜头移动
> 过渡画面/非关键帧 → low）；④ **无关键帧窗口不产任何 event**。

> **2026-09-18 一轮评审意见已并入**：V5 清理口径扩展为**两类残留**（无关键帧窗口的
> window 行 + 降级帧的 video 行，§4/§5）；术语计数校正（filter：屏幕截图×2 +
> 屏幕内容×1；extract：1 处，§0/§3/V4）；§4 补 v1 工具范围外注记与"整窗静止但
> 有价值内容"边缘项；prompt 草案删"数组字段"句、null 限定为 duplicate_of/key_rank、
> value 改述为**综合判定**（§2.1）；§2.2 补不变式的"剩余信号"说明。

> **2026-09-18 二轮（用户决策，待 review）**：① **删除 `key_rank`**——window 内
> 排序无意义；其唯一承重消费方（bff 日卡缩略图排序）随之替换（§4/§5 V7）；
> ② **`is_key_frame` 不再由模型输出**——简化为**代码派生：`value == "high"` 且
> 非重复帧**（二轮探针实测模型对 is_key_frame 的遵循不稳定：重复帧仍标 key；
> 派生把一致性收敛到定义本身，§2.2）。探针同步更新后重跑验证。

> **2026-09-18 三巡（拍板与实测验证）**：**V7 拍板 =「按 window 去重取每窗口
> 首个派生关键帧 → ts 升序」**（用户 2026-09-18）；V1/V3/V4/V5/V6 按推荐执行。
> 探针（精简草案 + 派生）**3 连轮**（run 3/4/5）实测：派生关键帧集合逐帧一致
> （窗口 1={01,13}、窗口 2={00,05}）、**零枚举越界、零全标退化**、事件数恒定
> 2 window + 4 video；旧草案的"每轮一个窗口全标"失败模式消失（§7.6/§7.8，
> 原始输出 /tmp/probe_run3-5.txt）。

## 0. 提案要点

1. **术语**：prompt 内 "屏幕" 字样共 3 处（"屏幕截图"×2 + "屏幕内容"×1，已按原文
   逐字计数校正）→ 统一改 "图片"；`window_caption` 描述
   "该时间窗口内**图片**内容的一句话概括"。
2. **帧层 schema 换血（二轮更新）**——最终键 = `frame_id / quality /
   duplicate_of / value / reason`（5 键）：
   - `quality_issues`(列表) → `quality: "low" | "high"`（图片本身画质：模糊/
     黑屏/花屏等低质 → low）；
   - **移除 `novelty`**——与 `duplicate_of` 语义重叠、无消费方；
   - **新增 `value: "low" | "high"`**——本帧总体价值：画质高且非重复/有信息量 →
     high；模糊、低质、镜头移动中的过渡画面、重复内容 → low；
   - **删除 `key_rank`**（二轮决策）——window 内排序无意义；唯一承重消费方
     bff 日卡排序替换见 §4/V7；
   - **`is_key_frame` 不再由模型输出**（二轮决策）——改为**代码派生：
     `value == "high"` 且 `duplicate_of` 为空**（"is_key_frame 的判断简化"）。
3. **行为变化**：窗口的**派生**关键帧集为空 → **不再产 window 事件**（文件仍标
   ok、审计 JSON 照写）。

## 1. 现状（改动前）

| 项 | 位置 |
|---|---|
| filter prompt | `tools/lvdata-mcp/src/lvdata_mcp/analysis/vision.py:51-70` |
| 帧键清单 | `vision.py:46-47`（`FRAME_KEYS`：frame_id/blur/quality_issues/duplicate_of/novelty/is_key_frame/key_rank/reason，8 键） |
| 帧归一化 | `vision.py:161`（`normalize_frame_entry`） |
| filter 解析校验 | `vision.py:193`（`parse_filter_output`：`FRAME_KEYS` 存在性 + 索引完整性 0..n-1） |
| 窗口事件 upsert | `vision.py:362-372`（**无条件**；payload 含 `static = len(key_ids)==0`） |
| 无关键帧分支 | `vision.py:379-385`（只标 ok 返回，事件已在上面写过） |
| extract prompt | `vision.py:72-89`（本次不动；术语同步见 V4） |

## 2. 新 schema 与 prompt 草案

**新 `FRAME_KEYS`（5 键，二轮更新）**：`frame_id / quality / duplicate_of /
value / reason`；**`is_key_frame` 为派生量、不入模型 schema**。

### 2.1 filter prompt（实施版草案，逐字，待 review）

```text
你是图片序列分析引擎。你只输出 JSON，不输出任何其他文本或解释。
用户提供按时间顺序排列的 N 张图片（图片之间以 "frame <index>" 文本标记索引，从 0 开始）。
请逐帧判定并严格按照以下 JSON Schema 输出：
{"window_caption": "该时间窗口内图片内容的一句话概括", "frames": [{"frame_id": "必须使用用户提供的图片索引字符串", "quality": "图片画质 low|high（模糊、黑屏、花屏等低质 → low）", "duplicate_of": "与窗口内前面某帧内容几乎相同则填该帧的 frame_id，否则 null", "value": "本帧总体价值 low|high（画质高、非重复、有信息量 → high；模糊、低质、镜头移动中的过渡画面、重复内容 → low）", "reason": "一句话理由"}]}
要求：每个输入的 frame_id 都必须有且仅有一个条目，不得遗漏、不得编造索引；若整窗口无显著变化，允许所有帧 value=low；只输出 JSON，不要 Markdown 代码块。
```

### 2.2 归一化与派生口径（二轮更新）

- `quality` / `value`：`str.lower().strip()`；∉{low,high} → **"low"**（降噪优先，
  含堵实测出现过的 `medium` 越界）。
- `duplicate_of`：null/缺失 → None；先经 `_norm_frame_id` 归一（与 frame_id 同
  规则：`"frame 2"`→`"2"`、`"00"`→`"0"`），**非空一律强转字符串**——模型可能
  输出 int 索引（`0` 是假值陷阱，派生判定必须安全）；**空串与字面量
  `"null"/"none"` 等价 None**（review 修复，2026-09-18：否则 value=high 的帧被
  静默误判为非关键帧）。
- **`is_key_frame` 派生（代码计算，不入模型 schema）**：
  `is_key_frame = (value == "high") and (duplicate_of is None)`。
  派生把一致性收敛到定义本身——二轮探针实测的"重复帧仍标 key / key 但 rank 缺失"
  类矛盾**从根上消除**（模型两轮对 value/duplicate_of 的输出均稳定）。
- 原 V2「非关键帧→value=low 不变式」由派生替代（重复帧的 value=low 由 prompt
  约束承接）。

## 3. 代码触点

| 位置 | 改动 |
|---|---|
| `vision.py:46` `FRAME_KEYS` | → 5 键：`frame_id / quality / duplicate_of / value / reason`（`parse_filter_output` 的存在性校验自动跟随；**另拒绝编造的多余索引**——§8 review 修复） |
| `vision.py:51` filter prompt | 换 §2.1 草案（删 is_key_frame/key_rank） |
| `vision.py:161` `normalize_frame_entry` | 删 blur/quality_issues/novelty/key_rank 分支；quality/value 归一；**duplicate_of 强转 str**；**新增派生的 `is_key_frame` 计算** |
| `vision.py:345/387` key_ids / rank_by_row | key_ids 改用派生结果；**`rank_by_row` 机制整体删除**（extract 事件 payload 不再带 key_rank） |
| `vision.py:362-372` 窗口事件 | **条件化**：仅派生关键帧集非空时 upsert；payload 的 `static` 字段推荐移除（V3） |
| `vision.py:379` 无关键帧分支 | 保留（全帧 mark ok 返回）；"无关键帧"= 派生集为空 |
| 审计 JSON（windows/frames） | 照写；`frames` 条目记录**派生后的** is_key_frame（不再有 key_rank） |
| **bff `_derive_images`**（`services/bff/timeline/service.py:115-131`） | **排序键替换**——key_rank 消失（方案见 V7）；Android 仅"保持 BFF 顺序"、无代码改动（DTO 注释陈旧） |
| `vision.py:72` extract prompt | 本次不动；"屏幕截图"（1 处："屏幕截图内容分析引擎"）术语同步见 V4 |

## 4. 调研结论与下游影响

**消费方扫描（2026-09-18，全仓）**：
- 被删/改字段（`blur` / `quality_issues` / `novelty`）：**全仓无消费方**
  （仅 producer 自身 + 测试 fixture）——安全。（v1 工具
  `tools/data-analysis/lvdata_analyze_video.py`、`probe_qwen_image.py` 含同款
  schema，按先例**范围外**——已被 lvdata-mcp 取代。）
- `duplicate_of`：**派生 `is_key_frame` 的输入之一**（代码消费）+ 提示词用它支撑
  "重复帧不算关键帧"——保留。
- `is_key_frame`：**无外部消费方**（仅 lvdata 内部 + 测试）——改派生安全。
- **`key_rank`（二轮删除）**：唯一**承重**消费方 = **bff `_derive_images` 日卡
  缩略图排序**（`timeline/service.py:119`：`(None-last, rank, ts)` 升序取前 3）；
  Android 仅"保持 BFF 顺序"展示；v1 工具范围外。删除后排序键需替换（V7）——
  **这是本次改动唯一的外溢成本点**。
- `static`：**bff replay 读**（`services/bff/replay/service.py:99`，window cue 的
  `static` 承载）——字段缺席时 `e.get("static") → None` 容缺（`ReplayCue` 为
  `FlatEventModel` extra=allow，无 schema 改动）。
- `key_frame_count` / `frame_count`：无消费方。
- `window_caption`：bff search 读（`search/service.py:74` 的 caption 回退）——窗口
  事件对"有关键帧"的窗口仍存在，检索不受影响。

**行为变化面**：
| 面 | 变化 |
|---|---|
| window 事件数 | 无关键帧窗口退出——连续静止/低质时段的事件消失（符合降噪意图） |
| bff replay | 该时段不再有窗口级 cue（原本是 caption 字幕 + `static` 标记）；有关键帧窗口不受影响 |
| day_summary / FTS / 向量 | 文本源 = `events.summary`——无关键帧窗口的 caption 不再进日总结/检索 |
| 客户端 | 帧层 payload 字段换血（含 key_rank 移除）——extra=allow 直通，Android 按 2026-09-15 裁定后期适配；时间线聚合仅保持 BFF 顺序、无代码改动 |
| **bff 日卡缩略图** | key_rank 消失 → **排序键替换**（V7）；混合期（旧行带 rank、新行无 rank）由新排序规则统一处理（旧行随 force 重跑/自然更替收敛） |
| 旧数据 | force 重跑**不会清理两类残留**：① 无关键帧窗口的 window 事件（`static=true`）：不再被重建；② **重跑后降级为非关键帧的帧**，其旧 `{rs}:video:{frame_id}` 事件（extract 不再 upsert 它）；两类残留行内还带着旧字段（`key_rank`/`is_key_frame`）——随清理一并收敛。清理口径见 V5 |

**边缘项（接受，评审记录）**：整窗完全静止且内容本身有价值（如长时间阅读一份
文档）→ 派生关键帧集为空 → 整段无事件（caption 由下一"有变化窗口"承接）；纯
idle 屏幕无事件符合降噪目标。

## 5. 待拍板

| # | 问题 | 推荐 |
|---|---|---|
| V1 | `blur` 字段去留 | **并入 quality**（"模糊 → low"由 quality 承载）；帧键最终 **8→5**（叠加二轮：删 novelty/key_rank、is_key_frame 改派生）；备选：保留 blur 独立字段 |
| V2 | `is_key_frame` 派生定义（二轮决策） | **`value == "high" and duplicate_of is None`**（代码派生，不入模型 schema）；矛盾输出（`duplicate_of` 非空但 value=high）按**"重复优先"派生为非 key** |
| V3 | window 事件 payload 的 `static` 字段 | **移除**（新数据恒有关键帧，恒 false 无意义；bff 容缺已验证） |
| V4 | 术语同步范围 | EXTRACT prompt 的"屏幕截图"（1 处，"屏幕截图内容分析引擎"）**同步改"图片"**（术语一致） |
| V5 | 旧数据 | dev：force 重跑 video + 清理**两类**残留——① 无关键帧窗口的 window 行；② 降级为非关键帧的帧的 video 行；生产自然更替（帧只分析一次，无此残留） |
| V6 | `quality`/`value` 是否进 `_video_summary_text` 拼装 | **不进**（拼装保持 caption/scene/apps/ocr/actions；质量字段是元信息，不应污染检索文本） |
| V7 | **bff 日卡排序替换**（key_rank 删除的连带） | **已拍板（用户 2026-09-18）：按 window 去重——每窗口取首个派生关键帧（ts 最早）→ ts 升序取前 N**；无 `window_id` 的旧行兜底按 event_ref 视作独立时刻。改动点 `timeline/service.py:119` + 其测试断言。**⚠ 2026-09-20 部分被 supersede**：「每窗只取 1 张」已改为**轮转选取**（第 1 轮每窗 1 张、窗口少时逐轮补满至池 15；输出仍 ts 升序），见 `docs/android_timeline_image_gallery.md` §3.2 Q3/E2；`window_id` 兜底口径沿用 |

## 6. 实施步骤（拍板后）

1. `vision.py`：`FRAME_KEYS` + prompt（+ V4 的 extract 术语）+ `normalize_frame_entry`
   （删 key_rank 分支、`duplicate_of` 强转 str、**派生 `is_key_frame`**）+
   key_ids/`rank_by_row` 调整（后者整体删除）+ 窗口事件条件化（+ V3 的 payload 调整）。
2. **bff `_derive_images`**：排序键替换（V7）+ 对应测试断言更新。
3. 测试（lvdata）：`_filter_json` fixture 换 5 键 schema；新增用例——**派生规则**
   （value=high∧非重复 → key；duplicate 非空 → 非 key 即使 value=high）、
   `duplicate_of` int 强转、**派生关键帧集为空 → 零事件**（窗口审计照写、文件全
   ok）；删除 key_rank 断言（`test_vision.py:235`、`test_tools.py:168`、conftest
   视频/窗口 fixture）；全量绿。
4. 测试（services）：`tests/unit/bff/test_timeline_api.py:101` 排序断言按 V7 更新；
   bff conftest 视频事件 fixture 去 `key_rank`；全量绿。
5. 文档：`docs/data/lvdata_video_analysis.md` 修订裁定 + `docs/data/lvdata_mcp_service_v2.md`
   注记。
6. 验证：**探针更新为新草案 → 多轮重跑**（观察：value/duplicate_of 稳定性、派生
   关键帧数、"静窗零事件"）→ dev 重启 + 走 REST 触发全量重跑（MCP 的 `trigger_analysis`
   已删除，见 `issue_list.md` I-3）：
   `curl -X POST -H "Authorization: Bearer $LVDATA_MCP_TOKEN" -H 'Content-Type: application/json' -d '{"stages":["video"],"force":true}' http://127.0.0.1:8090/api/analysis/trigger`
   → 核对窗口/关键帧划分、新 schema 事件、两类残留量（V5）与 bff 日卡。

## 7. 评估意见（评审）

1. **方向认可**：与 audio 三轮同一哲学——降噪 + 把"是否值得保留"收敛为显式
   value 判定。`novelty` 与 `duplicate_of` 语义重叠且零消费方，删除干净。
2. **行为变化的边界已核实**：唯一外部消费方是 bff replay 的 window cue（容缺），
   其余（搜索/日总结/审计）不受损；无关键帧窗口本就是"屏幕没变化"的时段，
   不进事件流符合其信息价值。
3. **quality 与 value 判据有意重叠**（模糊会同时压低两者）：prompt 用"画质"与
   "信息价值"两种措辞区分，但模型输出预计高度相关——接受（二者本就强相关）；
   观察期留意"value=high 而 quality=low"的矛盾输出占比。
4. **无关键帧窗口的事件消失后**，`static` 字段失去载体（V3 推荐移除）；旧数据的
   残留清理与 audio 迁移同款（V5）。
5. **value 的综合属性已明示**（§2.1/§2.2）。观察期指标建议：value=high 占比、
   V5 两类残留量（清理前后对比）。
6. **二轮两项决策方向认可**：删 `key_rank` 消除了"模型输出不稳 × 承重下游排序"
   的组合风险——外溢成本集中为 bff 一次排序替换（V7）；派生 `is_key_frame` 把
   实测的"重复帧仍标 key"类矛盾**从根上消除**（不再请求模型输出可推导字段）。
   探针证据（两轮）：`is_key_frame` 遵循不稳定，而 `value`/`duplicate_of` 输出稳定
   ——派生是数据驱动的最优简化。
7. **混合期口径**：旧行带 key_rank、新行无——由 V7 新排序统一；Android 无代码改动
   （仅 DTO 注释陈旧，随后期适配统一清理）。

## 8. 收尾记录（2026-09-18）

**已完成**（见 video_analysis_plan.md S1–S3）：
- `vision.py`：`FRAME_KEYS` 5 键；filter prompt 换三巡精简版（与本文档 §2.1
  逐字一致，程序化核对）；`normalize_frame_entry` 重写（quality/value 归一、
  `duplicate_of` 强转 str、**派生 `is_key_frame`**）；`rank_by_row` 机制整体删除；
  窗口事件条件化（派生关键帧集为空 → 不产事件）；window payload `static` 移除
  （V3）；extract prompt 术语同步（V4）；模块 docstring 更新；
- bff `_derive_images`：**V7 排序替换**（window 去重取每窗口首个关键帧 → ts 升序
  取前 N；无 `window_id` 的旧行按 event_ref 兜底）；
- 测试：**lvdata 168 绿**（fixture 5 键化；新用例：派生规则/矛盾输出重复优先、
  int-0 强转、静窗零事件；key_rank 断言清理含 conftest/test_tools）；
  **services 1055 绿**（日卡排序断言按 V7 更新 + 同窗去重新用例 + conftest
  fixture 去 key_rank/static）；
- 文档：data 两份修订裁定（`lvdata_video_analysis.md`、`lvdata_mcp_service_v2.md`）。

**dev 实跑（2026-09-18 完成，用户拍板全量执行）**：重启 lvdata-mcp + `force=true`
重跑 38 帧 → **2 窗口 / 38 帧 / 0 失败**；窗口事件 payload 实证无 `static`、新
video 事件无 `key_rank`（quality/value 在窗口审计）；V5 清理 8 条降级帧残留
video 行（备份 `/tmp/video_stale_rows_backup_20260918.json`）+ `reindex --vectors`
（FTS/向量对账 113 chunks）；启动 livachat-server → **日卡 images = 2**（每窗口
首个关键帧 3efe/91d5、ts 升序）——V7 实测正确。

**dev 实跑发现并修复的 bug（extract 解析兼容）**：首轮 force 两窗口全败——
extract 阶段模型把 `frame_id` 回显成 marker 原文 `"frame 0"/"frame 1"`（旧/新
prompt 首行均复现，与本次改动无关、属模型当下行为），旧解析裸 `str()` 比对 →
缺索引 → 重试耗尽整窗 failed。修复：解析器侧 `_norm_frame_id` 归一（剥离
"frame " 前缀，`"0"/0/"frame 0"` 三态均接受；filter/extract 两处同改）+ 回归
用例——**lvdata 169 绿**；filter prompt 未改动（保持 3 连轮验证版）。

**review 修复（2026-09-18，2 处）**：
1. **编造索引崩溃**（严重）：`parse_filter_output` 原来只查缺索引不查多余索引；
   模型多输出一个 `frame_id`（如窗口 3 帧输出 `"3"`）且 value=high → `key_ids`
   含 `"3"` → `rows[int("3")]` **IndexError 未捕获，崩掉整个 video stage**
   （非单窗失败）。修复：`unexpected frame indices` 校验（与缺索引同走
   ValueError→retry→整窗 failed 路径）；`_norm_frame_id` 补纯数字串规整
   （`"00"`→`"0"`）。
2. **关键帧静默丢失**（中）：`duplicate_of` 为空串或字面量 `"null"/"none"` 时
   被当作非空（"重复帧"）→ value=high 的帧派生为非关键帧、事件/日卡均丢图。
   修复：空串/`"null"/"none"`（不区分大小写）→ None。
回归用例 +3（parse 拒绝编造索引 / nullish duplicate_of / 编造索引整窗 failed
不崩）——**lvdata 172 绿**；filter prompt 与 schema 未变。

---

## 9. extract 阶段精简（2026-09-18 第二轮，S0–S3 已实施）

> **提案来源（用户，2026-09-18）**：① 移除 extract 的 `scene` / `apps` / `events` /
> `actions` 四个字段；② 新增 `place`（用户原文："根据图片推测地点, 不确定就是null.
> 如果是屏幕截图，也是null"）。范围：lvdata-mcp vision **extract 阶段**（prompt +
> `EXTRACT_KEYS` + normalize + summary 拼装 + 测试 + 文档）；filter 阶段（§0–§8，
> 已实施提交 `5ed88a9`）不动；不改 events 表结构。

### 9.0 决策拍板（用户 2026-09-18，按推荐执行）

| # | 问题 | 拍板 |
|---|---|---|
| D1 | place 是否进 summary 拼装（FTS/向量/日总结输入） | **进**——否则 place 无消费出口，价值缩水 |
| D2 | 图谱 scene/app 节点在新数据上消亡 | **接受**；place 图谱 kind 需前后端联动，**下轮再说** |
| D3 | extract prompt "1-4 张关键帧**截图**" → "**图片**" | **改**（与 filter 术语统一；上轮 V4 只改首句的遗留） |
| D4 | 探针先行 | **必要**（S0；沿用 filter 改动前探针先例） |

### 9.1 新 schema 与 prompt 草案（逐字，待 review）

**新 `EXTRACT_KEYS`（5 键）**：`frame_id / caption / place / objects / ocr / people`
（删 `scene` / `apps` / `events` / `actions`）。

```text
你是专业的图片内容分析引擎。你只输出 JSON，不输出任何其他文本或解释。
用户提供 1-4 张关键帧图片（图片之间以 "frame <index>" 文本标记索引）。
对每张图严格按照以下 JSON Schema 输出：
{"frames": [{"frame_id": "必须使用用户提供的图片索引字符串", "caption": "画面的一句话客观描述", "place": "根据图片推测地点，不确定就是 null；如果是屏幕截图，也是 null", "objects": [{"name": "物体名", "description": "描述"}], "ocr": [{"text": "显著可见文字，每条不超过80字", "location": "画面位置"}], "people": [{"count": 0, "description": "人物与动作"}]}]}
要求：ocr 提取画面中显著可见的文字（标题、菜单、弹窗、代码、错误信息等），最多 15 条；画面文字极少时可为空数组；每个数组字段可以为空但必须存在；只输出 JSON，不要 Markdown 代码块。
```

（相对现版差异：删 scene/apps/events/actions 四段与"events 描述静态画面中可观察
到的事件"要求句；新增 place 段（描述取自用户原文、标点规整）；首行"关键帧截图"
→"关键帧图片"（D3）。）

### 9.2 归一化口径

- `place`：非字符串 → None；空串 / 字面量 `"null"/"none"`（不区分大小写）→ None
  ——复用 `duplicate_of` 的 nullish 处理（§8 review 修复同款退化防御）。
- `caption`：非字符串 → `""`；`objects` / `ocr` / `people`：非 list → `[]`
  （现规则不变）。
- extract 解析为**宽松归一**（无 `EXTRACT_KEYS` 存在性硬校验，与 filter 的严格
  校验不同）——normalize 默认值兜底，字段缺失不触发整窗重试。

### 9.3 调研结论与下游影响（2026-09-18 全仓扫描）

**数据现状（关键背景）**：dev 实库 4 条 video 事件（抽查 payload）全部为**摄像头
实拍照片**（书桌/床头柜、640×480；objects=马克杯/键盘/显示器/茶叶罐等），非屏幕
截图——place 有真实语义域（书桌/床头/办公桌）；"屏幕截图→null"为防御性兜底。
同时解释 scene/apps 对实拍帧是"推断显示器里的内容"的间接产物。

**被删字段消费方**：

| 字段 | 消费方 | 删除影响 |
|---|---|---|
| `scene` | ① bff graph `service.py:198-205`（video 事件 → **图谱 scene 节点**；`test_graph_api.py:30` 断言 `scene:browser`）② web 时间线 VideoCard 场景 chip（`EventCard.tsx:80`）③ replay frame cue `.scene`（`replay/service.py:91`；Android/web 均未渲染，仅 DTO 声明）④ ~~`store.get_key_image` meta 透传（`store.py:324`）~~（**2026-09-27：该工具已并入 `get_event`，meta 透传路径删除**） | 新数据图谱 scene 节点消亡（旧 payload 被重跑 upsert 覆盖后同样消亡）；web chip 自动空 |
| `apps` | ① bff graph `service.py:206-213`（→ **图谱 app 节点**；`test_graph_api.py:28-29` 断言 `app:google chrome`）② payload 透传展示 | 同上，app 节点消亡（VSCode/Chrome 类实体不再入图） |
| `events` | **全仓无消费方**（仅类型声明） | 纯删除 |
| `actions` | 仅 `_video_summary_text` 拼入 summary（`vision.py:576`） | 随 summary 重写移除 |
| `objects` `ocr` `people` | 保留：objects 续供图谱 object 节点（跨音/视频共用分支）；三者均进 summary 拼装 | 不动 |

**文本链（唯一入口 `_video_summary_text`）**：`events.summary` = FTS `search_docs`
（`index.py:96-105`，只索引 summary）= 向量 chunk = day_summary LLM 输入 = search
snippet 的一致文本源 → **summary 拼装是删字段对搜索/日总结影响的全部**（payload
字段本身不进搜索文本）。

**新 summary 拼装（D1）**：`caption + place(非空) + ocr[].text`（原：
caption + scene + apps.name + apps.window_title + ocr + actions）。

**先例**：音频 2026-09 二轮曾移除 place（用户当时裁定"太多推测都是无意义的"）——
失败模式是宽松无约束推测；本次视频 place 带严格 null 规则（不确定→null、屏幕
截图→null），风险点=模型对 null 规则的遵循度 → 探针先行（D4）。

### 9.4 代码触点

| 位置 | 改动 |
|---|---|
| `vision.py:49` `EXTRACT_KEYS` | → `["caption", "place", "objects", "ocr", "people"]` |
| `vision.py:67` `EXTRACT_SYSTEM_PROMPT` | 按 §9.1 草案 |
| `vision.py:188` `normalize_extract_entry` | 按 §9.2（place nullish 归一） |
| `vision.py:567` `_video_summary_text` | 按 §9.3 新拼装 |
| 审计 JSON | 字段自然跟随（extract entry 全量落盘，无独立触点） |
| 图谱 / replay / store meta / web/Android 前端 | **不改**（D2：保留旧字段读取，新数据自然为空） |

### 9.5 实施步骤（拍板后）

| # | 任务 | 说明 |
|---|---|---|
| S0 | extract 只读探针 | 新增 `scripts/probe_video_extract.py`：对现有 4 帧（现 4 条 video 事件来源）用 §9.1 草案跑 2-3 连轮，输出逐帧 place/字段/null 率/稳定性 |
| S1 | vision.py 四触点改造 | §9.4 表；探针复核通过后落生产 |
| S2 | lvdata 测试 | `_extract_json` fixture 5 字段化；payload/summary 断言更新（`test_vision.py:239-241`）；新用例：place nullish 归一、新 summary 拼装；conftest 两处视频 fixture |
| S3 | services 测试 + 文档 | bff conftest 视频 payload（`conftest.py:140-143`）、`test_timeline_api.py:171` scene 断言、`test_graph_api.py:28-30`（改旧 payload 兼容断言）；本文档收尾 + data 两份修订裁定横幅（service doc 行 288/293/527/531、video doc §2.4/§2.5） |
| S4 | dev 验证 | force 重跑 38 帧 → 核对 place 分布/新 summary 搜索文本/日总结输入；重启 server 核对日卡与图谱（scene/app 节点消亡为预期） |

### 9.6 风险

- **place 噪音**（模型不守 null 规则，推测地点失真）→ S0 探针把关；不合格的
  回退方案=place 只落 payload、不进 summary（D1 降级执行）。
- 图谱 scene/app 节点消亡为**预期代价**（D2 已拍板）；place 入图需前端 kind
  列表联动，留待下轮。

### 9.7 收尾记录（2026-09-18）

**S0 探针（3 连轮通过）**：`scripts/probe_video_extract.py` 对存量 4 关键帧实测
——place 语义正确（办公桌/卧室/家庭办公室或书房）、零噪音、字段完备；仅"屏幕
主体帧"存在保守 null↔房间推断波动（r1 与 r2/r3 差异，非 null 值本身一致）。真正
"屏幕截图→null"分支待 dev 数据覆盖（当前数据全为实拍）。

**S1–S3 完成**：vision.py 四触点（`EXTRACT_KEYS` 5 键 / prompt §9.1 / place
nullish 归一 / summary 新拼装）——**生产 = 探针 = §9.1 三处逐字一致（程序化
核对）**；lvdata **170 绿**（+2 用例：place 归一、summary 拼装；跨包 4/4）；
services **1274 绿**（conftest 新 schema + timeline `scene`→place 断言 + graph
新增兼容用例——旧 payload 仍出 scene/app 节点、新 payload 不出）。

**S4 dev 实跑（2026-09-18 完成）**：重启 lvdata-mcp + 触发 video 全量重跑（当时经 MCP
`trigger_analysis`，该工具 **2026-09-28 已迁 REST** `POST /api/analysis/trigger`）→
**2 窗口 / 38 帧 / 0 失败**；4 条 video 事件 payload 全为新 5 字段
（零旧字段残留）——place 实测：`办公桌` / `null`（屏幕主体帧）/ `家庭办公室或
书房` / `卧室或宿舍`；summary 实证 = caption + place + ocr（place=null 不占位）；
window 事件不变（key_frame_count=2、无 static）。FTS reindex 刷新 search_docs，
MCP search「宿舍」命中该 video 事件；day_summary 自动重生成（09-15：39 原始
− 4 带转写 audio = 35，双口径一致），日总结吸收 place 语义（"办公桌环境的拍摄
记录"）。bff 离线核对：**图谱 scene/app 节点消亡实证**（kinds =
emotion/event/object/topic；object 18 保留）、09-15 日卡 images=2（V7 不受影响）。

**review 修复（2026-09-18，1 处）**：**审计 JSON `frame_id` 被模型回显覆盖**——
`_extract_chunk` 的 frames 审计 doc 与 `_process_window` 的 window doc 均把
`**entry` 展开放在权威字段**之后**：extract 模型回显 "frame N" / chunk 局部索引
（"0"/"1"…）覆盖真帧 UUID——dev 实证 **12/12 个 frames/*.json 的 frame_id 全部
失真**（"frame 1" 等）。scope：审计文件"仅人工核查"、无程序消费方，events
payload 覆写顺序本就正确（未波及）。修复：两处改为 entry 先展开、权威字段
（frame_id/ts_ms/window_id…）后覆写；测试 fixture 改为按 dev 实测的 `"frame N"`
回显形态，two_step 新增审计文件 frame_id 断言（真帧 UUID / 窗口局部索引）——
**lvdata 170 绿**（跨包 4/4）。同类模式在 audio.py 审计 doc（`**parsed` 末位）
存在但键集不重叠（SEGMENT_KEYS ∩ {segment_id, duration_s…} = ∅），无实害、留观。

---

## 10. filter schema 第二轮：`duplicated` / `key_frame`（2026-10-07，已实施）

> 状态：**已实施（2026-10-07）：S1–S5 完成，见 §10.8 收尾记录**。§0–§9 中
> "5 键 schema / 派生 is_key_frame"的描述由本节取代（§0–§9 为历史记录）。
> **提案来源（用户，2026-10-07）**：① `duplicate_of` → `duplicated: true|false`
> （场景重复 → true）；② `value` → `key_frame: true|false`——同一视频场景最多
> 一个 key frame，选取高画质的作为 key frame；③ 补充口径（用户原话）："同一
> 场景最多一个 key frame，过渡场景，黑屏场景可以没有 key frame"；④ 以 codebase
> 为基础、不猜测。

### 10.0 决策记录（2026-10-07 用户拍板）

| # | 问题 | 拍板 |
|---|------|------|
| D1 | 静止/无变化场景的关键帧数 | **同一场景最多 1 帧；过渡场景、黑屏场景可以没有**（用户原话）。正常场景（含静止有内容者）→ 1 代表帧；探针实测"静窗 0→1"类窗口已确认接受 |
| D2 | `key_frame` 与 `duplicated` 交叉 | **独立客观判定、允许双真**；不做交叉强校验（探针实证 36 窗口次 `key∧dup`=0，代表帧实测从不与"重复"同真） |
| D3 | 审计/内部键名 | **`is_key_frame` → `key_frame`**（不再有"派生"语义，与模型 schema 同名） |
| D4 | bool 归一 | 原生 bool 直取；int `0/1`；字符串 `"true"/"false"`（strip/lower）；其余/缺失 → 默认（**`key_frame`→false、`duplicated`→true**，降噪优先，同 audio `value` 越界→low 先例） |
| D5 | 过渡画面显式句 | **保留**（写入 `key_frame` 描述："画面移动中的过渡场景、黑屏场景可以没有关键帧"） |
| D6 | 模糊首帧被选为 key（探针偏差） | **B = 代码守卫**：`key_frame=true` 且 quality（归一后）=="low" → `key_frame=false`；prompt 不加句（偏差实测 ≈3%，守卫确定性兜底） |

D6=B 实现细节（已按此实现）：守卫作用于**归一后**的 quality——枚举越界 → "low"
时连坐降 key，保证存储中不存在 `key∧quality=low` 组合（与降噪口径一致）；备选
"仅显式 low 触发"会造成同值不同判，未采用。

### 10.1 新 schema 与 prompt（逐字 = 探针实测版 = 将落生产版）

**新 `FRAME_KEYS`（5 键）**：`frame_id / quality / duplicated / key_frame / reason`

```text
你是图片序列分析引擎。你只输出 JSON，不输出任何其他文本或解释。
用户提供按时间顺序排列的 N 张图片（图片之间以 "frame <index>" 文本标记索引，从 0 开始）。
请逐帧判定并严格按照以下 JSON Schema 输出：
{"window_caption": "该时间窗口内图片内容的一句话概括", "frames": [{"frame_id": "必须使用用户提供的图片索引字符串", "quality": "图片画质 low|high（模糊、黑屏、花屏等低质 → low）", "duplicated": "本帧与之前某帧内容几乎相同（同一场景的画面重复）→ true，否则 false", "key_frame": "本帧是否为其场景选出的代表帧：同一场景最多一帧 true（取该场景画质最高的帧）；画面移动中的过渡场景、黑屏场景可以没有关键帧 → true，否则 false", "reason": "一句话理由"}]}
要求：每个输入的 frame_id 都必须有且仅有一个条目，不得遗漏、不得编造索引；只输出 JSON，不要 Markdown 代码块。
```

- `parse_filter_output` 的存在性硬校验（`FRAME_KEYS` 缺键 → retry）与索引完备
  校验（缺/编造索引 → retry）不变；
- 保留"逐帧判定"字样（`test_vision.py` FakeVision 按该子串分派）。

### 10.2 归一化与守卫（新 `normalize_frame_entry` 口径）

- `quality`：`str.lower().strip()`；∉{low,high} → `"low"`（不变）；
- `duplicated` / `key_frame`：按 D4 bool 归一，无法解析 → 默认值（true / false）；
- **守卫（D6=B）**：归一完成后 `key_frame=True and quality=="low"` → `key_frame=False`；
- `duplicated` 不参与任何判定（纯元数据，D2）；
- `duplicate_of` 归一逻辑与 `is_key_frame` 派生逻辑整体删除；`_norm_frame_id`
  保留（`frame_id` 回显归一仍在用，extract 侧不变）。

### 10.3 探针实录（S0，2026-10-07）

- **数据**：dev 存量 **3667 帧 / 230 窗口**（19 sessions，09-15 → 10-07）；确定性
  均匀取样 **12 窗口 × 3 连轮**（跨全时段，含单帧/静止/动态类）；脚本
  `tools/lvdata-mcp/scripts/probe_video_filter.py`（已按 §10.1 重写）；原始输出
  `/tmp/probe_v2_run{1,2,3}.txt`（只读：未写 DB/derived/events）。
- **稳定性（flag 级逐帧比对）**：`dup`/`key` 判定——run2 ≡ run3 全 12 窗逐帧
  一致；run1 与 run2/3 的 key 偏差仅窗口 1 的 00/01 一对反转（12 窗 × 3 轮中仅
  此 1 处，≈3%）：run1 输出 `{00,13}`——00 为失焦模糊首帧（quality=low），模型
  以"内容独特/首帧代表性"为由标 key，且**漏标清晰帧 01**（理由误写"非最高画质
  帧"），构成同场景双 key；run2/3 为正确的 `{01,13}`。D6=B 守卫下 run1 输出将
  降为 `{13}`（模糊帧挡掉；漏标 01 属模型偶发，守卫不承诺补偿）。`quality`
  枚举另有无 key 后果的轻微抖动（街道窗 2 帧 low↔high，各轮不定）。另观测：
  短暂切换场景（桌下视角帧）的归类（"过渡" vs "新场景"）跨轮次波动（冒烟轮曾
  标 key、3 连轮均判非 key）——属 D1 允许的模型判定空间，非违约。
- **遵循度**：类型越界 0、缺失条目 0、编造索引 0；`key∧dup` 双真 0；
  `key∧quality=low` 仅 run1 那 1 处；过渡帧（运动模糊）全部两个 flag false。
- **同窗口 before/after（DB 现库存 vs 新规则，同一 12 窗）**：

  | 窗口类 | 现规则（DB） | 新规则 | 解读 |
  |---|---|---|---|
  | 10-05 夜间街道 24 帧 | **23** 条 video 事件（近全标） | **2** | 去冗余正中痛点（动态但同一场景画面不再逐帧入库） |
  | 10-06 静止吸顶灯 ×5 | **0**（静窗零事件） | **1/窗** | D1：静止有内容=正常场景 → 1 代表帧（0→1 已确认接受） |
  | 其余 6 窗（主屏滑动/单帧/桌面切换） | 10 | 9 | w12 3→2，其余持平 |

  （DB 计数 = payload 含该 window_id 的 video 事件行数；生产按 pending 批划分
  窗口、边界可能与探针全量分组不同——此表为同 window_id 的量级对照。）

- **全量推算**：sample 7.7% key/帧 → 3667 帧 ≈ 280 video + ~230 window 事件（现
  库存 video 325 / window 164：量级相当、结构重排）；单窗 ~17s / ~21k input
  tokens（全量单轮 ≈ 65 分钟）。
- **未覆盖**：黑屏/全过渡窗口（样本 0 命中）——"零关键帧 → 不产事件"路径保留
  为防御（模型仍可能全 false），未被实测触发。

### 10.4 代码触点（S1）

| 位置 | 改动 |
|---|---|
| `vision.py:48` `FRAME_KEYS` | → `[frame_id, quality, duplicated, key_frame, reason]` |
| `vision.py:51-64` prompt | → §10.1 逐字版 |
| `vision.py:166-188` `normalize_frame_entry` | 重写：bool 归一（D4）+ D6 守卫；删 `duplicate_of`/`value`/派生 |
| `vision.py:370` | `key_ids` 改读 `key_frame` |
| `vision.py:389-414` | 窗口事件条件 / 无关键帧分支不变（防御保留） |
| `vision.py:1-15` 模块 docstring | 同步字段名与口径 |
| `scripts/probe_video_filter.py` | **已完成**（本轮探针 = §10.1 逐字） |

不改：窗口分组/预算/重试机制、extract 阶段、event payload 结构、events 表结构。

### 10.5 测试（S2 lvdata / S3 services）

- `_filter_json` fixture（`test_vision.py:32`）换新 5 键（保留 `"frame N"` 回显）；
  `two_step`（:220）审计/事件断言改 `key_frame`/`duplicated`；`fabricated`（:289）
  与 `prefix`（:269）用例 builder 更新；
- `test_normalize_derives_key_frame_and_coerces`（:305）重写：bool 归一（原生/
  int 0-1/字符串/非法→默认）+ **D6 守卫**（key∧quality=low→false、枚举越界连坐）；
- 删除 `test_normalize_nullish_duplicate_of`（:324，`duplicate_of` 不复存在）；
- `test_static_window_produces_no_events`（:368）保留为防御语义（模型输出全
  false → 零事件）；
- `conftest.py:159` 审计 fixture `is_key_frame` → `key_frame`（无消费方，随改）；
- services：**预期零改动**（全仓 rg 无 filter 字段引用）→ 跑绿确认。

### 10.6 文档与部署（S4/S5）

- `docs/data/lvdata_mcp_service_v2.md` §6.4 正文（:591-592 仍为 09-18 前旧键表
  ——顺手修正为本轮口径）；
- `docs/data/lvdata_video_analysis.md` 修订横幅追加；
- `docs/android_timeline_image_gallery.md` §1.2（:64-68 派生口径引用改述；:67 括注
  "payload 含 quality/value/reason"与代码不符——顺带核对修正）；
- S5 dev：force 重跑 video（全量 3667 帧 ≈ 65 分钟）→ 核对事件/审计/日卡/画廊
  → 残留清理（不再是关键帧的旧 video 事件行，按 09-18 V5 口径备份+删除）+
  `reindex --vectors`；生产旧数据自然更替（帧只分析一次）。
- bff / Android 零改动（事件 payload 结构不变；`duplicated`/`key_frame` 不落
  payload，落窗口审计 JSON）。

### 10.7 风险

- **D1 下静止时段恢复产帧**（0→1）：录制中纯静止时段每窗口 1 video + 1 window
  事件；S5 观察量级（可与现行"静窗零事件"对照）。
- **D6 守卫为弱代理**：仅拦截 quality=low 组合；"首帧自合理化"类偏差余量由探针
  连轮观察（本轮 ≈3%）；守卫不弥补模型漏标（run1 类）。
- **单帧窗口**：样本观测 2/2 产 1 帧（存量 38 个单帧窗口）——按 D1"正常场景 1
  代表帧"。
- 场景归类轮间波动（短暂切换场景算"过渡"还是"新场景"）：D1 允许空间，不设
  代码约束。

### 10.8 收尾记录（2026-10-07，已实施）

**S1–S4**：
- `vision.py` 五触点（§10.1 逐字 prompt、`FRAME_KEYS`、`normalize_frame_entry`
  含 `_norm_bool` D4 + D6 守卫、`key_ids`、模块 docstring）；新增
  `scripts/rerun_vision_stage.py`（沙箱 force 重跑驱动，内置 /opt/lvdata 拒绝
  护栏）。**程序化核对：生产 = 探针 = 文档 §10.1 逐字一致**（三处逐一比对 pass）。
- 测试：lvdata **288 绿**（fixture 换新 5 键 + bool/守卫用例 + 静态窗改防御
  语义）；services **1496 绿 / 1 skip**（零改动）。注：services 首轮 10 个 e2e
  失败为**环境定位问题**——未激活 venv 时 e2e 子进程解析到
  `~/ven/python312/bin/uvicorn`（旧环境缺 `services/avdetector`）→ gateway 回兜
  "unsupported message type"；按规范入口（README `server-test`：`cd services &&
  pytest`，venv 激活）全绿；另刷新 root `.venv` 的 `pip install -e services
  --no-deps`（editable 映射过期）。
- 文档：data 两份修订横幅 + `lvdata_mcp_service_v2.md` §6.4 正文旧键表修正 +
  gallery doc §1.2 口径改述。

**S5 沙箱全量重跑（真实数据副本，真实库零写入）**：
- 副本 `/tmp/lvdata_sandbox_20261007`（`VACUUM INTO` 快照）+ concurrency=2 →
  **230 窗口 / 3667 帧：ok=222 / failed=8**；wall 2603s（~43 分钟）。
- failed 8 窗口（87 帧）= **5 内容审核 400**（data_inspection_failed，不可重试）
  + **3 extract 模型偶发**（missing frame indices，生产由 fail_count 退避重试）；
  87 帧中 69 帧为存量历史失败（本轮全部确定性复现）、18 帧新增。
- **守卫实弹 0 违规**：新审计 222 窗口 / 284 关键帧，`key∧quality=low` = 0、
  bool 类型异常 = 0（D6=B 生效；审计关键帧总数 284 与新写 video 事件 284 完全
  交叉核对一致）。
- **零关键帧窗口实弹命中 1**（24 帧静止天花板窗，模型全 false → 按 §10 不产
  事件；防御路径首次真库触发，符合语义）。
- **全量模型行为审查（3580 帧审计）**：每窗关键帧分布 = 0×1 / 1×180 / 2×25 /
  3×12 / 4×2 / 5×2——**无全标退化**（单窗上限 5/24，"同场景最多一帧"在整库
  层面成立）；`duplicated=True` 84.4%；`dup∧key` 双真 0；旧键
  （value/duplicate_of）残留 0（模型无字段回显污染）。
- **前后对比（快照 vs 重跑，同一数据）**：

  | 指标 | pre（现规则） | post（新规则） |
  |---|---|---|
  | video 事件 | 325 | 284（保留 155 / 新增 129 / 移除 139） |
  | 未重判（失败窗口帧的旧键，排除在移除外） | — | 31 |
  | window 事件 | 164 | 224 新写（另 58 条旧批分组残留 id） |
  | 旧同 id 无事件→新有 | — | 118 窗口（含静窗恢复） |
  | 共同 window_id（103 个）diff | — | 持平 85 / 增 8 / 减 10 |
  | 去冗余典型（共同窗） | 24→1、23→2（同场景近全标 → 代表帧） | 同左 |
  | day_stats 10-06（静止日） | video 事件 62 | 169（池 15 封顶不变） |

- **残留口径**：旧 window 分组 id 残留行（58）+ 移除键的旧 video 行——按 09-18
  V5 口径备份+清理（真实库切换实录见下条）。
- **真实库切换（2026-10-07 15:02–16:41，~100 分钟）**：旧服务停机（优雅退出）→
  新代码服务启动 → 备份 `VACUUM INTO /tmp/lvdata_backup_pre_rerun.db` → REST
  `POST /api/analysis/trigger {"stages":["video"],"force":true}` 全量重跑
  （期间 31 新帧同步进入并一并分析；3667→3698 帧）。
  结果：ok 3653 / failed **45**（全部为 5 个内容审核 400 窗口、fail_count=3
  停车；extract 偶发失败全部经退避重试自愈）；**守卫 0 违规**（本轮审计 298
  关键帧 ↔ 新写 video 事件 298 完全对齐）；零关键帧窗口 1。
  对比（快照 vs 现状）：video 325→**298**（保留 167 / 新增 135）；window
  164→**227**（+121 新窗事件）；共同 window_id 106：持平 88 / 增 8 / 减 10；
  day_stats 10-06 video 62→**132**、10-07 4→**8**（池 15 封顶；day_stats 曾在
  清理前算过一轮残留中转值，切换末已 force 重算）。
  清理：stale video 162（158 旧键 + 4 同窗重分析遗留）+ stale window 58 →
  备份 `/tmp/real_cleanup_backup.json` 后删除；force reindex（FTS docs
  2712→2492）+ force embed（向量孤儿 220→0）。
  备注：REST trigger 非 force 时 stages 仅入队轮（轮次固定序列执行，stages 只
  在 force=true 时生效）；FTS 增量重建不清理已删行、向量孤儿仅全量重建清扫、
  day_stats 对删除不可见——删改事件后的对账需 force 触发 reindex+embed+day_stats。
- 产物：`/tmp/lvdata_sandbox_20261007/`（副本 DB + 新规则审计 derived）、
  `/tmp/sandbox_pre_*.txt`、`/tmp/sandbox_compare.py`、探针
  `/tmp/probe_v2_run{1,2,3}.txt`。

---

## 11. filter schema 第三轮：`duplicated` → `scene_id`（2026-10-07，已实施）

> 状态：**已实施（2026-10-07）：V1–V4 拍板 + S1–S5 完成，见 §11.7 收尾记录**。
> 取代 §10 的 `duplicated` 字段；§10 其余口径（`key_frame` 判定、D6 守卫、
> 静窗防御）不变。
> **提案来源（用户，2026-10-07）**："`duplicated` 修改为 `scene_id: integer`——
> 窗口内同样的场景标注相同的 id，不同的场景标注不同的 id；其他内容不变"。
> 目的：把"是否重复"的布尔升级为显式的**场景结构标识**（跨窗口 scene 支持的
> 前置，见 §11.6）。

### 11.1 新 schema 与 prompt（逐字 = 探针实测版 = 生产版，2026-10-07 起）

**新 `FRAME_KEYS`（5 键）**：`frame_id / quality / scene_id / key_frame / reason`

```text
你是图片序列分析引擎。你只输出 JSON，不输出任何其他文本或解释。
用户提供按时间顺序排列的 N 张图片（图片之间以 "frame <index>" 文本标记索引，从 0 开始）。
请逐帧判定并严格按照以下 JSON Schema 输出：
{"window_caption": "该时间窗口内图片内容的一句话概括", "frames": [{"frame_id": "必须使用用户提供的图片索引字符串", "quality": "图片画质 low|high（模糊、黑屏、花屏等低质 → low）", "scene_id": "本帧所属场景的整数编号：窗口内同样的场景标注相同的 id，不同的场景标注不同的 id", "key_frame": "本帧是否为其场景选出的代表帧：同一场景最多一帧 true（取该场景画质最高的帧）；画面移动中的过渡场景、黑屏场景可以没有关键帧 → true，否则 false", "reason": "一句话理由"}]}
要求：每个输入的 frame_id 都必须有且仅有一个条目，不得遗漏、不得编造索引；只输出 JSON，不要 Markdown 代码块。
```

### 11.2 归一化口径（V1/V2 已拍板）

- `scene_id`：int / 整数字符串（`"1"`）/ 整值 float → int；bool → 非法；
  负值 → 非法；**非法值（存在但非整数）→ 归 `None`，语义 = "本帧独立场景"**
  （不与其他帧合并；避免多个非法值同号被误判同场景）；
- `FRAME_KEYS` 存在性硬校验不变（缺 `scene_id` 键 → retry）；`quality` /
  `key_frame` / D6 守卫不变；`duplicated` 归一逻辑删除（`_norm_bool` 保留供
  `key_frame`）；
- **场景级 key 抑制（2026-10-07 用户拍板，新增）**：同一窗口内同一有效
  `scene_id` **至多保留 1 个 key frame——取帧索引最小者（窗口内位置序，
  不依赖模型输出顺序；暂定——若未来改按画质最优，另行拍板）**，其余强制
  `key_frame=false`；**出现即写 warning log**
  （window_id / scene_id / 保留帧 / 被抑制帧清单）；`scene_id=None`（非法值）
  不参与分组（各自独立场景，无抑制）。执行位置：逐帧归一（含 D6 守卫）之后、
  `key_ids` 计算之前（窗口级后处理）；审计 JSON 记录**抑制后**的值，warning
  log 留模型原始误标的差异证据。

### 11.3 探针实录（2026-10-07，10-05 单日）

- **数据**：10-05 全天 199 帧 / 14 窗口（单 session `01a10a41-…`，本地
  15:01–22:40）；脚本 `scripts/probe_video_filter.py`（scene_id 版，
  `--dates` 过滤）；原始输出 `/tmp/probe_scene_1005.txt`。
- **结果：23 个场景**（窗口内计数之和；跨窗不合并）/ **21 个 key**：
  - 每窗 scene 数：1×9 窗 / 2×2 窗 / 3×2 窗 / 4×1 窗；
  - 场景规模（帧数）：**≤5 帧小场景 13 个**（穿插的短暂画面/过渡）；**≥7 帧
    大场景 10 个**（整窗静止/稳定画面）。
- **模型行为**：编号 1 起（1,2,3…）；**0 解析失败 / 0 缺失条目 / 0 编造索引**；
  `key∧quality=low` 1 例（D6 守卫可拦）；过渡/短暂场景可 0 key（窗口 3 的
  scene 2/3/4、窗口 7 的 scene 2 均无 key）✓。
- **语义观察（拍板相关）——"同场景多 key"1 例**（窗口 3，24 帧车拍）：模型把
  被打断后重现的路况画面归回 **scene 1**（共 17 帧：{0} + {5..20}），但对同一
  id **按"出现段"各选了代表** → scene 1 有 3 个 key（0/5/11，理由分别写"该
  场景的代表帧/第二组路况的关键帧/该段强光照路况的代表帧"）。即：**scene_id =
  窗口内"内容场景身份"（重现归同 id），key_frame 被模型按出现段各选了代表**；
  该行为由 §11.2 场景级抑制规则收敛（同 id 仅保留帧索引最小的 1 个 key）——
  本例 3 key → **1 key**，10-05 全天 21 → **19 key**。
- **与现行生产对照（同 14 窗）**：现 duplicated 版产出 22 个 video 事件 vs
  scene 版 21 个 key——筛选行为基本一致；scene 版的增量价值是可直接回答
  "一天有几个场景"。

### 11.4 决策记录（V1–V4，全部已拍板 2026-10-07）

| # | 问题 | 拍板 |
|---|------|------|
| V1 | 非法 `scene_id` 口径 | **已拍板（用户 2026-10-07，按推荐）：None = 本帧独立场景（不合并）**；整窗 retry 备选未采用 |
| V2 | 编号起点/连续性 | **已拍板（用户 2026-10-07，按推荐）：不约束**（解析任意非负整数；实测 1 起），仅要求窗口内一致 |
| V3 | "同场景多 key"（重现场景各选代表） | **同窗部分已拍板（用户 2026-10-07）：同窗同 `scene_id` 只留帧序第一个 key、其余降级并写 warning log**（§11.2）；**跨窗**延续抑制仍留 §12 一并定 |
| V4 | 10-06 补跑验证 | **不补跑（用户 2026-10-07 拍板）**——以 10-05 单日验证为准；10-06（已瘦身 677 帧 / 30 窗）留作后续回归数据 |

（V1–V4 齐备 → §11 进入实施：`vision.py` 改造 + 测试（§11.5）→ 沙箱/真实切换
沿用 §10 既有流程——全量 force 重跑 + 残留清理 + force reindex/embed/day_stats。）

### 11.5 代码触点（实施时）

| 位置 | 改动 |
|---|---|
| `vision.py:49` `FRAME_KEYS` | `duplicated` → `scene_id` |
| `vision.py:52-65` prompt | → §11.1 逐字版 |
| `vision.py:167` `_norm_bool` | 保留（供 key_frame）；新增 `_norm_scene_id` |
| `vision.py:180` `normalize_frame_entry` | `duplicated` 分支 → `scene_id` int 归一 |
| `vision.py:351` `_process_window` | 归一后、`key_ids` 前新增**场景级 key 抑制**（同窗同 id 只留首帧）；触发写 `log.warning`（window_id/scene_id/保留帧/被抑制帧） |
| 审计 JSON | frames 条目自然携带 `scene_id`（无独立触点；§12 带入读取依赖此字段） |
| `scripts/probe_video_filter.py` | **已完成**（scene_id 版 = 本轮探针） |
| data 文档 / `android_timeline_image_gallery.md` §1.2 | 实施后同步口径 |

（测试：新增用例——同窗同 `scene_id` 多 key → 留帧索引最小者、其余降级、
warning log 断言；`scene_id=None` 帧不参与抑制；抑制在 D6 守卫之后执行。）

### 11.6 与 §12（跨窗口 scene 支持）的关系

跨窗带入（前窗末场景帧带入下窗、同场景抑制 key）为独立计划，已入档 **§12**
（未排期）：**依赖 §11 的审计 `scene_id` 字段**（已就绪）；实施顺序 §11 →
§12；同窗抑制口径已定并落地（§11.2/§11.4 V3），**跨窗**延续抑制仍与 §12
一并定。

### 11.7 收尾记录（2026-10-07，已实施）

**S1–S4**：
- `vision.py` 按 §11.5 完成（FRAME_KEYS/prompt 逐字/`_norm_scene_id`/
  `normalize_frame_entry`/`suppress_scene_duplicate_keys` + `_process_window`
  挂点 + warning log）；**程序化核对：生产 = 探针 = 文档 §11.1 逐字一致**（pass）。
- 测试：lvdata 全绿（fixture 换 scene_id；新增 scene_id 归一用例、抑制单测、
  实弹 e2e——4 帧同场景全 key → 仅帧 0 产事件/审计保留、其余降级、log 捕获
  断言）；services 1496 绿 / 1 skip（零改动）。
- 文档：service v2 §6.4 + 第三轮横幅、data 文档横幅、gallery doc §1.2 同步。

**S5 真实库切换（2026-10-07 19:41–20:35，~53 分钟）**：
- 备份 `VACUUM INTO /tmp/lvdata_backup_scene_round.db` + 前态 dump（200 video /
  139 window 事件、1700 帧）→ 服务重启（新代码）→ REST
  `{"stages":["video"],"force":true}` 全量重跑。
- 结果：**ok 1655 / failed 45 / pending 0**（45 = 5 个内容审核窗口，确定性；
  运行中一度多出的 3 帧已靠退避重试自愈）。
- **抑制实弹 2 次**（warning log 明细）：`01a0bd4d-8d21` scene 4（留帧 7 /
  抑帧 13）、`01a10f03-2e2e` scene 1（留帧 0 / 抑帧 5）——正是设计场景
  （模型同场景多 key → 只留帧索引最小者）。
- 对比（快照 vs 现状）：video 200→**191**（保留 182 / 新增 9 / 移除 18——除
  抑制 2 例外其余为模型轮间方差）；window 139→139（无残留）；共同 139 窗：
  持平 123 / 增 5 / 减 11。
- 审计扫描（本轮）：140 成功窗口；`key∧quality=low` **0**；类型异常 **0**；
  **抑制后同场景多 key 残留 0**（规则生效全量校验）；零关键帧窗口 1；
  `scene_id` 非法→None 帧 **3**（V1 路径实弹，占比 ~0.2%）；审计 key 191 ↔
  新写 video 事件 **191 完全对齐**。
- 清理：stale video 18 条（备份 `/tmp/scene_cleanup_backup.json`）；force
  reindex（search_docs 2309 ≈ events 2311）+ force embed（孤儿 0）+
  force day_stats（10-05: video 22/14、10-06: 33/30、10-07: 8/5）。
- 备注：§11.3 探针轮"10-05 全天 21→19"为该探针轮的场景级估计；真实重跑轮
  10-05 模型未再输出同场景多 key（轮间方差），实际 22 条、无抑制——两口径
  均如实记录。

**review 修复（2026-10-07，1 处）**：字符串数字解析仅用 `str.isdigit()` 的
隐患——`'²'` 等非 ASCII 数字字符 `isdigit()=True` 但 `int()` 抛 ValueError
（实证）→ 归一抛错走整窗重试耗尽失败（"静默数据丢失"同类缺陷）。修复：
`_norm_scene_id` / `_norm_frame_id` / 探针 `_scene_id` 加 `s.isascii()` 守卫
（scene_id 非 ASCII → None=V1 非法口径；frame_id 原样保留交索引校验）；
回归用例 +2（非 ASCII 入非法集、`_norm_frame_id` 不崩）；lvdata 全绿、服务
重启加载修复版。

---

## 12. 跨窗口 scene 支持（2026-10-07 入档，**未排期**：先不实现，未来再 plan）

> 状态：**计划入档、未排期 / 不实施**（用户 2026-10-07："放入文档，先不实现，
> 未来再 plan"）。前置依赖：§11（`scene_id`）先落生产；未来实施前重新 review
> （含 §12.4 D1–D8 拍板）。
> **需求来源（用户，2026-10-07）**：① 前窗最后一个场景若已有 key frame，且该
> 场景的 video frame 与下窗开始时间差 < 5s，则把该场景的 video frame 带入下窗；
> ② 下窗中与"带入的第一帧"场景相同的帧视为同场景、**忽略 key_frame**。

### 12.1 目标语义（提炼）

场景跨窗延续时，代表帧只留在前窗；下窗用"带入帧"作上下文锚识别延续并抑制
重复 key：

1. **条件 A**：前窗 W1 末场景（含末帧的场景）已有 key frame；
2. **条件 B**：W1 末帧 ts 与 W2 首帧 ts 差 **< 5s**（连续录制逐帧 ~1s，几乎
   所有相邻窗满足；录制中断 >5s 自然不触发）；
3. **带入**：该场景的帧加入 W2 的 filter 输入；
4. **抑制**：W2 中与"带入的第一帧"同场景的帧 → 不产代表帧/事件。

目的：消除跨窗边界同场景的双 key（同一场景在两窗各产 1 个代表帧；§11 同窗
抑制规则的跨窗延伸）。

### 12.2 现状锚点（代码）

| 项 | 位置 | 现状 |
|---|---|---|
| 窗口分组 | `vision.py:126` `group_windows_by_device` | 纯时间/帧数分组，**无跨窗知识**；仅对 pending 批分组 |
| 窗口编排 | `vision.py:290` `process()` | 取 pending → 分组 → **全部窗口并发 gather**（无同设备序） |
| 单窗流程 | `vision.py:351` `_process_window` | filter → 审计 JSON → window 事件 → extract → video 事件；整窗原子重跑 |
| 前窗状态可得性 | 审计 `windows/<window_id>.json` | §11 落地后 frames[] 携带 `scene_id`/`key_frame`——带入判据来源 |
| 幂等 | files 状态机 + 事件 upsert | 整窗重跑 window_id 不漂移（重试确定性依赖于此） |

### 12.3 机制设计

- **3.1 带入计算（处理 W2 时）**：读 W1 审计 → 末帧所属 scene_id → 该场景的
  尾段（窗口末尾连续同 id 帧）；校验条件 A（该场景存在 `key_frame=true`）与
  条件 B（末帧 ts → W2 首帧 ts < 5s）；
- **3.2 带入集**：尾段帧取上限 K（见 D1）；锚帧 = 带入序列第一帧；
- **3.3 filter 调用**：合并序列 `[带入帧…] + [W2 自有帧…]`，marker 连续编号；
  **带入位置的模型输出一律丢弃**（不产 key/事件/extract，文件零副作用）；
- **3.4 key 抑制（代码强校验）**：W2 自有帧中 `scene_id == 带入锚.scene_id` →
  强制 `key_frame=false`（与 D6 守卫/§11.2 同窗抑制同层，在其之后执行）；
  **prompt 不需要改**；
- **3.5 产物归属**：W2 审计只记自有帧；`window_id`/`start_ms` 不漂移；window
  事件/key_frame_count 只计自有产出；
- **3.6 执行结构**：**同 device 窗口按时间串行链**、跨 device 并行（替换扁平
  gather）；重试从前窗**已落盘审计**确定性重算；前窗失败/未处理 → 无带入降级；
- **3.7 分组基座**：带入需前窗帧行 → 分组改对**全量帧**（非仅 pending），
  window_id 确定性由"窗口原子重试"升级为"全量分组天然确定"。

### 12.4 决策点（未来实施前拍板）

| # | 问题 | 选项与推荐 |
|---|------|-----------|
| D1 | 带入集范围 | 全尾段帧（可能 24 帧）/ **推荐：尾段截取 K=1–2 帧（key 帧 + 可选末帧）**，K 可配置 |
| D2 | "忽略 key_frame"效果 | **推荐：延续帧不产 key、代表帧永远留在前窗**；备选：前窗 key 撤销、合并链重选（需跨窗删已产事件，复杂不推荐） |
| D3 | 链式传播（跨 3+ 窗） | **推荐：被延续场景视为"已有 key"，继续向下一窗带入**；否则长场景在第三窗重新产 key |
| D4 | 5s 与 K 落地形式 | **推荐：配置项** `watcher.cross_scene_gap_s: 5`（+ carry 上限）；常量次选 |
| D5 | 末场景无 key | **按条件 A：不带入**（无代表帧可保护，下窗正常产） |
| D6 | 前窗失败/未处理 | **推荐：无带入降级（接受偶发边界重复）**；备选：等待前窗成功（阻塞整链，不推荐） |
| D7 | max_frames 上限 | **推荐：组合调用允许 24+K 张**（改动最小，≈+4–8% 图像量）；备选：带入挤占自有帧（级联改分组，不推荐） |
| D8 | 审计是否记带入帧 | **推荐：不记**（审计=本窗自有帧）；备选：记但标 context |

### 12.5 预期效果与风险

- **10-05 估算**：14 窗中约 6 处相邻窗满足 <5s（15:29 三连窗、21:25/21:34
  两处、22:40 等）→ 预计在 §11 同窗抑制后的 **19 key** 基数上进一步减少约
  4–8 个（~11–15；粗估，以 S0 探针为准）；
- **极端效应**：连续录制整段静止（10-06 天花板段）将塌缩成 **1 个 key**（首窗
  产、后续延续抑制）——事件/画廊量大幅下降：是核心收益，也是实施前最需确认的
  语义；
- 其他风险：模型把"延续"判为新场景 → 链断、边界重复偶发（可接受，无代码
  兜底）；调用图数 +K（图像成本）；前窗未来被 force 重跑改变结论时下窗带入已
  固化（窗口结果不复评的既有语义）。

### 12.6 实施步骤（未来排期后）

S0 探针离线模拟（10-05 按序模拟带入：量化链条数/长度、key 减少、抑制帧数、
边界重复消除）→ S1 拍板 D1–D8 → S2 `vision.py` 改造 + 单测（条件 A/B 不满足、
链式传播、抑制强校验、带入零副作用、重试确定性、跨 session 带入）→ S3 沙箱
全量对照 → S4 文档 + 真实切换（沿用既有流程）。

