from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gateway.auth import wire_digest

# Make client.py importable from the hyphenated directory
sys.path.insert(0, str(Path(__file__).parent))

E2E_PORT = 18780
E2E_SECRET = "e2e-test-token"
E2E_TOKEN = wire_digest(E2E_SECRET)  # wire token = 摘要（docs/server_auth.md §3.1）
E2E_DEVICE = "00000000-0000-0000-0000-000000000001"


def _server_env() -> dict:
    # 服务端子进程自行加载仓库根 .env（docs/ali_bailian_provider.md §9）；
    # 此处只放测试专用覆盖（真实 env 优先于 .env —— setdefault 语义）。
    env = {**os.environ}
    env.update({
        "APP_AUTH_TOKEN": E2E_SECRET,
        "DEEPGRAM_API_KEY": "x",
        "DASHSCOPE_API_KEY": "x",
        "DASHSCOPE_API_HOST": "x",
        "DASHSCOPE_WORKSPACE_ID": "x",
        "OPENROUTER_API_KEY": "x",
        "DEEPSEEK_API_KEY": "x",
        "CARTESIA_API_KEY": "x",
        "ELEVENLABS_API_KEY": "x",
        "LIVACHAT__SQLITE__CHAT_SERVER_DB_PATH": "/tmp/livachat-e2e-chat_server.db",
        "LIVACHAT__REDIS__URL": "x",
        # 测试实例关日志文件（避免与真实实例共写 logs/，docs/server_logging.md）
        "LIVACHAT__OBSERVABILITY__LOG_FILE__ENABLED": "false",
    })
    return env


@pytest.fixture(scope="session")
def e2e_server():
    # 清掉上一轮运行的 chat server db（固定 device UUID 会 restore 到旧 messages，
    # 造成跨运行污染）
    for suffix in ("", "-wal", "-shm"):
        stale = Path(f"/tmp/livachat-e2e-chat_server.db{suffix}")
        if stale.exists():
            stale.unlink()
    proc = subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(E2E_PORT), "--log-level", "warning"],
        env=_server_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        time.sleep(0.25)
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early with code {proc.returncode}")
        try:
            import urllib.request

            urllib.request.urlopen(f"http://127.0.0.1:{E2E_PORT}/healthz")
            break
        except Exception:
            pass
    else:
        proc.terminate()
        raise RuntimeError("uvicorn did not start")

    yield f"ws://127.0.0.1:{E2E_PORT}"

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def e2e_token() -> str:
    """合法 wire token（摘要值）——供"过鉴权、测其他 401 路径"的用例复用。"""
    return E2E_TOKEN


@pytest.fixture
def e2e_url(e2e_server: str) -> str:
    return f"{e2e_server}/v1/ws/stream?token={E2E_TOKEN}&device_id={E2E_DEVICE}"


from client import E2EClient


@pytest.fixture
async def e2e_client(e2e_url: str):
    async with E2EClient(e2e_url) as client:
        yield client
