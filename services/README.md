# LivaChat Backend Server

## Quickstart

```bash
# 1. Install server + protocol SDK
make server-install

# 2. 无外部基础设施依赖（chat server 全部持久化在 SQLite 文件库；Redis 已
#    去接线，见 docs/chatserver_no_redis.md）——无需启动任何容器

# 3. Run tests
make server-test

# 4. Start dev server
make server-dev
```

Then open `http://localhost:8080/healthz`.
