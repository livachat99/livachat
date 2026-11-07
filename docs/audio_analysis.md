# 音频分析 schema 调整设计：prompt 精简 + value 评估 + 静音规则重定义

> 状态：**二轮、三轮已实施**（2026-09-16；lvdata 全量 160 绿——见 §8/§10）。
> 范围：lvdata-mcp audio 分析（prompt + 配套解析/判定代码 + 测试 + 文档）+
> day_summary 输入去重。不涉服务端 recording 链路、不改 events 表结构。

> **2026-09-16 二轮调整（基于真机听验反馈，待 review）**：① 删除 `place`
> （推测性内容占比高、无检索价值；一轮 Q3 的 summary 拼装置首随之回退）；
> ② 新增 `value`（low|high，总体价值评估）；③ 静音规则再定义：仅当
> 「transcript 词数 > 5（CJK 感知计数）或 value = high」才写 events 行，其余按
> silent 处理；`objects`/`people`/`emotions` 降级为 payload 信息、不再参与判定。
> 附：评估意见（§9）、16 条真实数据推演（§4.1）。

> **2026-09-16 一轮评审意见已并入（历史）**：normalize 实现口径写死、防 None→[]
> 陷阱（§3）；动机事实修正（§0）；prompt 补 `count` 未知语义（§2.2）；静音规则
> delta 精确口径（§4）；客户端影响机制核实（bff `extra="allow"` 直通，§5）；
> 行号引用与消费方扫描均已逐项核对。

## 0. 二轮提案要点

1. **删除 `place`**（一轮新增项回退）：推断性内容占比高，实测出现"线上通话
   环境"、"Studio / Broadcast environment"、中英混杂与虚构线索（如某段声称
   "开头有英文广播声"但 transcript 无英文）——无检索/展示价值。
2. **新增 `value`**：`"low" | "high"`——该段声音的**总体价值评估**（判定标准
   写进 prompt，§2.2；二值取舍见 §6 V1）。
3. **静音规则再定义**（§4）：仅当「**transcript 词数 > 5**（CJK 感知计数）」
   或「**value = high**」写 events 行；其余一律按 silent 处理、不落行。
4. **`objects` / `people` / `emotions` 保留但降级**：仍进 payload（客户端展示
   用），但**不再参与静音判定**——一轮由 objects 兜底产生的环境段（提示音/
   咳嗽/底噪，共 6 条）在二轮默认不再落行。
5. 保留：`language` / `transcript` / `summary`；summary 拼装回退为
   `[transcript, summary, 情感名]`（移除 location 置首）。

改动动机：真机听验——一轮的两个兜底（place 推测、objects 兜底）产生了大量
低价值内容；静音判定应回到「内容量（词数）+ 总体价值（value）」两个可解释维度。

## 1. 现状（一轮实施后）

| 项 | 位置（二轮实施后） |
|---|---|
| System prompt | `tools/lvdata-mcp/src/lvdata_mcp/analysis/audio.py:55`（`AUDIO_SYSTEM_PROMPT`） |
| schema 键清单 | `audio.py:50`（`SEGMENT_KEYS`，7 键含 `value`） |
| 归一化 | `audio.py:139`（`normalize_segment`；计数强转 `_coerce_count` :110） |
| schema 校验 | `audio.py:169`（`missing_schema_keys`，仅顶层键） |
| 词数口径 | `audio.py:179`（`_word_count`，CJK 感知） |
| 静音判定 | `audio.py:186`（`is_silent`：词数>5 或 value=high） |
| 事件写入 | `audio.py:361+`（summary 拼装 + `upsert_event`），`common.py:106` |
| 检索消费 | `vec.py:115`（chunk 文本 = `events.summary`）；`summary/day.py:326`（只读 `type/ts_ms/summary/event_id/updated_at`） |

## 2. 二轮 schema 设计

### 2.1 顶层字段变更表

