# BFF 对外接口：overview / timeline / search / media

> 依据：当前代码逐文件阅读（`services/bff/*`、`services/app/application.py`、
> `tools/lvdata-mcp/*`（overview 数据来源）、`apps/web/mylife/*`），所有结论附 file:line；
> 未写代码中不存在的行为。更新日期 2026-09-25（2026-10-13 勘误：/mylife SPA 静态托管已随路由摘除，见 §5；§0.2 token 已启用校验）。
> 范围：4 个对外面——`/api/mylife/overview`、`/api/mylife/timeline`、`/api/mylife/search`、
> `/api/mylife/media/{device}/{session}/{kind}/{filename}`。
> `/api/mylife/graph`、`/api/mylife/graph/entity/{id}` 不在本文范围。
> 写面（删除 video 事件，`POST /api/mylife/events/remove`）见 `docs/bff_service_remove.md`；
> agent job 取消（`POST /api/mylife/agent-jobs/cancel`）见 `docs/android_agent_job_status.md`
> §7.2 ——本文只覆盖读面契约。
> §7 = overview 的数据来源（`day_summary` / `day_stats` 写入侧契约），§8 = overview 分页。

---

## 0. 公共契约

### 0.1 挂载与开关

| 事实 | 位置 |
|---|---|
| 全部 JSON/媒体接口挂在 `APIRouter(prefix="/api/mylife")`，子 router：timeline / graph / search / media | `services/bff/router.py:12-16` |
| `register(app)` 同时注册 `BffError` 异常处理器 | `services/bff/router.py:21-29` |
| 仅在 `config.bff.enabled`（默认 True）时挂载 | `services/app/application.py:385-389`；默认值 `services/shared/config/models.py:266` |
| 全局 GZip：`minimum_size=1024`，默认排除 `image/*`、`audio/*`、`video/*` 响应，206 分片整体跳过（media/Range 零影响） | `services/app/application.py:327` |
| 访问日志中间件：全部请求记录 method/path/ip/status/latency，4xx/5xx 或 >1000ms 升 warning（原 `/mylife/` 前缀跳过已随托管摘除删除） | `services/app/access_log.py:19-43` |

### 0.2 鉴权（当前语义）

`get_principal` 依赖（`services/bff/common/deps.py:39-49`）：

| 参数 | 类型 | 说明 |
|---|---|---|
| `token` | Query str，默认 `""` | **已校验**（2026-10-13，Phase 2 落地于本单点）：dev 模式 = 与 wire token `sha1_hex(auth.dev_token + "LivaChat")` 相等（2026-10-14 起为固定盐摘要，明文 secret 不上网；同 WS 握手，docs/server_auth.md）；失败 401 `{"error":{"code":"unauthorized","message":"authentication failed"}}` |
| `device_id` | Query str \| null | 当前唯一生效的请求级过滤；与 `config.bff.device_uuids` 白名单取交集（**overview 除外**：参数保留但失效，见 §7.3 E2）；不校验格式 |

所有 4 个面都接受 `?token=&device_id=`。

### 0.3 数据源：LvdataStore（只读 facade）

| 事实 | 位置 |
|---|---|
| 惰性构建：`LvdataStore.from_config(cfg.bff, cfg.recording)`；`data_root = bff.data_root or recording.base_dir`，`db = bff.lvdata_db_path or <data_root>/lvdata_inventory.db`，白名单 `bff.device_uuids`（空 = 全部） | `services/bff/common/deps.py:20-24`、`:31-36`；`services/bff/common/store.py:52-69`；配置 `services/shared/config/models.py:232-241` |
| 连接：`file:<db>?mode=ro`（URI 只读）、`row_factory=Row`、`PRAGMA busy_timeout=5000` | `store.py:73-79` |
| DB 文件不存在且查询走 `_connect` → `BffError("internal")` → **500** | `store.py:73-75` |
| 依赖表（`files` 已不再读取）：`recording_sessions`（`:110`）、`events`（`:149`、`:198`、`:226`）、`day_summary`（`:261`、`:315`、`:335`）、`search_docs` FTS（`:360`、`:370`、`:397`）、`timezones`（`:86`、`:335`） | `store.py` |
| 事件排序优先级（同 ts_ms 决胜）：`chat=0, transcribe=1, translate=2, window=3, video=4, audio=5, agent_job=6`，未知类型排最后；SQL 由该表生成 `CASE type` | `store.py:22-26`、`:33-37` |
| 事件行出库 `_event_out`：payload JSON 解析后按键合并（**同名列以列为准**），另存 `payload_dict`（原始 payload dict，供取真值，不上 wire） | `store.py:242` |

### 0.4 时区解析（`resolve_request_tz`）

`services/bff/common/deps.py:64-82`，对所有带 `timezone` 参数的接口生效：

| 输入 | 结果 |
|---|---|
| 未传 / 空串 | 默认时区 `config.bff.timezone`（默认 `Asia/Shanghai`，`models.py:241`） |
| 非法（`canonicalize_timezone` 返回 None） | `BffError("bad_request")` → 400 `invalid timezone: ...` |
| 合法且等于默认 | 短路口返回，不查表 |
| 合法但 `timezones` 表无此行 | 回退默认（debug 日志） |
| `timezones` 表缺失 / 空表 / DB 异常 | 放行请求时区（`timezone_known` 返回 None） |

规范形支持 IANA 名、`UTC`、固定偏移（`+08:00` / `GMT+8` 等，|offset| ≤ 14h），见 `services/shared/timeutil.py:20-80`。

### 0.5 错误契约

`BffError` → `JSONResponse{"error": {"code", "message"}}`（`services/bff/router.py:18-27`）：

| code | HTTP |
|---|---|
| `bad_request` | 400 |
| `not_found` | 404 |
| `range_invalid` | 416（**存量死键**：全仓无抛出点，media 的 Range/416 由 Starlette `FileResponse` 内部处理，不经 `BffError`） |
| 其他（含 `internal`） | 500 |

