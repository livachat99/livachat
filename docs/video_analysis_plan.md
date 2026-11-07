# 视频分析 filter 阶段 prompt 精简 — 开发与测试计划

> 依据：`docs/video_analysis.md`（三巡拍板：**V7 = 按 window 去重取每窗口首个派生
> 关键帧 → ts 升序**；V1 并入 quality / V3 移除 static / V4 extract 术语同步 /
> V5 dev 清理 / V6 不进拼装——按推荐执行；V2 派生定义已由用户决策）。
> 探针 3 连轮稳定验证已通过（本轮实施前验证，见设计文档 §7）。
> 原则：每站 = 代码 + 对应测试，**交付即全绿**；两侧测试套件（lvdata / services）。

## 工作分解

### S1 vision.py 改造 + lvdata 测试（filter 5 键 schema + 派生 + 静窗零事件）

| # | 任务 | 说明 |
|---|------|------|
| 1.1 | `FRAME_KEYS` → 5 键 | `frame_id / quality / duplicate_of / value / reason`（parse 校验自动跟随） |
| 1.2 | `FILTER_SYSTEM_PROMPT` 换三巡精简版 | 逐字取自设计文档 §2.1（与探针脚本核对一致） |
| 1.3 | `normalize_frame_entry` 重写 | 删 blur/quality_issues/novelty/key_rank 分支；quality/value 归一（∉{low,high}→low）；**duplicate_of 强转 str**（int-0 陷阱）；**派生 is_key_frame** |
| 1.4 | `rank_by_row` 机制整体删除 | `_process_window`/`_extract_all`/`_extract_chunk` 签名链 + payload 去 key_rank |
| 1.5 | 窗口事件条件化 + `static` 移除（V3） | 审计 JSON 与 record_run 照旧；仅派生关键帧集非空时 upsert window 事件 |
| 1.6 | extract prompt 术语（V4）+ 模块 docstring | "屏幕截图内容分析引擎"→"图片"；docstring 记新规则 |
| 1.7 | lvdata 测试 | fixture 换 5 键；新用例：派生规则（dup 非空→非 key 即使 value=high）、int-0 强转、静窗零事件（审计照写/文件 ok）；清理 key_rank 断言（test_vision:235、test_tools:168、conftest 视频/窗口 fixture） |

### S2 bff `_derive_images` 排序替换（V7）+ bff 测试

| # | 任务 | 说明 |
|---|------|------|
| 2.1 | `timeline/service.py::_derive_images` | 排序改为：**每窗口取 ts 最早的视频事件（window 去重）→ ts 升序取前 N**；无 `window_id` 兜底按 event_ref 独立；docstring 同步 |
| 2.2 | bff 测试 | `tests/unit/bff/test_timeline_api.py` images 排序断言按 V7 更新（同窗口多帧只取首个）；conftest 视频 fixture 去 key_rank；全量绿 |

### S3 文档

data 两份修订裁定（`docs/data/lvdata_video_analysis.md`、`docs/data/lvdata_mcp_service_v2.md`）+
本文档收尾记录。

### S4 验证

1. 探针 3 连轮稳定性：**已完成**（run 3/4/5 逐帧一致、零越界，设计文档 §7.8）。
2. dev 实跑（需拍板）：重启 lvdata-mcp → 触发 video 全量重跑（REST，MCP `trigger_analysis`
   已删除）：`curl -X POST -H "Authorization: Bearer $LVDATA_MCP_TOKEN" -H 'Content-Type: application/json' -d '{"stages":["video"],"force":true}' http://127.0.0.1:8090/api/analysis/trigger`
   重跑 38 帧 → 核对新 schema 事件/静窗零事件/两类残留量（V5 清理）→ **bff 日卡
   核对**（需一并重启本地 server 加载 bff 改动）。

## 顺序与关键路径

- 关键路径：1.3（归一+派生，其余为跟随）→ 1.5（事件条件化）→ 2.1（bff 排序）→
  两包全绿 → S4。
- 1.2 的 prompt 文本以设计文档 §2.1 为准（探针脚本同源核对）。

## 里程碑

| M | 内容 | 出口条件 |
|---|------|----------|
| M1 | S1+S2 完成 | lvdata 与 services 两包测试全绿（含新用例） |
| M2 | S3 完成 | data 两份修订裁定更新 |
| M3 | S4.2 完成 | dev force 重跑核对 + bff 日卡按 V7 排序正确 |

## 收尾记录（2026-09-18）

**已完成**：S1（vision.py 全部触点 + lvdata 测试——**168 绿**）｜S2（bff
`_derive_images` V7 替换 + services 测试——**1055 绿**）｜S3（data 两份修订裁定 +
设计文档收尾）。验证前置已完成：探针 3 连轮稳定（run 3/4/5 逐帧一致、零枚举
越界，设计文档 §7）。

**S4.2 dev 实跑（2026-09-18 完成，用户拍板全量执行）**：重启 + `force=true` 重跑
38 帧 → 2 窗口 / 38 帧 / 0 失败；新 schema 事件实证（窗口无 static、video 无
key_rank）；V5 清理 8 条降级帧残留（备份 /tmp/video_stale_rows_backup_20260918.json）
+ `reindex --vectors`（113 chunks）；livachat-server 启动后 **日卡 images = 2**
（每窗口首个关键帧、ts 升序）——V7 实测正确。

**实跑中修复的 bug**：extract 模型回显 marker 原文 "frame N" → 解析器 `_norm_frame_id`
归一（filter/extract 同改）+ 回归用例（lvdata **169 绿**）；与本次 prompt 改动
无关（旧首行亦复现）。

**review 修复（2026-09-18，2 处，设计文档 §8）**：① 编造索引 → `parse_filter_output`
拒绝（原缺校验 → 下游 `rows[int(fid)]` IndexError 崩整个 video stage）；②
`duplicate_of` 空串/`"null"/"none"` → None（原被当重复帧，关键帧静默丢失）。
回归用例 +3（lvdata **172 绿**）。**全部完成，无剩余**（代码/测试/文档/dev 验证）。