| 字段 | 二轮变化 | 说明 |
|---|---|---|
| `language` / `transcript` / `summary` | 不变 | |
| `value` | **新增** | `"low" \| "high"`；总体价值评估；无信息（寒暄填充词/底噪/无意义短句）→ low |
| `place` | **删除** | 一轮新增项回退（§0.1） |
| `emotions` / `objects` / `people` | 不变（**降级**） | 保留于 payload/派生 JSON；**不再参与静音判定** |

### 2.2 System prompt（实施版草案，逐字，待 review）

```text
你是专业的音频内容分析引擎。你只输出 JSON，不输出任何其他文本或解释。
分析用户提供的音频，严格按照以下 JSON Schema 输出：
{"language": "主要语言代码，如 zh / en", "transcript": "完整转写文本，保留口语原样、不修正口误；无语音则空串", "summary": "一句话总结该片段内容", "value": "总体价值评估（low|high）：high=有实质信息或重要内容（有信息量的对话/陈述、有意义的分析、播报、演讲、重要事件声音）；low=无信息量（寒暄与填充词、纯底噪/环境杂音、无意义的短句、仅提示音）", "emotions": [{"emotion": "happy|sad|angry|surprised|frustrated|neutral|other", "confidence": 0.0-1.0, "evidence": "依据，如笑声、语速"}], "objects": [{"name": "物体名", "description": "描述"}], "people": {"count": "人数（整数；无人或无法判断填 0）", "male_count": "男性人数，无法辨识填 null", "female_count": "女性人数，无法辨识填 null", "description": "人物描述"}}
要求：transcript 必须完整转写全部语音内容；数组字段可以为空数组、对象字段必须存在；不确定的信息填 null，不要编造；value 必须给出；只输出 JSON，不要使用 Markdown 代码块包裹。
```

### 2.3 字段细则

- `value` 判定标准（写进 prompt）：**内容导向，不看声音大小**——「还不错。」/
  「嗯。」类填充词与纯环境音 → low；成句的交流/陈述/播报/讨论 → high；
  短句但重要（如求救、关键指令）→ high（豁免词数阈值，见 §4）。
- **词数口径（评审细化，待确认 §6 V2）**：CJK 字符逐个计数 + 拉丁词按词计数
  （`[A-Za-z0-9']+`），标点不计。**不能直接用 `str.split()`**——中文整句会被
  算成 1 个"词"，将把全部中文段判掉。
- `transcript` 无语音为空串（prompt 明确）；空串词数 0。
- 非法 `value`（非 low/high）：归一为 `"low"`（降噪优先）；**键缺失**仍走
  schema 重试 → 失败路径（与其它键一致）。

## 3. 代码触点

| 位置 | 改动 |
|---|---|
| `audio.py:49` `SEGMENT_KEYS` | → `["language","transcript","summary","value","emotions","objects","people"]` |
| `audio.py:54` `AUDIO_SYSTEM_PROMPT` | 换为 §2.2 草案 |
| `audio.py:137` `normalize_segment` | 删 `place` 块；新增 `value` 归一（`str.lower().strip()`；∉{low,high} → `"low"`） |
| 新增 `_word_count(text)` | CJK 感知计数（§2.3 口径；`re` 常量 + 简单相加） |
| `audio.py:181` `is_silent` | 重写（§4）；docstring 同步 |
| `audio.py:357-370` summary 拼装 | 回退 `[transcript, summary, emotion_text]`（移除 location 置首） |
| `audio.py:5-6` 模块 docstring | 同步新规则 |
| `common.py:106` `upsert_event` | 无需改 |

## 4. 静音规则（二轮重定义）

```python
def _word_count(text: str) -> int:
    return len(_CJK_RE.findall(text)) + len(_LATIN_WORD_RE.findall(text))

silent = not (_word_count(parsed["transcript"]) > 5 or parsed["value"] == "high")
```