注意：FastAPI 自身参数校验失败（`pattern`、`ge/le` 越界等）返回框架默认 **422**，不走上述 envelope（用例：`services/tests/unit/bff/test_search_api.py:255-258`）。

---

## 1. `GET /api/mylife/overview`

日卡总览：**单表读 `day_summary`**（§7），按本地日返回计数与派生字段，支持 keyset 分页（§8）。

### 1.1 请求

| 参数 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `timezone` | str \| null | 非法 → 400 | 本地日分组时区；**overview 专用生效规则见 §7.3 E3**（非「最新时区」一律重定向到最新） |
| `limit` | int \| null | 1–400 | 天数页大小；缺省 = 全量（§8.1） |
| `cursor` | str \| null | `YYYY-MM-DD`，须与 `limit` 同传 | 严格早于该日的下一页（§8.1） |
| `token` / `device_id` | §0.2 | | `device_id` 对 overview **失效**（§7.3 E2） |

`services/bff/timeline/router.py:11-26` → `build_overview(store, principal.device_id, timezone, default_tz=cfg.bff.timezone, limit, cursor)`

### 1.2 响应（`OverviewResponse`，`services/bff/common/schemas.py:62-79`）

```json
{
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "days": [
    {
      "date": "2026-09-20",
      "counts": {"chat": 12, "transcribe": 8, "video": 3},
      "summary": "……",
      "hot_slots": [{"start_ms": 1758336000000, "end_ms": 1758342600000, "count": 21}],
      "images": [{"frame_id": "…", "ts_ms": 1758336000000, "event_ref": "…",
                   "media_url": "/api/mylife/media/<dev>/<sess>/video/<frame_id>.jpg",
                   "window_id": "…"}],
      "has_pending": false,
      "topics": [{"topic": "旅游计划", "detail": null, "search_words": "…"}]
    }
  ],
  "next_cursor": "2026-09-22",
  "total": 412
}
```

| 字段 | 语义 | 位置 |
|---|---|---|
| `days[]` | **按 `date` 降序**；分页模式 = 本页（≤ `limit` 天），全量模式 = 全表（超 `FULL_CAP_DAYS` 截断，§8.2.2） | `service.py:106-112` |
| `date` | 本地日 `YYYY-MM-DD`（= 行 `local_date`，按生效时区分组） | `service.py:114-127` |
| `counts` | `day_stats.counts` 透出：该日 events `type` 逐类原样计数（只含出现的类型） | §7.2.2 |
| `summary` | `day_summary.doc["summary"]`；**仅 `status=='ok'` 透出**，否则 `null` | `service.py:118`（§7.3 E11） |
| `topics` | `doc["topics"]` 经 `topic_items` 归一化：`{topic, search_words}` / legacy str（`search_words=None`）；`detail` 为历史字段**恒 null** | `services/bff/common/topics.py:9-21`；`schemas.py:23-34` |
| `hot_slots` | `day_stats.hot_slots` 透出（五类可展示事件 60min 切段、≤3 段） | §7.2.2；`service.py:139-147` |
| `images` | `day_stats.images` 透出 + BFF 组装 `media_url`（`window_id` 原样传递） | §7.2.2；`service.py:149-168`、`:279` |
| `has_pending` | 由行派生：`status=='pending'`，或 `status=='failed'` 且 `fail_count < 3` | `service.py:130-137`（§7.3 E1） |
| `next_cursor` / `total` | 分页字段（§8.2.2） | `schemas.py:73-79` |
| 条目级容错 | `hot_slots`/`images` 的畸形条目**丢该条目**（不 500）；`day_stats` 列缺失 / 坏 JSON / 非 dict → 空字段 | `service.py:139-168`（§7 交叉版本兼容） |

### 1.3 内部逻辑（`build_overview`，`service.py:73-112`）

1. `tz = resolve_overview_tz(store, timezone, default_tz)`：**overview 专有**的生效时区规则
   （请求时区恰为「最新」则用它，否则一律用最新；每次请求读一次 `timezones` 表，失败 fail-open）
   ——§7.3 E3/E10。timeline/search 仍走 `resolve_request_tz`，**两套规则有意不同**。
2. 一次 `store.day_summaries(tz, None, None, before=cursor, limit=limit+1)`（分页模式）或
   `limit=FULL_CAP_DAYS+1`（全量模式）取行 → 组装 `OverviewDay`（§8.2.2）。
3. `total`：分页模式一次 `count_days(tz)`；全量模式未触防呆 = 行数（**每请求查询次数汇总见 §6**）。
4. `device_id` 不参与任何过滤（§7.3 E2，参数保留）。

> `hot_slots` / `images` 的派生规则与常量真值来源在 lvdata-mcp（§7.2.2）；
> **改选图规则必须同步 Android**（`PeriodAggregator.collectImages` 按同规则跨日重建）。

---

## 2. `GET /api/mylife/timeline`

事件时间线（直接查 `events` 表），支持日期窗 / 类型 / session 过滤 + 游标分页。

### 2.1 请求（`services/bff/timeline/router.py:23-49`）

| 参数 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `date_from` / `date_to` | str \| null | `^\d{4}-\d{2}-\d{2}$`（pattern 不匹配 → 422） | null |
| `session_id` | str \| null | 按 `rsession_id` 过滤；不存在 → 404 | null |
| `types` | str \| null | 逗号分隔，取值 ∈ `EVENT_TYPES` 7 类；含未知 → 400；空串/未传 = 全类型 | null |
| `limit` | int | 1–500 | 200 |
| `cursor` | str \| null | 形如 `<local_date>|<offset>`（见 2.3-5） | null |
| `timezone` / `token` / `device_id` | §0.2、§0.4 | | |

### 2.2 响应（`TimelineResponse`，`schemas.py:95-100`）

```json
{
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "days": [{"date": "2026-09-20", "events": [ { /* TimelineEvent */ } ]}],
  "next_cursor": "2026-09-20|200",
  "total": 1234
}
```

`TimelineEvent`（`schemas.py:82-87`）为 **flat + extra=allow**：

