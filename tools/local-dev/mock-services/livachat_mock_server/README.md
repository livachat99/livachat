# LivaChat Mock Server

本地开发用 WebSocket 模拟服务，供 iOS / Android 客户端联调测试。

## 快速启动

```bash
cd tools/local-dev/mock-services/livachat_mock_server
uvicorn livachat_mock_server.app:create_app --factory --host 0.0.0.0 --port 8080
```

或者从项目根目录：

```bash
make mock-server
```

## 架构

```
app.py           FastAPI 入口：REST / WebSocket 路由
config.py        服务配置：场景、超时、token 等（支持环境变量）
protocol.py      协议层：消息构建 / 解析，复用 livachat_protocol DTO
session_store.py 会话注册表：内存存储，支持 TTL 过期和会话恢复
scenarios.py     场景引擎：转写、AI 文本流、音频、打断等运行逻辑
media_fixtures.py Mock Opus 音频数据
```

## REST 接口

| Method | Path | 说明 |
|--------|------|------|
| GET | `/healthz` | 健康检查，返回 `{"ok": true}` |
| POST | `/v1/auth/token` | 返回本地测试 token |

## WebSocket 接口

```
ws://localhost:8080/v1/ws/stream?token=dev-token&device_id=<uuid>
```

### 消息流（default 场景）

1. 客户端发送 `session.start`
2. 服务端返回 `session.ready` → `status(idle)` → `status(listening)`
3. 客户端发送 `audio` 帧 → 服务端周期性返回 `transcription`（interim）
4. 连续 silence 达到阈值 → 服务端返回 `transcription(final)` → `status(thinking)` → `status(speaking)` → `ai_text` 流 → `ai_audio` 流 → `status(listening)`
5. 客户端发送 `video` → 服务端返回 `ai_video_ack`
6. 客户端发送 `ping` → 服务端返回 `pong`
7. 客户端发送 `interrupt` → 服务端取消当前流，返回 `status(listening, interrupted)`
8. 客户端发送 `session.end` → 服务端返回 `session.ended`

## 场景切换

通过环境变量或代码设置 `MOCK_SCENARIO`：

| 场景 | 行为 |
|------|------|
| `default` | 完整流程：转写 + AI 文本 + AI 音频 + 视频 ack |
| `text_only` | 只文本，不发 `ai_audio` |
| `text_on_start` | session.ready 后立即发送一轮文本（无音频，供 App runtime E2E） |
| `with_audio` | 包含 `ai_audio` 帧 |
| `slow_network` | 每条 S2C 延迟 500–2000ms |
| `server_error` | 建连后发送 error（1001） |
| `auth_failed` | 握手阶段拒绝，code 4001 |
| `idle_timeout` | 无消息后主动关闭，code 4003 |
| `ping_timeout` | 不响应 ping，超时关闭，code 4002 |
| `resume_success` | 带旧 session_id 重连返回 `session_restored: true` |
| `resume_expired` | 旧 session_id 过期返回新 session_id |
| `resume_drop` | 首连发送 ready 后关闭（1012）；重连带旧 session_id 返回 restored=true 并触发文本流 |
| `interrupt` | 正常对话可中断 |
| `video_gallery` | 收到带 `text` 的 video 帧后返回 ack + thinking + 带图片的 final `ai_text` + listening（用于 iOS 图库 E2E） |

```bash
# 启动 text_only 模式
MOCK_SCENARIO=text_only uvicorn livachat_mock_server.app:create_app --factory --host 0.0.0.0 --port 8080
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MOCK_HOST` | `0.0.0.0` | 监听地址 |
| `MOCK_PORT` | `8080` | 监听端口 |
| `MOCK_SCENARIO` | `default` | 场景 |
| `MOCK_DEV_TOKEN` | `dev-token` | 测试 token |
| `MOCK_IDLE_TIMEOUT_S` | `60` | 空闲超时秒数 |
| `MOCK_PING_TIMEOUT_S` | `35` | 心跳超时秒数 |
| `MOCK_PING_INTERVAL_S` | `15` | 心跳间隔秒数 |

## App 端接入

修改 App 的 base URL 指向本机：

- **iOS 模拟器**：`ws://localhost:8080`
- **Android 模拟器**：`ws://10.0.2.2:8080`
- **真机**：`ws://<电脑局域网 IP>:8080`

测试用 token / device_id：

- token: `dev-token`
- device_id: 任意 UUID v4，如 `00000000-0000-0000-0000-000000000001`

## 测试

```bash
cd tools/local-dev/mock-services/livachat_mock_server
pip install -e "../../../../packages/sdk-python"
pip install -e ".[test]"
pytest tests/ -v
```

测试覆盖：建连、session.start/end、ping/pong、audio→transcription、video ack、interrupt、seq 校验、contract test（S2C 消息可被 ServerMessageAdapter 校验通过）、全部 11 个 scenario。
