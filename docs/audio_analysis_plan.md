# 音频分析 schema 调整 — 开发与测试计划

> 依据：`docs/audio_analysis.md`（评审意见已并入，2026-09-16）。实施口径 = 该文档
> §6 推荐（Q3 拼 location 且置首位/原值无标签；Q4 null；Q6 不 bump）+ §7 步骤。
> 原则：每站 = 代码 + 对应测试，**交付即全绿**；文档口径以设计文档 §3 触点表写死
> 项为准（防 None→[] 陷阱）。

## 工作分解

### S1 代码改造 + 既有测试同步（audio.py 全部触点）

| # | 任务 | 说明 |
|---|------|------|
| 1.1 | `SEGMENT_KEYS` → 7 键（+place,people；−background_sounds,events,quality） | 跟随生效：`missing_schema_keys` |
| 1.2 | `AUDIO_SYSTEM_PROMPT` 换 §2.2 草案逐字 | 含 count「无人或无法判断填 0」 |
| 1.3 | `normalize_segment` 重写（§3 写死口径） | 先置循环只覆盖 list 键（emotions/objects）；place/people dict 兜底；count 强转（str 数字/float 取整，非法/None→0）；male/female 非法→null；**专防 None→[] 陷阱** |
| 1.4 | `is_silent` 重定义 | `not transcript and not objects and not people.count`；place 不参与；docstring 写依据 |
| 1.5 | summary 拼装（Q3） | 序列 `[location, transcript, summary, emotion_text]`，原值无标签，null 不拼 |
| 1.6 | 模块 docstring :6 同步 | 静音规则描述过时（含 summary/emotions），改为新规则 |
| 1.7 | 既有测试同步 | `tests/test_audio.py` fixtures（:88-90/:146-147/:169-171 等）与静音用例改新 schema；`tests/test_rebuild_flow.py` fixture；**保持套件绿** |

### S2 新增用例（§7.2 清单）

| # | 用例 | 断言点 |
|---|------|--------|
| 2.1 | place/people 缺失兜底 | 不踩 None→[] 陷阱：place → `{"location":null,"description":""}`；people → count 0 / male·female null |
| 2.2 | count 强转分支 | `"2"`→2；null→0；`1.5`→1；非法串→0 |
| 2.3 | is_silent 四分支 | transcript / objects / people.count 各撑非静音；**仅 place 有仍静音** |
| 2.4 | Q3 拼装 | 有 location → summary 列以 location 开头；location null → 不拼 |

### S3 文档同步

`docs/data/lvdata_audio_analysis.md`（schema 表 :104-140、prompt :313-323）；
`docs/data/lvdata_mcp_service_v2.md`（按惯例加带日期修订裁定：audio schema 变更）；
`docs/audio_analysis.md` 头部状态 → 已实施 + 收尾记录。

### S4 验证（§7.4）

1. 单测全量：`tools/lvdata-mcp/tests` 绿（python312 解释器）。
2. dev 实跑：起 lvdata-mcp（或以现有服务）实跑一段音频 → 核对派生 JSON 与
   events.payload 为新结构（place 出现、三字段消失、people 为对象）。
3. （按 Q5）对 audio 跑一次全量刷新——REST `POST /api/analysis/trigger`
   （MCP `trigger_analysis` 已删除）：`curl -X POST -H "Authorization: Bearer $LVDATA_MCP_TOKEN" -H 'Content-Type: application/json' -d '{"stages":["audio"],"force":true}' http://127.0.0.1:8090/api/analysis/trigger`——
   注意副作用：无条件 bump updated_at → 涉事日 day_summary 重生成 + 重嵌。
   **执行前与用户确认**（动 dev 库数据）。

## 顺序与关键路径

- 关键路径：1.3/1.4（normalize 与静音判定，其余为文案/清单跟随）→ 1.7（旧测试绿）
  → S2 新用例 → S3 → S4。
- 1.5（拼装）与 1.3 有依赖：location 取值依赖归一化后的 place。
- 每站完成 = 对应测试绿 + 设计文档条款对照（§3 触点表逐行核）。

## 里程碑

| M | 内容 | 出口条件 |
|---|------|----------|
| M1 | S1+S2 完成 | lvdata 套件全绿（含新用例 2.1–2.4） |
| M2 | S3 完成 | 三份文档同步 |
| M3 | S4.1/4.2 完成 | 全量绿 + dev 实跑新结构核对通过；S4.3 待用户拍板 |

## 收尾记录（2026-09-16）

**已完成**：S1（audio.py 全部触点 + 既有 fixtures 同步）｜S2（新增 5 用例：
兜底默认 / count 强转 / is_silent 四分支 / location 置首 / null 不拼）——
lvdata 全量 **157 绿**｜S3（三份文档：`lvdata_audio_analysis.md` 修订裁定横幅、
`lvdata_mcp_service_v2.md` 修订裁定、`audio_analysis.md` 收尾）｜S4（重启服务 +
`trigger_analysis(audio, force)` 全量 16 段重分析 16 ok，payload/派生 JSON 新结构
与 summary 置首核对通过；flush 连带效应符合预期——两日 day_summary 重生成、
水印推进）。**一轮全部完成，无剩余**。

**2026-09-16 二轮（真机听验后）**：删 `place`、新增 `value`、静音规则重定义
（transcript 词数>5（CJK 感知）或 value=high 才落行）——已实施（代码 + 测试
4 换 4 + data 两份修订裁定），lvdata 全量 157 绿；dev 实跑（重启 + force 16 段）
完成：**silent=8 与推演一致**（落行 8 条、OR 语义实测、flush 连带两日重生成）。
设计与推演见 `audio_analysis.md` §0–§9；迁移痕迹（被 drop 段旧行残留）已清理
（删 8 行 + FTS/向量重建 + 09-15 日总结重生成）。

**2026-09-16 三轮（day_summary 输入去重）**：调研（8 条实证：audio 转写文字均
在 chatlog 中覆盖）+ 实施 + dev 验证完成——`day.py` 输入过滤（audio 且 transcript
非空不进 LLM 输入/event_count；护栏与空集语义同步）+ 3 新用例，lvdata 全量
160 绿；dev：两日重生成 event_count 精确减 4（46→42 / 39→35），护栏实测（audio
bump 后 flush 照跑、generated_at 不变、零 LLM 调用）；文档同步
（`recording_data_process.md`、v2 修订裁定、`audio_analysis.md` §10）。补充
（同日）：`stats.audio_segments_total`（当日真实 audio 数）——dev 实测两日
0/4 双口径正确。