- `objects` / `people` / `emotions` **不参与**（降级为 payload 信息）；`place`
  已删除。
- `transcript` 为空 → 仅 `value = high` 可豁免（非语音高价值内容，如重要播报
  被转写失败时仍保留——由模型 value 判定兜底）。
- OR 语义：长转写但 value=low 的段**保留**（内容量优先放行）。

### 4.1 真实数据推演（16 条现存 audio events，CJK 感知词数）

| event_id | 词数 | 转写维度判定 | transcript |
|---|---|---|---|
| 14 | 35 | KEEP | 哈喽，你好。还不错啊，讲个故事吧… |
| 30 | 15 | KEEP | 请查一下今天最新的科技财经新闻。 |
| 37 | 16 | KEEP | 现在可以吗？海马汽车为什么今天大跌？ |
| 41 | 15 | KEEP | 格式，你这个格式太紧密了。注意换行。 |
| 44 | 3 | drop | 还不错。 |
| 45 / 49 / 52 / 95 / 98 / 102 | 0 | drop | （无语音环境段） |
| 53 | 1 | drop | 嗯。 |
| 70 | 20 | KEEP | 哈喽…What's your name? My name is [NAME]. |
| 84 | 92 | KEEP | I like China… |
| 91 | 155 | KEEP | and so that we…（英文新闻） |
| 92 | 18 | KEEP | 很好，非常好。这就是一个测试。好吧，就这样吧。 |

**结论：转写维度 8 KEEP / 8 drop**——与被听验淘汰的段（"还不错。"、提示音、
咳嗽段）完全一致；drop 的段仍可由 `value=high` 豁免（以模型输出为准）。

## 5. 下游影响

| 消费方 | 影响 |
|---|---|
| 检索 / day_summary | **事件行减少**（低价值段不再落行）；summary 拼装回退（不含 location）；含语音高价值段不受影响 |
| events.payload | `place` 消失、`value` 新增；旧行仍含 `place`——存在新旧混合窗口（force 或自然更替，见 §6 V5） |
| 派生 JSON | 同步（审计照写不变——被 drop 的段仍保留完整审计数据可复查） |
| bff / 客户端 | `place` 从 timeline 响应消失（`extra="allow"` 直通机制不变）；Android 按 2026-09-15 裁定后期统一适配 |
| 文档 | `docs/data/lvdata_audio_analysis.md`、`lvdata_mcp_service_v2.md` 修订裁定在**实施时**更新为二轮口径 |

## 6. 待拍板（二轮）

| # | 问题 | 推荐 |
|---|---|---|
| V1 | `value` 命名与枚举 | `value` + `low\|high` 二值——判定目标就是"是否落入 events"，二值最可判定；如后续需区分中等价值再扩枚举 |
| V2 | 词数口径 | CJK 字符逐字 + 拉丁词（`[A-Za-z0-9']+`），阈值 >5（§4.1 实测） |
| V3 | 非法 value 兜底 | → `low`（降噪优先）；键缺失走重试/fail 路径（与其他键一致） |
| V4 | place 移除连带 | summary 拼装回退 + 一轮 Q3 废止（§3）——确认 |
| V5 | 旧数据 | 同前：dev 可弃 → 部署后对 audio 跑一次 force 重分析（副作用：bump → day_summary 重生成 + 重嵌）；生产自然更替 |

## 7. 实施步骤（拍板后）

1. `audio.py`：`SEGMENT_KEYS` + prompt + `normalize_segment`（删 place、加
   value）+ `_word_count` + `is_silent` + 拼装回退 + 模块 docstring。
2. 测试：`place` 用例移除/替换为 `value` 用例；`_word_count` 口径用例（中文/
   英文/混合/标点/空串）；静音规则用例（>5 词放行、value=high 豁免、
   transcript 空 + value=low → silent、长转写 value=low 仍放行）；fixtures 同步
   （`test_audio.py`、`test_rebuild_flow.py`）；全量单测绿。