| 字段 | 来源 |
|---|---|
| `ts_ms` / `type` / `device_uuid` | 事件行同名列 |
| `ts_utc` | `ts_ms` 转 UTC ISO（`service.py:296-297`） |
| `media_url` | 见 §4.2 计算规则（audio+segment_id / video+frame_id），否则 null |
| 其余列（如 `event_ref`）与 payload 键 | `_event_out` 合并结果直通；`payload["audio_summary"]` 仅 audio 类型且 `payload_dict.summary` 为真值时补齐（列 `summary` 是检索拼接串，非 LLM 摘要） |
| **不透出** | `event_id`、`payload`、`payload_dict`、`created_at`、`updated_at`、`rsession_id`、`session_id`（`service.py:276-293`） |

### 2.3 内部逻辑（`build_timeline`，`services/bff/timeline/service.py:199-273`）

1. `type_set`：`types.split(",")`；与 `EVENT_TYPES` 求差集，非空 → 400 `unknown event type(s): ...`。
2. 设备过滤：`device_id` 或服务端白名单任一非空 → `devices = list_sessions(device_id)` 去重（**空列表 = 无可见设备**，store 层返回空而非不过滤：`_events_where` 返回 None → 结果恒空，`store.py:174-177`、`:211-213`）。
3. 日期下推 SQL：`day_bounds_ms` 把 `date_from` → 当日 00:00:00.000、`date_to` → 当日 23:59:59.999（闭区间）；`ValueError` → 400（`timeutil.py:18-26`）。
4. cursor 解析：`rsplit("|", 1)` 取 offset，失败 → 400 `invalid cursor`；不带 cursor → 0。
5. 两分支：
   - **session_id 分支**：`store.timeline_events(session_id, start_ms, end_ms)`（内存切片分页）；结果为空且 `store.get_session(session_id)` 为 None → 404 `session not found`；再按 `type_set` 过滤。此分支的 `devices` 白名单不参与过滤，白名单仅通过 `get_session` 内部的可见性判断间接影响 404 判定（`store.py:132`、`:143-145`）。
   - **全局分支**：`count_events(...)` 取 total（同 where），`events_in_range(..., limit, offset)` 由 SQL `ORDER BY ts_ms, CASE 优先级, event_id LIMIT/OFFSET` 分页。
6. `next_cursor = f"{page 最后一条的本地日}|{start+limit}"`，仅当 `start+limit < total`；offset 越界时钳制到 total。
7. 分页结果按本地日分组，日降序输出。

---

## 3. `GET /api/mylife/search`

FTS 检索，检索源 = lvdata-mcp 建表的 `search_docs` 派生索引（用户/后端见 `docs/bff_search_fts.md`）。

### 3.1 请求（`services/bff/search/router.py:11-31`）

| 参数 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `q` | str | 去空白后空 → 400 `empty query` | `""` |
| `limit` | int | 1–200（越界 422） | 50 |
| `cursor` | str \| null | 形如 `0|<offset>`；非法 → 400 | null |
| `timezone` / `token` / `device_id` | §0.2、§0.4 | | |

### 3.2 响应（`SearchResponse`，`schemas.py:113-118`）

```json
{
  "schema_version": 1,
  "query": "股票",
  "total": 7,
  "next_cursor": "0|50",
  "hits": [{"date": "2026-09-20", "ts_ms": 1758336000000, "kind": "chat", "snippet": "…含命中片段的上下文…"}]
}
```

`SearchHit`（`schemas.py:106-111`）：`kind` 只可能是五类可展示事件 `chat | transcribe | translate | agent_job | video`（`service.py:27`）；`snippet` ≈120 字符窗口，命中位置偏前 1/3，截断处补 `…`（`service.py:24`、`:34-58`）。

### 3.3 内部逻辑（`build_search`，`services/bff/search/service.py:78-129`）

1. `q.strip()` 为空 → 400。
2. 停用词短路：去标点后为空或纯停用词（`的 了 吗 呢 是 在 啊 吧 哦 嗯`，`fts.py:15`、`:70-72`）→ 直接空结果，不落 FTS。
3. 构 FTS 查询串 `build_fts_query`：按分隔符切词 → CJK 连续串转重叠 bigram → 每词双引号包裹（防 FTS5 语法注入/歧义，`fts.py:57-67`）；无 token（如纯标点）→ 空结果（`MATCH ''` 是语法错误）。
4. 索引缺失（无 `search_docs` 表）→ 空结果 + warning，**无回退**（`store.py:384-395`）。
5. 设备白名单：`devices = list_sessions(device_id)` 去重；空列表 → store 层直接返回 0/空（安全关键，`store.py:377-378`、`:400-401`）。
6. `count_fts(match, SEARCH_TYPES, devices)`；**total==0 时放宽重查一次**：`relaxed_terms`（CJK bigram / ASCII 整词，丢弃 len<2 token）→ 词加引号 OR 连接（`fts.py:75-94`），再用放宽串 count + 检索。
7. `start` 钳制到 `[0, total]`；`search_fts` SQL：`WHERE search_docs MATCH ? AND type IN (5类) AND device_uuid IN (...)` `ORDER BY bm25 升序, ts_ms DESC, event_ref LIMIT ? OFFSET ?`（`store.py:386-391`）。
8. 命中构造：`date` = `ts_ms` 按生效时区转本地日；snippet 定位优先整串 `q`，找不到退化为首个命中词（放宽场景传 bigram 词集），都找不到则取文首 120 字符。
9. `next_cursor = "0|{start+limit}"`，仅当还有下一页。

---

## 4. `GET /api/mylife/media/{device}/{session}/{kind}/{filename}`

媒体文件出流（音/视频/图像原始文件），可选音频转码 m4a。

### 4.1 请求

