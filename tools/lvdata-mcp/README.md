# lvdata-mcp

LvData MCP Service v2 — 多模态生活数据建档、分析与查询服务（设计文档：
[`docs/data/lvdata_mcp_service_v2.md`](../../docs/data/lvdata_mcp_service_v2.md)）。

- **Ingestion 平面**（cs 单链路，详见 `docs/recording_data_process.md`）：
  Watcher 每 60s 轮询 **chat_server.db 的 `recording_files` 表**（服务端唯一
  事实源，不再扫文件系统），进程内完成同步（recording_watch）、chat 三 jsonl →
  events（analysis/chatlog）、音频/视频分析（analysis/audio|vision）、
  索引/向量增量、日摘要（summary/day）——无 subprocess。
- **Query 平面**：Streamable HTTP MCP server，**5 个只读 tools**（数据面 = `events`）；
  另有运维 REST 端点 `/api/status`、`/api/analysis/trigger`（同 token 鉴权，
  见 [REST 端点](#rest-端点运维面)）。

## 前置条件

| 项 | 说明 |
|----|------|
| Python | ≥ 3.12 |
| 数据根 | 配置 `data.root_dir`（默认 `/opt/lvdata`），**目录必须已存在**；可为空目录——首次启动 watcher 自动建表并经 cs 同步建档 |
| 百炼凭证 | `DASHSCOPE_API_KEY` + `DASHSCOPE_API_HOST`（纯域名 = 百炼控制台「API Host」列原值）+ `DASHSCOPE_WORKSPACE_ID`——audio/vision/summary/embedding 各 LLM 阶段需要；经 lvdata_mcp.yaml 的 `${ENV_VAR}` 占位符解析，缺失即启动 fail-fast，API key **为空**同样 fail-fast（validate_required 要求非空） |
| ffmpeg | 音频分析 ogg→wav 转码需要（`audio_sample_rate: 0` 直传 ogg 时不需要） |

## 快速开始（本机开发）

复用仓库根 `.venv`（`pip install -e .` 的 `[image,vec]` 组合已含在根 venv 的单命令安装中）：

```bash
# 1. 安装（仓库根；等价于根 venv 的 server-install 单命令）
cd <repo> && python3.12 -m venv .venv
<repo>/.venv/bin/pip install -e packages/sdk-python -e "services[test,dev]" -e "tools/lvdata-mcp[image,vec,dev]"
#   [vec] 必装（dev yaml 配了 embedding provider，缺 sqlite-vec 时向量操作 ImportError）；
#   [image] 建议装（Pillow，入库图像缩放——vision 分析用）

# 2. 环境（占位符解析需要；lvdata_mcp.yaml 引用 DASHSCOPE_API_KEY / DASHSCOPE_API_HOST / DASHSCOPE_WORKSPACE_ID）
#    首选：cp .env.example .env（仓库根）填好后即可——服务启动时自动加载根 .env
#    （setdefault：shell 注入优先，docs/ali_bailian_provider.md §9），无需 export。
#    export 仍可用且优先（上下二选一即可）：
export DASHSCOPE_API_KEY=sk-xxx
export DASHSCOPE_API_HOST=llm-xxxx.cn-beijing.maas.aliyuncs.com
export DASHSCOPE_WORKSPACE_ID=llm-xxxx

# 3. 数据根（必须已存在，可为空）
mkdir -p /opt/lvdata

# 4. 启动（任意 CWD；配置 = configs/dev/lvdata_mcp.yaml，自动发现）
.venv/bin/lvdata-mcp serve
#   或指定：LVDATA_MCP_CONFIG=configs/dev/lvdata_mcp.yaml lvdata-mcp serve

# 5. 验证
curl -s http://127.0.0.1:8090/healthz        # {"status":"ok"}（liveness）
curl -s http://127.0.0.1:8090/readyz         # 首轮 poll 完成前返回 503；200 后可查询
```

启动即生效：watcher 首轮 poll 自动同步（recording_files）→ chatlog/音视频处理 →
索引 → 日摘要，无需手动步骤。
`reindex` 仅在异常重建时使用（数据源 = events 表）。

### 重置某日分析产物（reset）

按日期重置 recording session 的分析产物：删除已 parse 但无事件的 audio/video 源
文件，purge 已知事件（未知 type 保留），清理/重建 FTS/向量/日摘要，由下一次
poll 重新分析。默认要求 server 已停止（`reset -> start` 的推荐顺序）：

```bash
systemctl stop lvdata-mcp                          # 或停掉手动启动的 serve
lvdata-mcp reset --date 2026-09-28 --dry-run       # 先看计划（零写入）
lvdata-mcp reset --date 2026-09-28 --yes           # 实际执行
systemctl start lvdata-mcp
```

- `--date` 支持 `YYYY` / `YYYY-MM` / `YYYY-MM-DD`（本地日期 = `watcher.timezone`）。
- `--json` 输出机器可读计划/结果；失败时统一输出 `{"error":{"code","message"}}`。
  `--dry-run`/未确认路径只读，不建表/不迁移。
- 实际执行前做三态 `/healthz` 探测：running 或 unknown 都拒绝；只有明确
  connection refused 放行。`--allow-running-server` 才允许覆盖并输出 warning
  （接受 day_summary/day_stats 可能 stale 的风险）。
- 部分完成（`failed_unlinks` / `vectors_pending` / `sessions_failed`）退出码为 1。
- 汇总区分物理删除与 DB 行删除：`files_unlinked`/`bytes_unlinked` 是本轮实际
  unlink（含随后被 defer 的 session），`files_deleted`/`bytes_deleted` 是已提交
  的 files 行；向量 sweep 删除数见 `vectors_swept`。
- 不删除 chat/meta 源文件，不调用 LLM；无备份。完整语义与风险见
  [`docs/data/lvdata_mcp_cli_reset.md`](../../docs/data/lvdata_mcp_cli_reset.md)。

### 配置

配置主体为 `lvdata_mcp.yaml`（§4.1 加载链）：

1. `--config` CLI → env `LVDATA_MCP_CONFIG` → `configs/<env>/lvdata_mcp.yaml`
   （跟随仓库 `_project_root()` 约定）→ CWD `lvdata_mcp.yaml`；全部缺失 = 启动失败。
2. `${ENV_VAR}` 占位符递归解析（未设置即报错）。
3. `LVDATA_MCP__` 前缀双层下划线 env 覆盖任意嵌套键（如
   `LVDATA_MCP__WATCHER__POLL_INTERVAL_S=30`）。

关键字段（完整 schema 见定稿 §4.3）：

| 配置 | 默认 | 说明 |
|------|------|------|
| `language` | `en` | 分析产物输出语言（单值）；支持 App 16 个 canonical code：`en, zh-Hans, zh-Hant, ja, ko, ru, de, fr, it, es, pt, hi, id, th, vi, ar`；空/非法/未知 → `en` |
| `server.host` / `port` | `127.0.0.1` / `8090` | 监听地址；非回环绑定必填 token |
| `data.root_dir` | `/opt/lvdata` | 数据根 |
| `data.chat_server_db_path` | `<root_dir>/chat_server.db` | chat server DB（**recording_files 源表**，ATTACH 只读扫描；与 chat server **同机部署**，运行账户对 DB 目录有写权限——WAL 只读打开需要，见 chatserver_no_redis.md §3.8.1） |
| `watcher.poll_interval_s` | `60` | 轮询间隔 |
| `watcher.concurrency` | `4` | 分析类 LLM 有界并发（成本护栏） |
| `watcher.max_files_per_poll` | `200` | 单轮 audio/vision 各处理上限（历史重分析分批） |
| `watcher.day_summary_max_per_poll` | `10` | 单轮日摘要生成上限 |
| `watcher.timezone` / `timezones` | `Asia/Shanghai` / `[]` | 日摘要时区（timezones 表 seed） |
| `providers.audio/vision/summary/embedding` | dashscope | vendor/base_url/model/api_key/dim/workspace_id（仅 DashScope：业务空间专属域名配套，驱动 X-DashScope-WorkSpace 头；未配置不带头） |
| `providers.embedding.min_vec_score` | `0.50` | `semantic_search` 的向量相似度下限（余弦）——低于它的向量命中不返回（响应带 `filtered`），`0` = 关闭。默认值为 dev 标定值，**换 embedding 模型需按 `docs/data/lvdata_mcp_tools.md` §15.2 复标** |

## Agent 客户端配置

```jsonc
{
  "mcpServers": {
    "lvdata": {
      "type": "http",
      "url": "http://127.0.0.1:8090/mcp",
      "headers": {"Authorization": "Bearer ${LVDATA_MCP_TOKEN}"}
    }
  }
}
```

典型工作流：`get_recent_events(days=1)`（最近动态）→ `get_timeline`（或 `search` 定位）→
`get_event` 取详情（video 事件自动附关键帧图片看佐证）。

agent 连上后（MCP `initialize`）还会收到一段英文 `instructions`（服务身份与用法摘要，逐字留档于
`docs/data/lvdata_mcp_tools.md` §14.2——**改动需评审**，它是每个 agent 的常驻上下文）；`serverInfo`
的版本号即本服务版本（与 `/api/status` 的 `service.version` 同源）。`lvdata-mcp`
控制台命令（serve/reindex/eval/stage/reset）、MCP 与 REST 的界面文案统一英文，
不随 `language` 配置变化（`language` 只影响分析数据内容；开发脚本
`rebuild_dev.py` / `scripts/*` 不在此范围）。

## Tools（§12 v2 集合）

| Tool | 说明 |
|------|------|
| `search` | FTS5 全文检索（CJK bigram）：日期窗 `start_date`/`end_date`（`YYYY-MM-DD` 含端点）+ `timezone`；命中带 `event_ref` 与可读 `timestamp` |
| `semantic_search` | 语义检索（FTS+向量 RRF 融合，入参与 `search` 同构：日期窗 + `timezone`，命中带可读 `timestamp`/`via`）；`with_context=true` 时附 ±2min 邻域（**轻量投影**，同 `get_timeline`：`{event_ref, type, timestamp, source, summary}`，细节按 `event_ref` 调 `get_event`）；相同 query 命中进程内缓存（最多 500 条）；命中带 `vec_score`（余弦相似度）/`fts_score`，低于 `min_vec_score` 的向量命中不返回（此时响应带 `filtered{min_score, best_dropped}`） |
| `get_timeline` | events 表时间线：日期窗 `start_date`/`end_date`（`YYYY-MM-DD` 含端点）+ `timezone`；按日分组（日升序、日内升序，轻量投影），`[ts_ms, type, event_id]` 三元组游标翻页 |
| `get_event` | 单事件详情（chat/audio/window/video/agent_job）；`timezone` 缺省/无效 = 服务配置时区；video 且关键帧存在时返回值附图片（image content） |
| `get_recent_events` | 近 N 个本地日的事件清单（days=1 今天、上限 31；轻量投影、按日分组日倒序，详情按 `event_ref` 调 `get_event`） |

## REST 端点（运维面）

同一进程、同一 bearer token；`/mcp` 与 `/api` 均需鉴权，`/healthz` `/readyz` 免鉴权。

| 端点 | 说明 |
|------|------|
| `GET /api/status` | 服务与数据新鲜度（watcher/pipeline/index 状态、pending/failed 分析积压、day_summary 状态）；配 embedding 时另含 `cache.query_embeddings`（查询向量缓存的 size/hits/misses） |
| `POST /api/analysis/trigger` | 手动触发 pipeline（body `{session_id?, stages?, force?}`，幂等入队；stages: inventory/chatlog/audio/video/reindex/embed/day_summary/day_stats，缺省 = 全链） |

```bash
curl -s -H "Authorization: Bearer $LVDATA_MCP_TOKEN" http://127.0.0.1:8090/api/status
curl -s -X POST -H "Authorization: Bearer $LVDATA_MCP_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"stages": ["reindex"]}' http://127.0.0.1:8090/api/analysis/trigger
```

## 测试

```bash
.venv/bin/python -m pytest tests/ -q
```

## 生产部署

**systemd**（`deploy/lvdata-mcp.service`）：`--config` 指向部署环境的
`lvdata_mcp.yaml`；`DASHSCOPE_API_KEY` / `DASHSCOPE_API_HOST` / `DASHSCOPE_WORKSPACE_ID`
经环境变量注入占位符（yaml 无
`${LVDATA_MCP_TOKEN}` 占位符——dev 配置的 `server.token` 是字面量；
`LVDATA_MCP_TOKEN` 仅是客户端侧调用约定，非回环绑定时的服务端要求见下）。

**Docker**（`deploy/Dockerfile`）：

```bash
docker run -d --name lvdata-mcp \
  -v /opt/lvdata:/opt/lvdata \
  -e DASHSCOPE_API_KEY=sk-xxx \
  -e DASHSCOPE_API_HOST=llm-xxxx.cn-beijing.maas.aliyuncs.com \
  -e DASHSCOPE_WORKSPACE_ID=llm-xxxx \
  -p 8090:8090 lvdata-mcp
```

对外暴露（非本机）时：`server.host: "0.0.0.0"` + 必填 `LVDATA_MCP_TOKEN`，前面挂
Caddy/nginx 终结 TLS。

## 目录

```
src/lvdata_mcp/
  main.py        入口（serve / reindex / eval / stage / reset 子命令，--config）
  reset.py       按日期重置分析产物（删除无事件媒体、purge/重建派生面）
  config.py      lvdata_mcp.yaml 加载 + pydantic schema（§4）
  ddl.py         v2 建表 + §7 幂等改名迁移 + 时间戳归一化
  recording_watch.py  recording_files 同步（cs 单链路；ATTACH 只读 + keyset + 单事务；
                      session.json 映射、finalized→day_force 在此处理）
  analysis/      chatlog（chat 三 jsonl → events）+ 音频/视频分析（events upsert + files 状态机）
  summary/day.py 日摘要生成与调度（§6.5；day_scan_state 水位 + 快照语义）
  pipeline.py    进程内 stage 编排（inventory/chatlog/audio/video/reindex/embed/day_summary）
  watcher.py     双 loop：poll（recording_files 同步 + 日摘要发现）+ work（round 固定序列，状态驱动）
  index.py       FTS5（数据源 = events.summary，CJK bigram）
  vec.py         sqlite-vec 事件级 chunk（chunk_id = event_ref）+ RRF
  tools.py       FastMCP tool 定义（§12 v2 集合）
  server.py      Starlette 装配（/mcp + /api 运维 REST（status/trigger）+ /healthz + /readyz + auth + 限流）
```