3. 文档：data 两处修订裁定更新为二轮口径。
4. 验证：dev 实跑（force 重跑 16 段）——核对 value 输出与落行集合符合 §4.1
   预期；抽查被 drop 段与 value=high 豁免段。

## 8. 收尾记录（一轮，2026-09-16）

**已完成**（见 audio_analysis_plan.md S1–S3）：
- `audio.py` 全部触点：`SEGMENT_KEYS` 7 键、新 prompt（一轮 §2.2 逐字）、
  `normalize_segment` 重写（先置循环只覆盖 list 键，防 None→[] 陷阱）、
  `is_silent` 重定义、summary 拼装（location 置首/原值/null 不拼）、模块 docstring；
- 测试：既有 fixtures 同步（`test_audio.py`、`test_rebuild_flow.py`）+ 新增 5 用例
  （兜底默认、count 强转、is_silent 四分支含"仅 place 仍静音"、location 置首、
  null 不拼）——**lvdata 全量 157 绿**；
- 文档：`docs/data/lvdata_audio_analysis.md`（修订裁定横幅）、
  `docs/data/lvdata_mcp_service_v2.md`（修订裁定）、本文档。

**dev 实跑（2026-09-16 完成，用户拍板执行）**：重启 lvdata-mcp（加载新代码）→
`trigger_analysis(stages=["audio"], force=true)` 全量重分析 16 段（16 ok / 0 failed，
<1 分钟）→ 核对通过：
- events.payload 新结构（实样：`place.location="室内"`、`people={"count":1,
  male_count:null,...}`、`objects` 无 `time_s`；`background_sounds`/`events`/
  `quality` 缺席）；派生 JSON 同步为新结构；
- summary 列 location 置首生效；location null 的段不拼；
- Q5 连带效应按预期：16 事件 bump → flush 门控放行 → embed + day_summary 重生成
  （09-15/09-16 两日），`flush_state` 水印推进，后续轮次 `flush:false`。

**review 修复（2026-09-16）**：`_coerce_count` 对 inf/nan 形态（模型输出
`"inf"`/`"Infinity"`/`1e999`）兜底——修复前 `OverflowError` 未捕获会让整个
audio stage 失败且该文件不落失败态（每轮重试再炸的毒丸）。已补回归用例。

**二轮实施（2026-09-16 完成）**：真机听验后删除 `place`、新增 `value`、重定义
静音规则（**取代一轮的 place 字段/Q3 决策与静音规则**），按 §7 执行：
- `audio.py`：`SEGMENT_KEYS` 7 键（删 place 加 value）、prompt 换 §2.2、
  `normalize_segment` value 归一（非法→low）、新增 `_word_count`（CJK 感知）、
  `is_silent` 重写（词数>5 或 value=high）、summary 拼装回退、模块 docstring；
- 测试：place 用例替换为 value/词数/新静音规则用例 + fixtures 同步
  （`test_audio.py`、`test_rebuild_flow.py`）——**lvdata 全量 157 绿**；
- 文档：data 两处修订裁定更新为二轮口径（`lvdata_audio_analysis.md`、
  `lvdata_mcp_service_v2.md`）。

**二轮 dev 实跑（2026-09-16 完成，用户拍板）**：重启 + force 16 段 →
**`silent: 8`，与 §4.1 推演完全一致**：
- 落行集合 = {14,30,37,41,70,84,91,92}（8 条，与推演逐一吻合）；`value` 分布
  high=7 / low=1——event 92 判 low 但词数 18 > 5，**OR 语义实测放行**；
- 新行 payload 无 `place`、含 `value`；summary 列无 location 置首（transcript
  开头）；被 drop 段派生 JSON `silent:true, value:low`（审计照写）；
- flush 连带：8 个 bump + 最旧 pending 超时 → 门控放行 → embed + 两日
  day_summary 重生成，水印推进至 08:52:46Z。