| 参数 | 位置 | 约束 |
|---|---|---|
| `device` | path | 必须 UUID（正则 `^[0-9a-f]{8}-…$`，忽略大小写），否则 400 |
| `session` | path | 同上（`store.py:29-30`、`:394-395`） |
| `kind` | path | 仅 `audio \| video \| image`，否则 400 |
| `filename` | path | `^[0-9a-f-]{8,64}\.(ogg\|jpg\|m4a)$`，否则 400（`store.py:29`） |
| `format` | query | `ogg \| m4a`（pattern，其他值 422）；仅 `format=m4a AND kind=audio` 触发转码 |
| `token` / `device_id` | §0.2 | `principal` 显式 `del`（Phase 2 强制纳入同一鉴权，`common/media.py:34`） |

### 4.2 响应 / 文件解析（`common/media.py:23-45`、`store.py:432-451`）

- 成功：`FileResponse` 文件字节流，`Cache-Control: public, max-age=31536000, immutable`，**不设** `Content-Disposition`（保 `<audio>` 内嵌播放）。
- `media_type`：`audio→audio/ogg`、`video/image→image/jpeg`；`format=m4a` → `audio/mp4`。
- 校验顺序（全部失败先于 IO）：
  1. `device`/`session` 非 UUID → 400；
  2. 服务端白名单非空且 `device` 不在其中 → **404** `media file not found`（不泄漏存在性）；
  3. `kind` 非法 → 400；
  4. `filename` 不匹配 → 400；
  5. `(data_root/device/session).resolve()` 必须落在 `data_root` 下、`…/kind/filename` 必须落在前者下（字符串前缀校验，防路径穿越）；
  6. 非文件 → 404。

### 4.3 m4a 转码（`_transcode_m4a`，`common/media.py:48-68`）

- 缓存：`<data_root>/_cache/m4a/{sha256(源绝对路径)[:16]}-{原名去扩展}.m4a`；已存在且 size>0 直接复用。
- 未命中：`ffmpeg -y -i <src> -c:a aac -b:a 96k -movflags +faststart <tmp>.m4a`，`timeout=120s`，成功后 `replace` 落位；`CalledProcessError / OSError / TimeoutExpired` → 删除 tmp + `BffError("internal")` → 500。
- 注：`BffConfig.media_cache_dir` 字段当前**未被任何代码读取**（全仓仅定义处），实际缓存目录固定为 `data_root/_cache/m4a`。

### 4.4 media_url 生成（供 overview/timeline 引用）

`service.py:300-305`：

| 条件 | URL |
|---|---|
| `type=audio` 且有 `segment_id` | `/api/mylife/media/{device_uuid}/{rsession_id}/audio/{segment_id}.ogg` |
| `type=video` 且有 `frame_id` | `/api/mylife/media/{device_uuid}/{rsession_id}/video/{frame_id}.jpg` |
| 其他 | null |

---

## 5. `GET /mylife/*`（SPA 静态托管）——已摘除

> 2026-10-13：/mylife SPA 静态托管已随装配删除（`_mount_web_dist` 函数与 `bff.web_dist` 配置一并移除，`/mylife*` 返回 404）。Web app 已不维护（决策见 `docs/mylife/android_change_v3.md`、`docs/bff_replay_removal.md` §1.2）；本节原契约内容（StaticFiles 挂载、token 入口、访问日志跳过等）随之失效，如需回溯见 git 历史。

---

## 6. 汇总：每请求的库查询面

| 接口 | SQL 面 |
|---|---|
| overview | **§7 单表化后**：`timezones` 1 次小查询（取最新时区，E10）+ `day_summary` 1 次（**全量模式**；触 FULL_CAP 防呆时另 +1 次 `COUNT(*)`）。**§8 分页模式**（传 `limit`）：`day_summary` 页查询 1 次 + `COUNT(*)` 1 次（+ 上面的 `timezones`）。设备过滤已失效（E2）——`recording_sessions`/`events`/`files` 均不再读 |
| timeline | 全局分支：`count_events` + `events_in_range`（各含时间窗/类型/白名单下推）；session 分支：`events` 1 次（内存分页）+ 可能的 `recording_sessions` 探测 |
| search | `search_docs` count（精确，0 命中时再 count 一次放宽）+ 检索 1 次；（未停用词/索引缺失短路时 0 次） |
| media | 0 次（纯文件系统；m4a 另加 ffmpeg 子进程） |

表缺失/DB 异常的读侧容错口径：`events`/`day_summary`/`search_docs`/`timezones` 相关查询捕 `sqlite3.Error` 或先判表存在，返回空/None（`timeline_events` 返回 None，`events_in_range` 返回 []，`day_summaries`/`count_days`/`latest_timezone` 返回 []/0/None）。**例外 = DB 文件不存在**：`_connect` 抛 `BffError("internal")` → 500（`store.py:73-75`）；只有 `timeline_events`/`day_summaries`/`count_days`/`has_search_docs`/`search_fts`/`count_fts`/`timezone_known`/`latest_timezone` 做了 `db_path.exists()` 前置（返回 None/[]/0/False），`list_sessions`/`events_in_range`/`count_events` 没有——timeline 全局分支直接在 `count_events` 处 500（非静默降级）。

---

# §7 overview 数据来源：day_summary / day_stats（写入侧契约）

> overview 的 `days[]` **只来自 `day_summary` 单表**。`counts` / `hot_slots` / `images` 由
> lvdata-mcp 的 `day_stats` stage 预计算进该表的 `day_stats` 列；BFF 侧零派生——只做 JSON
> 解析、`status`/`fail_count` 派生（`has_pending`）与 `media_url` 组装。
>
> 文件路径约定：本节出现的 `ddl.py` / `summary/*.py` / `watcher.py` / `pipeline.py` /
> `main.py` / `tools.py`（MCP）/ `consts.py` 均在 `tools/lvdata-mcp/src/lvdata_mcp/` 下；
> 未标路径的 `store.py` / `service.py` / `schemas.py` 指 BFF 侧（`services/bff/`）。

## 7.1 表与列

