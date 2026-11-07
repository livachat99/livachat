# Server HTTPS/TLS Support

## Context

当前 server 仅支持 HTTP 明文传输（uvicorn 绑定 `0.0.0.0:8080`，无 SSL 参数）。生产环境中 TLS 由 Kubernetes Ingress 终结，但开发者直接运行 server 或非 K8s 部署时无法启用 HTTPS。需要增加 server 端原生 HTTPS 支持，通过配置开关控制。

## Design

### 配置模型 (`services/shared/config/models.py`)

新增 `ServerSslConfig`：

```python
class ServerSslConfig(BaseModel):
    enabled: bool = False
    certfile: str = ""
    keyfile: str = ""
    keyfile_password: str | None = None
    cafile: str | None = None
    cert_reqs: Literal["none", "optional", "required"] = "none"  # → ssl.CERT_*
```

`cert_reqs` 使用 `Literal` 枚举类型，由 pydantic 在解析阶段拒绝非法值，无需在 `_validate_ssl()` 中再检查合法性。

`ServerConfig` 新增字段 `ssl: ServerSslConfig = ServerSslConfig()`。

### YAML 配置 (`configs/dev/server.yaml`)

```yaml
server:
  ssl:
    enabled: false
    certfile: ""
    keyfile: ""
```

环境变量覆盖：`LIVACHAT__SERVER__SSL__ENABLED=true` 等。

### Config 加载重构 (`services/app/application.py`)

**问题**：当前 `load_config()` 在 async `lifespan()` 内调用，但 uvicorn SSL 参数必须在 `uvicorn.run()` 之前同步获取。

**方案**：`create_app(config=None)` 接受可选 `AppConfig`，传入时直接使用，否则内部调用 `load_config()`。`app.state.config` 在 `create_app()` 中同步设置。

**职责划分**：config 的单一来源是 `create_app()`。`lifespan()` 不再调用 `load_config()`，改为直接复用 `app.state.config`，避免配置加载两次及两处不一致。

### 入口 (`services/app/main.py`)

- 模块级 `cfg = load_config()`，`app = create_app(config=cfg)`
- `_build_ssl_kwargs()`：当 `ssl.enabled=True` 时构建 `ssl_certfile`/`ssl_keyfile`/`ssl_keyfile_password`/`ssl_ca_certs`/`ssl_cert_reqs` 传给 `uvicorn.run()`
- `cert_reqs` 字符串映射：`"none"` → `ssl.CERT_NONE`，`"optional"` → `ssl.CERT_OPTIONAL`，`"required"` → `ssl.CERT_REQUIRED`
- host/port 从 config 读取，消除硬编码

### 配置校验 (`services/shared/config/validation.py`)

`_validate_ssl()`：
- `enabled=true` 时 `certfile`/`keyfile` 非空
- mutual TLS (`optional`/`required`) 时 `cafile` 必填

（`cert_reqs` 合法性已由 `Literal` 类型在 pydantic 解析阶段保证，无需在此校验。）

### 测试计划

- **单元测试** `_validate_ssl()`：覆盖 `enabled=true` 缺 `certfile`/`keyfile`、mTLS (`optional`/`required`) 缺 `cafile` 等失败分支，以及合法配置通过的分支
- **单元测试** `_build_ssl_kwargs()`：验证 `cert_reqs` 字符串到 `ssl.CERT_NONE`/`CERT_OPTIONAL`/`CERT_REQUIRED` 的映射，以及 `enabled=false` 时返回空 kwargs
- **集成测试**（可选）：用临时自签证书启动 uvicorn 子进程，完成一次 `wss://` 握手

### 不变更项

- 测试 fixtures 使用 `uvicorn app.main:app` CLI 模式 + 默认 `ssl.enabled=false`，无需修改
- WebSocket WSS 由 uvicorn 在 HTTPS 模式下自动支持
- Docker Compose / K8s 部署文件不改动

## Usage

### 开发环境（HTTP，默认）

```bash
python services/app/main.py
# → ws://0.0.0.0:8080/v1/ws/stream
```

### 开发环境（HTTPS + 自签证书）

```bash
# 生成自签证书
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes \
  -subj "/CN=localhost"

# 配置环境变量
export LIVACHAT__SERVER__SSL__ENABLED=true
export LIVACHAT__SERVER__SSL__CERTFILE=$(pwd)/cert.pem
export LIVACHAT__SERVER__SSL__KEYFILE=$(pwd)/key.pem

python services/app/main.py
# → wss://0.0.0.0:8080/v1/ws/stream
```

### 生产环境（uvicorn CLI + Let's Encrypt）

```bash
uvicorn app.main:app \
  --host 0.0.0.0 --port 443 \
  --ssl-certfile /etc/letsencrypt/live/example.com/fullchain.pem \
  --ssl-keyfile /etc/letsencrypt/live/example.com/privkey.pem
```

## Files Changed

| File | Change |
|------|--------|
| `services/shared/config/models.py` | Add `ServerSslConfig`, add `ssl` field to `ServerConfig` |
| `configs/dev/server.yaml` | Add `ssl:` section |
| `services/shared/config/validation.py` | Add `_validate_ssl()` |
| `services/app/application.py` | Lift `load_config()` out of lifespan, accept optional config param |
| `services/app/main.py` | Load config at module level, pass SSL kwargs to uvicorn |
| `services/.env.example` | Add SSL env var docs |