**迁移痕迹清理（2026-09-16 已处理，用户拍板）**：force 重跑对"改为静音"的段
不删除旧事件行（upsert 只在非静音时写）——8 条被 drop 段的旧 schema 行已手动
删除（备份 `/tmp/audio_stale_rows_backup.json`），并完成派生清理：
`lvdata-mcp reindex --vectors`（FTS 全量重建 87 docs 与 events 逐一相符、0 孤儿；
向量按会话重建 87 chunks，被删 ref 在 vec_chunks/vec_state 均清零）；09-15
day_summary 置 pending 后自动重生成（event_count 54→46）。生产自然流程
（段只分析一次）无此迁移痕迹。

## 9. 评估意见（二轮，评审）

1. **三点方向均有数据支撑**：place 的推测性（§0.1）与 objects 兜底的噪声
   （16 条中 6 条纯环境段）在听验中直接可感；`value` 把"该不该落行"从多个
   字段的隐式组合收敛为一个显式判断——可解释、可回归测试。
2. **词数口径是必须解决的坑**（§2.3/V2）：中文无空格，`split()` 会把整句算成
   1 词。CJK 感知计数后推演结果（8/8）与听验判断一致；若不修此点，中文事件
   将几乎清零、规则退化为"全看 value"。
3. **`value` 的失败模式要防滥用**：模型有"宁高勿低"倾向——若 value 普遍判
   high，规则等价失效。建议：prompt 已给正反例（§2.2）；上线后观察 value=high
   占比（>50% 即需收紧标准）。
4. **objects/people/emotions 降级而非删除**是合理的：零额外成本（已在输出中），
   保留客户端展示与未来重判空间；被 drop 段的完整审计 JSON 照写，数据不丢。
5. **短句重要内容依赖 value 豁免**（如"着火了！"）：prompt 的 high 定义已含
   "重要事件声音"，实施后抽查此类豁免是否生效。

## 10. 三轮调整（2026-09-16）：day_summary 输入去重（已实施）

**提案（用户）**：audio 事件若 `transcript` 有文字 → **不进入 day_summary**（chatlog
已含完整文字），该事件仅保留 FTS/向量；`transcript` 为空 → 以 event summary 进入
day_summary。

### 10.1 调研结论

**现状**：`day.py::_generate_one` 拉取当日全部 events（`SELECT type, ts_ms,
summary, event_id, updated_at ... WHERE ts_ms BETWEEN ...`）→ 每事件渲染一行
`[HH:MM] type: summary` 进 LLM 输入。audio 事件的 summary 拼装含 transcript →
**与 chatlog 铸造成的 chat/transcribe/translate 事件在日总结输入里重复出现**
（同一段话被喂两遍，且 audio 版是二次 ASR 的等值复述）。

**实证（dev 库 8 条保留 audio 事件 × 三 jsonl 对照）**：
- 7 条精确命中：4 条对话段 → history.jsonl；2 条中英对话段 → translate.jsonl；
  1 条"这就是一个测试" → transcribe.jsonl；
- 1 条英文新闻段探针词 "warfighting" 未命中（ASR 措辞差异），但同段关键词
  arms race / grappling / satellites / jamming **全部命中** translate/transcribe.jsonl
  ——内容等值，**结论：全部 8 条的文字在 chatlog 中均已覆盖，前提成立**。
- 语义映射（与既有认知一致）：对话=history、翻译/同传=translate、转写=transcribe。

**正向副作用（顺带收益）**：过滤后，audio 事件自身的更新（如 force 重分析 bump）
**不再触发 day_summary 重生成**——`up_to_date` 护栏基于过滤后集合判定，仅
audio-with-text 变化的日不再浪费 LLM 调用（今日二轮 force 时两日重生成的现象
在三轮后不会再出现）。

### 10.2 修改点（计划）