| 事实 | 位置 |
|---|---|
| `day_summary`：`PRIMARY KEY (timezone, local_date)`；列 = `utc_offset_min` / `day_start_ms` / `day_end_ms` / `day_summary`（LLM 产出的 doc JSON）/ `model` / `status` / `event_count` / `fail_count` / `error` / `generated_at` / **`day_stats`**；另有 `idx_day_summary_status` | `tools/lvdata-mcp/src/lvdata_mcp/ddl.py:59-80` |
| `day_stats_state (timezone PRIMARY KEY, last_scan_at TEXT)`：`day_stats` stage 的增量扫描游标 | `ddl.py:148-152` |
| 写法分工：LLM 阶段（`summary/day.py`）与 `day_stats` stage **只写各自的列**——`day.py` 的 upsert 不含 `day_stats`；stage 的 UPDATE 不触碰 `status`/`day_summary`/`model`/`generated_at`/`fail_count` | `summary/day.py:501`、`:588`；`summary/day_stats.py:176`、`:197` |
| doc JSON 字段：`schema_version / timezone / local_date / generated_at / model / stats / summary / moments / topics / usage` | `summary/day.py:480-501`（组装 + upsert） |

## 7.2 契约

### 7.2.1 `day_summary` 增列（DDL）

```sql
-- 新库：DAY_SUMMARY_SQL 内含该列
day_stats       TEXT NOT NULL DEFAULT '{}',
-- 存量库：DAY_SUMMARY_ALTERS + ensure_schema 幂等 ALTER
```

`ddl.py:74-76`、`:175`（ALTER 清单）、`:362`（`ensure_schema` 应用）。

### 7.2.2 `day_stats` JSON 契约

```jsonc
{
  "schema_version": 1,
  "computed_at": "2026-09-25T07:31:00.000Z",  // 扫描快照时刻 T_scan
  "counts": {"chat": 57, "audio": 263, "video": 6},   // 日窗内 events.type 原样逐类计数
  "hot_slots": [{"start_ms": 1758336000000, "end_ms": 1758342600000, "count": 21}],
  "images": [{"frame_id": "…", "ts_ms": 1758336000000, "event_ref": "…",
              "window_id": "…", "device_uuid": "…", "rsession_id": "…"}]
}
```

- `counts`：**不去重、不合并 transcribe/translate**，只含当日实际出现的类型（不补零）。
- `hot_slots`：五类可展示事件（`chat/agent_job/video/transcribe/translate`）按 60min 间隔切段；段数 > 3 时反复合并「相邻间隔最小」的对（并列取最左）。
- `images`：`video` 事件按 `window_id ?: event_ref` 分组轮转选取，池上限 15，ts 升序输出；**只存原始字段**——`media_url` 由 BFF 出口组装（`service.py:279` 单 helper，overview 与 timeline 共用）。
- 派生常量唯一出处：`tools/lvdata-mcp/src/lvdata_mcp/consts.py:35-45`（`SPLIT_GAP_MS` / `MAX_HOT_SLOTS` / `HOT_SLOT_TYPES` / `DAY_IMAGE_POOL`）；派生函数 `summary/day_stats.py:70`（`derive_hot_slots`）、`:100`（`derive_images`），三字段组装 `:145`。
- `has_pending` **不在 JSON 内**（由行 `status`/`fail_count` 派生，见 §7.3 E1）。
- 无事件日不落行：`days[]` ≈ 有事件且被 stage 访问过的日。
- 消费方约束：**改 `images` 选图规则必须同步 Android**（客户端按同规则跨日重建，契约见 `docs/android_timeline_image_gallery.md` §3.2；实现 `PeriodAggregator.collectImages`）。

### 7.2.3 写入方：`day_stats` stage（lvdata-mcp）

| 事实 | 位置 |
|---|---|
| 常跑组第 5 位（`video` 之后、`reindex` 之前），每 round 必跑、不受 flush 门控：`ROUND_STAGES = ("inventory","chatlog","audio","video","day_stats","reindex")` | `watcher.py:41`、`:188` |
| 注册面 4 处：`Pipeline.STAGES` + `_dispatch`、`watcher.ROUND_STAGES` + 构造注入、MCP `TriggerParams.stages`、CLI `stage` choices | `pipeline.py:29`；`watcher.py:41`、`:167`；`schemas.py`；`main.py:45-47`、`:148`、`:167` |
| 1) **目标时区 = LLM 活跃时区集合**（最近使用的 enabled 时区 ∪ 配置默认时区，≤2）——与 LLM 生成面共用同一 helper，避免为非活跃时区落行导致 `status` 恒 pending 的「假分析中」；非活跃时区的游标/占位行原样保留，再激活续跑 | `summary/timezones.py:19-38`（`DaySummary` 与 `DayStats` 共用） |
| 2) 增量候选 = `events WHERE updated_at > last_scan_at`（游标写**扫描开始时刻**：扫描期间并发落入的事件下轮必见）→ 映射 `(tz, local_date)` 去重；**只扫 events，不读 files** | `summary/day_stats.py:176` |
| 3) 逐日**单次**日窗查询（`ts_ms` 在日窗内 且 `updated_at <= T_scan`），内存派生三字段 | `summary/day_stats.py:197` |
| 4) upsert：行存在 → `UPDATE day_stats`；行不存在且去重后集合非空 → `INSERT OR IGNORE`（`status='pending'` 占位行，`utc_offset_min`/`day_start_ms`/`day_end_ms` 填真实值）；**去重空日不落新行**（镜像 `day.py:54-65` 的去重判定） | `summary/day_stats.py:176`、`:150` |
| 5) 竞态兜底：候选日事件被删空 → 删除其 `status != 'ok'` 行（对齐 `day.py:231-240`）。事件被整体清理造成的 `ok` 残留行不清理（§7.3 E6） | `summary/day_stats.py` |
| `force=True`（MCP `trigger_analysis(["day_stats"], force=True)` 或 CLI）：忽略游标全历史重算，**全局完成时把游标推进到本次 T_scan** | `summary/day_stats.py`；`main.py:178` |
| 幂等：重复执行结果一致；崩溃恢复靠 `day_stats_state` 游标。成本：每 round ≤1 次游标查询 + 每 dirty 日 1 次日窗查询（无 LLM、无网络） | |

### 7.2.4 `doc.stats` 保留（同一行并存两套计数，**勿混用**）

