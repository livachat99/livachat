# session.json 静态化重构 — 开发与测试计划

> 依据：`docs/session_json_refactor.md`（三轮评审已并入，契约冻结）。
> 原则：每站 = 代码 + 对应测试 + 文档条款对照，**交付即全绿**；编号沿用设计
> 文档 §3 的 A/B/C/D/E 项号，便于双向追溯。
> 与设计文档 §6 实施顺序的差异（明示）：§3.D 的测试**拆入 S1/S2**（行为变更与
> 其测试同站交付，避免中站红）；§3.E 文档 → S3；§4 验证 → S4。其余顺序一致
> （A → B/C → 部署验证）。

## 工作分解

### S1 服务端写侧：静态化 + start 幂等首写（§3.A 项 1–7 + §3.D services 测试）

| # | 任务 | 产出 | 测试 |
|---|------|------|------|
| 1.1 | `_write_session_json()` 7 键 payload（删 timezone/status/last_active_at/finalized_at）；`started_at` 规范为 ms+Z（`timespec="milliseconds"` + `Z`，两处生成点：`start()`、`attach()`） | `session.py` | key 集合 == 7；格式断言 |
| 1.2 | `start()` 幂等首写：文件存在 → 跳过写盘 + 回读 `started_at` 还原 `_started_at_iso`；不存在 → 首写；**登记照常**（`record()` INSERT OR IGNORE） | `session.py` | 重启幂等用例（内容+mtime 不变、内存回读一致、无 touch/bump） |
| 1.3 | `_sync_session_json(register=)` → `_register_session_json()`（只保留首登记；去掉 touch 分支与参数） | `session.py` | — |
| 1.4 | `finalize()` 删写盘/登记两行；`flush_meta()` 整体删除；`service._tick()` 删 active 的 flush 块（保留跨天 finalize + idle TTL） | `session.py`、`service.py` | file_registry finalize 不再 touch |
| 1.5 | 注释同步：`sink.py:39`（时区不再落盘）、`file_registry.py:6`（不再重写）、`chat_store.py:373`（session.json 重写→仅首登记）、`models.py:228`（ticker 周期语义）、`service.py` ticker 注释 | 多文件 | 静态 grep |

### S2 lvdata：读侧静态化 + day_force 删除（§3.B 项 8–12 + §3.C 项 13–14 + §3.D lvdata 测试）

| # | 任务 | 产出 | 测试 |
|---|------|------|------|
| 2.1 | `_apply_session_json`：删 `prev` 查询与 finalize 转换块 → 读盘 → `_upsert_session` → `stats` | `recording_watch.py` | golden 更新 |
| 2.2 | `_upsert_session`：删 status/finalized_at 形参与转换；4 字段 `.get` 摄入**保留**；`started_at` 过 `normalize_ts()` | `recording_watch.py` | status `''`/timezone `'UTC'` 断言 |
| 2.3 | `_write_day_force`、`_to_epoch_ms`、`_compute_duration_ms` 删除；stats 去 `finalized`；`main.py:188` 打印同步 | `recording_watch.py`、`main.py` | 删 day_force 转换用例 |
| 2.4 | `day.py`：discover/`_day_generatable`/`_generate_one`/`_in_day_force`/`_check_day_force` 全删；docstring 更新 | `summary/day.py` | test_day_summary 更新 |
| 2.5 | `ddl.py`：删 `DAY_FORCE_SQL`/`V2_DDL` 条目；`migrate()` 补幂等 `DROP TABLE IF EXISTS day_force` | `ddl.py` | migrate 用例 + 表清单 |
| 2.6 | fixture 同步：`conftest.py`、`test_rebuild_flow.py`、`test_rebuild_dev.py`（去 4 字段）；新增"静态重同步幂等"用例 | tests | 全绿 |

### S3 文档（§3.E）

`recording_data_process.md`（写点表/登记表/读者描述/测试计划/§5.C）、
`server_recording_design.md`（ticker/finalize 写盘/§5.3 JSON 结构/配置注释）、
`timezone-support.md`、`data/lvdata_mcp_service_v2.md`（带日期修订裁定）、
`data/lvdata_inventory.md` §2.3。

### S4 验证（§4）

1. 单测：`services` unit/recording → 全量；`tools/lvdata-mcp` 全量。
2. 静态核数：`grep -rn "day_force\|flush_meta" services tools/lvdata-mcp/src` →
   仅剩 `ddl.py` 的 DROP 语句与历史沿革注释。
3. dev E2E（与 WS4.2 部署窗口合并，**用户侧**）：部署本分支 services/ → 录制
   跨 ticker 周期验证 session.json mtime 恒定、cs.recording_files 该行不 bump；
   `stage inventory --force` 无 finalized、无 day_force 表；停录 600s 后
   debounce 生成当日 day_summary。

## 顺序与关键路径

- 关键路径：**1.2（幂等首写）→ 2.1/2.2（读侧静态化）→ S4 静态核数**。
- S1 与 S2 代码互不依赖，但 S1 先行（语义源）；S3 可与 S4.1 并行。
- 每站完成 = 对应包测试全绿 + 设计文档条款对照 + 无回归。
- 交付后整支提交；dev E2E 结果回填本文档收尾记录。

## 测试策略

| 层 | 手段 |
|----|------|
| 单测 | pytest 两包各自套件（`services/tests/`、`tools/lvdata-mcp/tests/`） |
| 契约 | 服务端：session.json 静态性（内容/mtime/登记幂等）；lvdata：字段摄入但不消费语义（status `''`、finalized_at 不触发 day_force） |
| 静态 | grep 残留检查（§4.2） |
| 端到端 | dev 部署 + 真机录制（与 WS4.2 合并，用户侧执行） |
| 回归 | 每站跑两包全量 |

## 里程碑

| M | 内容 | 出口条件 |
|---|------|----------|
| M1 | S1 完成 | services 单测全绿；session.json 首写后内容/mtime 恒定 |
| M2 | S2 完成 | lvdata 单测全绿；grep 无 `day_force`/`flush_meta` 活代码残留 |
| M3 | S3+S4.1/4.2 完成 | 双包全量绿 + 静态核数通过；dev E2E 就绪（随 WS4.2 执行） |

## 收尾记录（2026-09-15）

**已完成**：S1（写侧静态化：7 键 + `start()` 幂等首写 + 回读 `started_at`；services
全量 1054 绿）｜S2（lvdata 读侧 + day_force 全删：`--get` 摄入保留、`normalize_ts`
收敛、ddl `DROP` 迁移；lvdata 全量 152 绿）｜S3（五份文档同步：写点/登记表、ticker/
finalize、§5.3 结构、时区链路、v2 修订裁定）｜S4.1 两包全量绿｜S4.2 静态核数：
`day_force` 仅剩 ddl.py DROP 与沿革注释、`flush_meta` 零残留。

**剩余（用户侧）**：S4.3 dev E2E——部署本分支 `services/` 到 dev chat server 后：
session.json mtime 首写后恒定、cs.recording_files 该行不再 bump；`stage inventory
--force` 无 finalized、库内无 day_force；停录 600s 后 debounce 生成当日 day_summary。
与 `recording_data_process_plan.md` 的 WS4.2 真实录制验证合并执行。

**注**：lvdata 套件须用 `python312` 解释器跑（`.venv` 无 services 包，`uv run`
会因 `recording` 导入失败误报 2 例）。