| 位置 | 改动 |
|---|---|
| `day.py` `_generate_one` | SELECT 增 `payload`；过滤：`type='audio' 且 payload.transcript 非空` 的行从 rows 移除；**LLM 输入 / stats 计数 / event_count 一致地基于过滤后集合**；过滤后空集 → 沿用 no-events 语义（删 pending 占位、skip；discover 游标防忙循环） |
| `day.py` 模块 docstring | 输入规则注明（audio-with-text 只进检索） |
| FTS / 向量 | **不变**（事件仍在索引中——用户提案的"只做 fts, vector"） |
| events 表 / audio 分析 | 不变 |
| 测试 | `test_day_summary.py`：①含文字 audio 行被排除、无文字 audio 行保留；②护栏——仅 audio-with-text 更新时 skip(up_to_date)；③过滤后空集走 no_events 路径；`test_rebuild_flow.py` 核对 day_summary 断言不受影响 |
| 文档 | `recording_data_process.md`（day_summary 输入描述）、`lvdata_mcp_service_v2.md` §6.5 注记、本文档 §8/§10 |

### 10.3 待拍板

| # | 问题 | 推荐 |
|---|---|---|
| T1 | 过滤实现位置：SQL（`json_extract`）vs Python 后过滤 | **Python 后过滤**（payload 反正要读、与护栏/空集逻辑同处、可测性好） |
| T2 | `event_count` / stats 口径 | **随过滤**（与 LLM 输入一致，避免"事件数含未喂给 LLM 的项"） |
| T3 | 「有文字」判定 | `transcript.strip() != ''` 即可；**不**额外核对 chatlog 是否真有对应行（复杂且实证已覆盖；前提成立时 chatlog 必有） |
| T4 | 过滤后空集语义 | 与 no-events 相同（删占位、skip；事件游标防忙循环——已分析无循环风险） |
| T5 | 是否同步排除其他类型（如 window/video） | 否（本次只处理 audio；video caption 与 chatlog 不重复） |

### 10.4 实施记录（2026-09-16）

按 §10.3 推荐执行（T1 Python 后过滤 / T2 口径随过滤 / T3 `strip()!=''` 判定 /
T4 空集同 no-events / T5 不涉其他类型）：
- `day.py`：模块 docstring 补输入过滤规则；新增 `_is_duplicated_audio(etype,
  payload)`；`_generate_one` SELECT 增 `payload`，过滤后集合贯通 LLM 输入 /
  stats / event_count / up_to_date 护栏；
- 测试新增 3 例：`test_generate_excludes_audio_with_transcript`（排除+保留+
  event_count 口径）、`test_audio_only_update_does_not_regenerate`（护栏：仅
  audio-with-text 更新不烧 LLM）、`test_generate_audio_only_day_skips_no_events`
  （过滤后空集 → no_events 语义）——**lvdata 全量 160 绿**（157+3）；
- 文档：`recording_data_process.md`（阶段表 day_summary 行）、
  `lvdata_mcp_service_v2.md`（修订裁定）、本文档。

**dev 验证（2026-09-16 完成，用户拍板）**：重启加载新代码 → 两日置 pending
重生成 → **event_count 精确减 4**（09-15: 46→42，09-16: 39→35，即各自排除的
audio-with-text 条数）；护栏实测——bump 一条 audio 事件 → flush 轮照跑（embed
ok）但 **generated_at 不变**（脏检测 → up_to_date skip，零 LLM 调用）。

**补充（2026-09-16，stats 双口径）**：`stats` 增加 `audio_segments_total`
——当日 audio 事件**真实总数**（含被排除的带文字段）；`audio_segments` 维持
"进入日总结的非语音段数"（T2 口径）。二者互为事实/输入口径，供日卡展示选择。
测试断言补入（排除用例同时校验 1/2）；dev 重生成实测：两日均为
`audio_segments=0 / audio_segments_total=4`（恰为各自当日 4 条 audio）。