- `doc.stats` = **LLM 输入口径**（`events` 去重后条数、transcribe/translate 并入 `chat_messages`、`audio_segments` 去重后段数、`audio_segments_total` 当日 audio 总数），本地计算、仅供 debug LLM 交互（`summary/day.py:437-439`、`:487-488`）。
- `day_stats.counts` = **wire 口径**（events `type` 原样逐类计数，见 §7.2.2）。`counts.audio` == `doc.stats.audio_segments_total`，其余无对应值。
- MCP `get_day_summary` 返回选中列 + 解析后的 doc（含 stats），输出不变（`tools.py:220-230`；`store.py:374-414`）。

## 7.3 规则约定（E1–E15）

| # | 约定 | 说明 |
|---|---|---|
| E1 | `has_pending` 由行派生：`status=='pending'`，或 `status=='failed'` 且 `fail_count < MAX_FAILURES`(3) | BFF 不读 `files`（`store.day_has_pending` 已删）；达上限的永久失败行不亮徽章、不驱动客户端轮询。常量与 lvdata-mcp（`day.py:48`、`watcher.py`）三处同步（`service.py:25`） |
| E2 | overview **不做设备过滤**：`device_id` 参数仍接受但失效（`day_stats` 为全设备合并口径） | 部署前置：`bff.device_uuids` 为空 / 单设备。timeline / search 的设备过滤不受影响；白名单非空时 media 仍会 404（`store.py:436-437`） |
| E3 | 生效时区 = 请求时区恰为「最新时区」则用它，否则**一律用最新**；未传时区同样适用 | 「最新」= `timezones` 表 enabled 行按 `(updated_at DESC, timezone)` 取首（镜像 lvdata-mcp 的「最近使用」判定，`day.py:167-183`）。非法时区 → 400；表缺失/空表/DB 异常 → fail-open 放行请求值。timeline/search 仍走 `resolve_request_tz`（**两套规则有意不同，勿统一**） |
| E4 | `counts` 口径 = 日窗内 events `type` 逐类原样计数（不去重、不合并） | 与保留的 `doc.stats`（LLM 输入口径）并存、用途分离（§7.2.4） |
| E5 | `images` 只存原始字段；`media_url` 由 BFF 单 helper 组装 | lvdata-mcp 不感知 BFF 路由；`service.py:279`（overview 与 timeline 共用） |
| E6 | `days[]` 来源 = `day_summary` 行 | 覆盖 = 有事件且被 stage 访问过的日；LLM `ok` 行在事件被清理后仍会出卡（残留行不清理） |
| E7 | `day_stats` 带 `schema_version` + `computed_at` | 版本演进与排障；日窗查询带 `updated_at <= T_scan` 上界（幂等收敛） |
| E8 | stage 落的 `status='pending'` 占位行会被 LLM discover 更早看到 | 最终语义一致；`INSERT OR IGNORE` 不覆盖已有行 |
| E9 | 新鲜度 ≤ 1 个 poll 周期（默认 60s，`config.py:140`）+ 队列等待 | 客户端本就 60s 轮询 + `has_pending` 驱动 |
| E10 | overview 每次请求读一次 `timezones` 表（E3 规则的必需输入） | 行数 = 曾用过的时区数（与数据量无关），失败 fail-open。`timezone_known`（行存在判定、不看 enabled）与 `latest_timezone()`（enabled + recency）**两套有意不同，勿统一**；`store.py:335-352` |
| E11 | `summary` / `topics` 仅 `status=='ok'` 透出 | `_finish_failed` 不清旧 doc（`day.py:577-598`），无门控会透出陈旧内容 |
| E12 | 去重空日不落新行（已有行仍 `UPDATE day_stats`） | 当日事件全部是被去重的 audio 时（`day.py:54-65`）不 INSERT——否则 LLM 的 no_events 分支会删 pending 行 → 日卡消失且游标已推进不再重建 |
| E13 | `force` 全局完成时推进游标；**回填首选 CLI** | CLI `run_stage` 不经 watcher，无 `stage_timeout_s`（1800s）限制；MCP force 走 watcher 超时后需重复触发（幂等可续跑） |
| E14 | tz 切换窗口期（接受）：新时区抬升后 overview 立即只读新时区行 | counts/images ≤1 round 可见；summary/topics 按 `day_summary_max_per_poll`(10) 逐日回填（`config.py:149`），期间历史日 pending 徽章亮起 + 客户端 60s 轮询常开（最坏 ≈ 历史天数/10 分钟） |
| E15 | `trigger_analysis(stages=["day_stats"], session_id=X)` 与不带 `session_id` **等价**（始终全局处理） | `session_id` 仅作触发前的会话存在性校验（`tools.py:303-311`），不影响计算范围——E13 的「完成推进游标」在任意触发形态下成立 |

## 7.4 消费方与行为影响

| 消费方 | 用法 | 位置 |
|---|---|---|
| Android（唯一活跃消费方） | `counts["video"]`/`counts["audio"]` 求和 → Gallery/Audio 入口段数；`hasPending` → 「分析中…」徽章 + 60s 轮询停止条件；`hotSlots`/`images`/`summary`/`topics` 渲染 | `PeriodAggregator.kt:131-137`；`MylifeRepository.kt:70-146`；`MylifeDtos.kt:54-70` |
| Android **不传** `device_id` | `api.overview(timezone = …)`；DTO `ignoreUnknownKeys = true`（BFF 新增响应字段向前兼容） | `MylifeRepository.kt:88`、`:95` |
| Web SPA | 实调 `/overview` 且读 `days[].date`/`counts`（`HeatStrip` 按已加载集合求和归一化柱高）；类型声明仍是 v1 旧契约（`sessions`/`total_sessions`） | `apps/web/mylife/src/api/hooks.ts:13-14`；`views/TimelineView.tsx:111-146`；`components/HeatStrip.tsx:4-10`；`api/types.ts:35-46` |
| MCP `get_day_summary` | 返回 `day_summary` 选中列 + 解析后的 doc（含 stats） | `tools.py:220-230`；`store.py:374-414` |
| 测试 | BFF 单测夹具自建 `day_summary` + `day_stats` 手工固化；lvdata-mcp 单测断言 `doc["stats"]`（保留口径的守护） | `services/tests/unit/bff/conftest.py`；`tools/lvdata-mcp/tests/test_day_summary.py` |

