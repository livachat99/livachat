# 视频分析 extract 阶段精简 — 开发与测试计划

> 依据：`docs/video_analysis.md` **§9**（第二轮提案，待 review；D1–D4 已按推荐
> 拍板：place 进 summary / 图谱节点消亡接受 / "截图"→"图片" / 探针先行）。
> 原则：每站 = 代码 + 对应测试，**交付即全绿**；两侧测试套件（lvdata / services）。
> 范围：lvdata-mcp vision **extract 阶段**；filter 阶段（已实施 `5ed88a9`）不动；
> 图谱/replay/store meta/前端不改（保留旧字段读取，D2）。

## 工作分解

### S0 extract 只读探针（先行门槛）

| # | 任务 | 说明 |
|---|------|------|
| 0.1 | 新增 `scripts/probe_video_extract.py` | 只读跑 §9.1 草案 prompt：从存量 video 事件取关键帧（4 帧）→ 按 `video_extract_chunk_size` 分 chunk 实测；镜像生产 `_image_parts` 标记与 enable_thinking 400 降级 |
| 0.2 | 3 连轮实测 | 每轮输出逐帧 caption/place/objects/ocr/people + **place 原始值/null 率**；判据：字段完备（无缺帧）、place 输出形态稳定（3 轮一致或差异可解释）、null 规则遵循（屏幕截图类帧 place=null） |
| 0.3 | 出口判定 | 稳定 → 进入 S1；有噪音 → 按 §9.6 回退方案（place 降级为不进 summary）提请拍板 |

### S1 vision.py 改造（探针复核后落生产）

| # | 任务 | 说明 |
|---|------|------|
| 1.1 | `EXTRACT_KEYS` → 5 键 | `["caption", "place", "objects", "ocr", "people"]`（normalize 分支跟随） |
| 1.2 | `EXTRACT_SYSTEM_PROMPT` 换 §9.1 草案 | 删 scene/apps/events/actions 段与 events 要求句；加 place（用户原文描述）；"关键帧截图"→"关键帧图片"（D3） |
| 1.3 | `normalize_extract_entry` 加 place 归一 | 非字符串 → None；空串/`"null"/"none"` 字面量（不区分大小写）→ None（复用 §8 duplicate_of nullish 处理） |
| 1.4 | `_video_summary_text` 重写 | `caption + place(非空) + ocr[].text`（D1；删 scene/apps/actions 拼装） |
| 1.5 | 探针复跑 | 生产改用同一草案后复跑 1 轮，输出与探针一致 |

### S2 lvdata 测试

| # | 任务 | 说明 |
|---|------|------|
| 2.1 | fixture 5 字段化 | `test_vision.py::_extract_json`、`tests/conftest.py` 两处视频 payload（:113-115、:149-151） |
| 2.2 | 既有断言更新 | `test_vision.py:239-241`（payload 字段 + summary 拼装新口径） |
| 2.3 | 新用例 | ① place nullish 归一（""/"null"/"none"/非字符串 → None；正常值保留）② summary 新拼装（caption+place+ocr，place 为空时不占位）③ place 缺失字段默认 None（宽松归一） |
| 2.4 | 全量绿 | lvdata 全套（含探针无关的过滤用例） |

### S3 services 测试 + 文档

| # | 任务 | 说明 |
|---|------|------|
| 3.1 | bff conftest 视频 payload | `tests/unit/bff/conftest.py:140-143` 改新 schema；**保留 1 条旧 schema 行**验证图谱/replay 对旧 payload 的兼容读取（D2 承重断言） |
| 3.2 | 既有断言更新 | `test_timeline_api.py:171`（scene → place/caption）；`test_graph_api.py:28-30`（旧行仍出 scene/app 节点；新行不出） |
| 3.3 | 文档 | `video_analysis.md` §9 收尾记录；`video_analysis_plan` 无关；data 两份修订裁定横幅（service doc 行 288/293/527/531、video doc §2.4/§2.5 标注被取代） |
| 3.4 | 全量绿 | services 全套 |

### S4 dev 验证（需拍板）

1. 重启 lvdata-mcp → 触发 video 全量重跑（REST，MCP `trigger_analysis` 已删除）：
   `curl -X POST -H "Authorization: Bearer $LVDATA_MCP_TOKEN" -H 'Content-Type: application/json' -d '{"stages":["video"],"force":true}' http://127.0.0.1:8090/api/analysis/trigger` 重跑 38 帧
   → 核对：place 分布（实拍帧非 null 率）、新 summary 搜索文本、payload 无被删字段
2. 重启本地 server（加载 bff 改动无——本轮 bff 仅测试改动，图谱写侧无改动）
   → 核对日卡/时间线/搜索/日总结输入
3. 图谱节点核对：scene/app 节点消亡为预期（D2）；object 节点仍在

## 顺序与关键路径

- 关键路径：0.x（探针门槛）→ 1.3/1.4（归一 + summary）→ 2.x → 3.1/3.2 → 两包全绿 → S4。
- 1.2 的 prompt 文本以设计文档 §9.1 为准（探针脚本同源核对）。

## 里程碑

| M | 内容 | 出口条件 |
|---|------|----------|
| M1 | S0 完成 | 探针 3 连轮输出稳定、null 规则遵循 |
| M2 | S1+S2 完成 | lvdata 测试全绿（含新用例） |
| M3 | S3 完成 | services 全绿 + data 两份修订裁定更新 |
| M4 | S4 完成 | dev force 重跑核对 + 搜索/日卡/图谱实证 |

## 收尾记录（2026-09-18）

**M1（S0）**：探针 3 连轮通过——place 语义正确（办公桌/卧室/房间推断）、零噪音、
字段完备；屏幕主体帧有保守 null 波动（已归因，非错误）。**M2（S1+S2）**：vision.py
四触点 + `_video_summary_text` 重写 + lvdata **170 绿**（+2 用例）——生产 prompt =
探针 = 设计文档 §9.1 逐字一致（程序化核对）。**M3（S3）**：services **1274 绿**
（conftest 新 schema / timeline scene→place / graph 旧 payload 兼容用例）+ data
两份修订裁定横幅 + 设计文档 §9 状态更新。**M4（S4）**：重启 + force 重跑 38 帧
**0 失败**；4 条 video 事件新 5 字段零残留（place：办公桌/null/家庭办公室或书房/
卧室或宿舍）、summary 新拼装实证；FTS/MCP search 命中新文本；day_summary 重生成
（39−4=35 双口径一致、吸收 place 语义）；bff 离线：图谱 scene/app 消亡（object
保留）、日卡 images=2 不变。

**review 修复（2026-09-18，1 处）**：审计 JSON `frame_id` 被模型回显覆盖（dev
12/12 frames 文件失真；仅人工核查用途、payload 未波及）→ 审计 doc 两处改
"entry 先展开、权威字段后覆写"+ 回归（fixture 按 "frame N" 实测形态）——lvdata
**170 绿**（跨包 4/4）。**全部完成，无剩余**（代码/测试/文档/dev 验证）。
