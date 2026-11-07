from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest

from gateway.auth import wire_digest
from shared.observability.logging import setup_logging

setup_logging(json_logs=False, log_level="WARN")

SERVER_PORT = 18765


def _env_for_server(dev_token: str) -> dict:
    return {
        **os.environ,
        "APP_AUTH_TOKEN": dev_token,
        "DEEPGRAM_API_KEY": "x",
        "DASHSCOPE_API_KEY": "x",
        "DASHSCOPE_API_HOST": "x",
        "DASHSCOPE_WORKSPACE_ID": "x",
        "OPENROUTER_API_KEY": "x",
        "DEEPSEEK_API_KEY": "x",
        "CARTESIA_API_KEY": "x",
        "LIVACHAT__SQLITE__CHAT_SERVER_DB_PATH": "/tmp/livachat-gw-chat_server.db",
        "LIVACHAT__REDIS__URL": "x",
        # 测试实例关日志文件（避免与真实实例共写 logs/，docs/server_logging.md）
        "LIVACHAT__OBSERVABILITY__LOG_FILE__ENABLED": "false",
        "ELEVENLABS_API_KEY": "x",
        "OPENAI_API_KEY": "x",
    }


@pytest.fixture(scope="session")
def dev_token() -> str:
    return "gw-test-token"


@pytest.fixture(scope="session")
def server(dev_token: str):
    """Start uvicorn as a subprocess for the test session."""
    # 清掉上一轮运行的 chat server db（固定 device UUID 会 restore 到旧 messages，
    # 造成跨运行污染）
    from pathlib import Path

    for suffix in ("", "-wal", "-shm"):
        stale = Path(f"/tmp/livachat-gw-chat_server.db{suffix}")
        if stale.exists():
            stale.unlink()
    env = _env_for_server(dev_token)
    proc = subprocess.Popen(
        [
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(SERVER_PORT),
            "--log-level",
            "warning",
            # 有界优雅关闭：gateway 感知客户端关闭有 ~35s 延迟（TCP 半开），
            # 无超时会让 uvicorn 无限等待连接清空（shutdown teardown 卡死）。
            "--timeout-graceful-shutdown",
            "3",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for server to be ready
    for _ in range(20):
        time.sleep(0.25)
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early with code {proc.returncode}")
        try:
            import urllib.request

            urllib.request.urlopen(f"http://127.0.0.1:{SERVER_PORT}/healthz")
            break
        except Exception:
            pass
    else:
        proc.terminate()
        raise RuntimeError("uvicorn did not start in time")

    yield f"ws://127.0.0.1:{SERVER_PORT}"

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)


@pytest.fixture
def ws_url(server: str, dev_token: str) -> str:
    # dev_token fixture 是明文 secret；线上 token 必须是摘要（docs/server_auth.md §3.1）
    return (
        f"{server}/v1/ws/stream?token={wire_digest(dev_token)}"
        "&device_id=00000000-0000-0000-0000-000000000001"
    )