**客户端可见口径**：`device_id` 对 overview 无效（E2）；`days[]` 边界 = `day_summary` 行（E6）；
`has_pending` 熄灯时机 = **日总结 `status` 转 ok**（受 debounce `summarize_quiet_s`
默认 600s 与 flush 门控影响）；已 ok 日再出新事件的窗口期内该日 `has_pending=false`；
tz 切换窗口期见 E14。

## 7.5 验证与回填基线（P0/P2 产物）

| 产物 | 位置 / 命令 |
|---|---|
| **P0 基线夹具**：`day_stats_events.json`（48 事件 / 4 日，覆盖日界、60min 边界、并列合并、轮转/同 ts 决胜/池上限、多设备）+ `day_stats_golden.json`（由当时 BFF `_derive_*` 固化的逐字段期望）+ `gen_day_stats_golden.py`（一次性生成器，留档） | `tools/lvdata-mcp/tests/fixtures/` |
| **P2 stage 用例**：等价（对 golden 逐字段）、游标推进/不漏并发、幂等、去重空日、INSERT 真实列、force 推游标、E15 target 忽略 | `tools/lvdata-mcp/tests/test_day_stats.py` |
| **回填入口**：`lvdata-mcp --config <cfg> stage day_stats --force`（先 CLI 回填再启动/重启 serve）；覆盖度核对 `SELECT COUNT(*) FROM day_summary WHERE day_stats='{}'` | `main.py:178` |
| **BFF 侧用例**：day_stats 透出、`has_pending` 四分支、ok 门控、单次查询 spy、交叉版本（缺列/`'{}'`/坏 JSON/畸形条目）、E3-D″ 各分支 | `services/tests/unit/bff/test_timeline_api.py` |

---

# §8 overview 分页（days keyset）

## 8.1 请求

| 参数 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `limit` | int \| null | 1–400 | **天数**页大小；缺省 = 全量（≤ `FULL_CAP_DAYS`，E2） |
| `cursor` | str \| null | `YYYY-MM-DD`（合法日历日）且**必须与 `limit` 同传** | 语义 = 「严格早于该日」（keyset，不重发该日） |
| `timezone` / `token` / `device_id` | §0.2、§0.4 | | `device_id` 对 overview 恒失效（E2，§7.3） |

- **分页模式判据 = `limit` 已传**（E3）。
- 非法 `cursor` → 400 `invalid cursor`；只传 `cursor` 不传 `limit` → 400 `cursor requires limit`。
- `cursor` 在路由上**不加 `pattern=`**（否则非法形态走 FastAPI 的 422）——校验在 `service.py:62`
  （`_validate_cursor`），与 `/timeline` 的 400 语义一致。
- `limit` 越界 → 422（`router.py:19` 的 `Query(ge=1, le=400)`）。
- `services/bff/timeline/router.py:11-26` → `build_overview(store, device_id, timezone, default_tz, limit, cursor)`。

## 8.2 响应与服务端实现

### 8.2.1 响应字段

```json
{
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "days": [ /* 本页，date 降序 */ ],
  "next_cursor": "2026-08-12",
  "total": 412
}
```

`next_cursor` / `total` 为追加式可选字段（老客户端忽略；`schema_version` 保持 1）。

### 8.2.2 组装规则（`build_overview`，`service.py:73-112`）

| 规则 | 内容 |
|---|---|
| 页窗口 | `ORDER BY local_date DESC` + `local_date < cursor`（严格早于）；页大小 = `limit`；**多取 1 行**（`LIMIT limit+1`）判 has_more，不依赖 `total` |
| `next_cursor` | **先截到 `limit` 条，再取最后一天的 `local_date`**；无更早 → `null`。多取的 1 行只用于判 has_more，**不得**当作 cursor（否则漏该天） |
| `total` | 该时区 `day_summary` 行数（「共 N 天」）。分页模式 +1 次 `COUNT(*)`；全量模式未触防呆 = `len(days)`（0 额外查询）；全量模式触防呆时 +1 次 COUNT |
| 全量模式（不传 `limit`） | `LIMIT FULL_CAP_DAYS+1`（1000+1）；未超限 → 与 §1.2 的字段逐字段一致（E11）；超限 → 截断到 1000 天 + 给 `next_cursor`（老客户端忽略 → 静默截断；续翻需与 `limit` 组合，E3） |
| `has_more` 与 `days` | `days` = 本页行（date 降序）；空页（cursor 早于最早一天 / 时区无行）→ `days=[]`、`next_cursor=null`、`total` 照常 |

### 8.2.3 store 层（`store.py:261-334`）

```sql
-- 页查询（day_summaries 的 keyword-only 参数 before/limit；缺省 = 现行为，graph 等调用不受影响）
SELECT * FROM day_summary
WHERE timezone = ?  [AND local_date < ?]        -- before
ORDER BY local_date DESC  [LIMIT ?];            -- limit（调用方传 n+1 / FULL_CAP+1）
-- total（分页模式，或全量模式触防呆时）
SELECT COUNT(*) FROM day_summary WHERE timezone = ?   -- 表缺失/DB 异常 → 0（容错口径同 §6）
```

PK `(timezone, local_date)` 使 `local_date < ?` 成为索引 range scan，`ORDER BY local_date DESC`
零排序成本（`local_date` 为 `YYYY-MM-DD` 文本，字典序 = 时间序）。

### 8.2.4 客户端规则（Android；细则 `docs/mylife/timeline-android-v2.md` §5.5）

| 规则 | 内容 |
|---|---|
| 页大小随粒度 | DAY/WEEK `limit=30`、MONTH `90`、YEAR `366`（`pageSizeFor`）。**切粒度本身是纯客户端重聚合**（`PeriodAggregator` 纯函数，用已加载窗口）；仅当「已加载天数 < 该粒度页大小」**且**「`nextCursor != null`」时补取一页（`ensureWindow`，与已加载集合合并，保深度） |
| 触底加载 | 距底部 3 个 item 自动 `loadMore`；`loadingMore` 同步置位互斥；失败落 `loadMoreError`（footer「加载失败，点击重试」，新窗口到达即清）。**自动触发必须带来 item 增长**（`lastAutoLoadCount`，离开底部重新武装）——否则 `loadingMore` 的 true→false 反复重新武装触发条件，列表不增长时会一轮一页拉完历史 |
| footer 三态 | 加载中 / 已到最早（`next_cursor == null`）/ 失败重试；**drilled scope 内不出 footer 且 `onLoadMore` no-op**（范围数据在进入下钻时已全量加载，翻页只会取回被 scope 过滤掉的更早日卡） |
| 边界 group 不上屏 | 判定 = `hasMore && 窗口最旧日 > group 首日`（**日历比较**；无事件日不落行，「首日是否有行」恒不成立）。含窗口最旧日的 group 至多一个 → 无级联；DAY 卡恒闭合。未闭合的月/年卡由 footer 占位 |
| 合并语义 | 按 `date` 去重（新页优先）、追加即保持 date 降序；`next_cursor` 取更深（日期较小）的一方，任一侧 `null` 则整体 `null`。合并走 `MutableStateFlow.update{}`（CAS）——轮询在 `Dispatchers.Default`、取数在 Main，读后写会丢合并 |
| 下拉刷新 | 全局：重置回第一页（`pageSizeFor(granularity)`）；**drilled scope 内**：`cursor = <scope 末日次日>` + `limit = scope 天数` 定位重取（否则 scope 过滤后为空 → 假空态）；scope 形状非法 → 清 scope 回全局 |
| 60s 轮询 | 重取**已加载窗口**（`limit = min(已加载天数, 400)`，不带 cursor）+ 合并——只刷第一页会让窗口内更早的 pending 日永不更新（徽章常亮 + 轮询不停止）。`hasPendingAnalysis()` 只统计该窗口 |
| tz 变化 | 响应 `timezone` 与上次不同 → 丢弃旧窗口；若本次取的是更早页/定位页（`cursor != null`）则**自动回第一页**（否则最新日卡消失）；VM 同时清 `scopeKey` |

## 8.3 规则约定（E1–E12）

| # | 约定 | 说明 |
|---|---|---|
| E1 | 分页单位 = **天数 keyset**（`local_date < cursor`），页大小由客户端指定 | 已翻过区间不回溯：翻页后才落入的、位于已翻过区间的旧日不会出现（补录 / tz 回填场景），下拉刷新重置可见；未翻到的区间不受影响 |
| E2 | 不传 `limit` = 全量（分页为新客户端 opt-in）；`FULL_CAP_DAYS = 1000` 防呆 | 老客户端零行为变化（继续全量拉取）；仅超长历史（>1000 天）时被静默截断 |
| E3 | 分页判据 = `limit` 已传；`cursor` 必须与其同传 | 避免「想翻页却拿到全表」的隐含语义 |
| E4 | 响应增量 = `next_cursor` + `total`（对齐 `/timeline` 字段名/类型） | 追加式可选字段不 bump `schema_version` |
| E5 | `cursor` 严格校验 → 否则 400 `invalid cursor` | 拒绝 `/timeline` 形态的 `2026-08-12\|200`，不静默错页 |
| E6 | tz 切换：服务端不校验 cursor；客户端负责重置（回第一页 + 清 `scopeKey`） | 生效时区由服务端决定（§7.3 E3），客户端无法预知 |
| E7 | `limit ∈ [1, 400]`（越界 422） | 400 ≥ 闰年 366：YEAR 粒度一页可覆盖一个自然年且有余量（客户端实际用 366） |
| E8 | 页大小由客户端按粒度选，服务端不设默认页 | 服务端「缺省 = 全量」只有一个语义 |
| E9 | 边界 group 归客户端（§8.2.4）：最旧未闭合的不上屏、footer 占位 | 服务端保持「纯按天切片」，不感知客户端粒度 |
| E10 | 刷新/轮询语义归客户端（§8.2.4）：下拉刷新 = 重置或 scope 定位；60s 轮询 = 整窗重取 | `has_pending` 语义不变（逐日字段），只在已加载/轮询窗口内派生 |
| E11 | 全量模式零行为变化（守护测试） | 不传 `limit` 的响应与 §1.2 的字段逐字段一致（仅多出 `next_cursor`/`total`） |
| E12 | 不加 `date_from`/`date_to` | 「跳到某窗口」用 `cursor = <目标窗口末日次日>`（同一个 keyset 契约），重置后回补 scope 即用此式 |

## 8.4 落点（文件级）

| 层 | 文件 | 内容 |
|---|---|---|
| BFF | `services/bff/common/schemas.py:73-79` | `OverviewResponse` 的 `next_cursor` / `total` |
| | `services/bff/timeline/router.py:11-26` | overview 路由的 `limit`（1–400）/`cursor`（无 pattern） |
| | `services/bff/timeline/service.py:29`、`:62`、`:73-112` | `FULL_CAP_DAYS`、`_validate_cursor`、`build_overview` 分页/全量双路径 |
| | `services/bff/common/store.py:261-334` | `day_summaries(before/limit)`、`count_days` |
| Android | `MylifeApi.overview` / `OverviewDto` | `limit`/`cursor` 参数；`nextCursor`/`total` 字段 |
| | `MylifeRepository` | 窗口合并（CAS）/游标取更深/tz 回第一页/轮询整窗（≤400 天） |
| | `TimelineViewModel` | `pageSizeFor` / `ensureWindow` / `renderablePeriods` / `scopeDateRange` / `onLoadMore` |
| | `TimelineScreen` | footer 三态 + 触底 + 增长守卫 |
| 文档 | `docs/mylife/timeline-android-v2.md` §5.5 | 客户端分页细则 |
