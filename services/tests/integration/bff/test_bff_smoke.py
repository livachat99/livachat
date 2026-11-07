"""BFF 查询层集成冒烟（真实 lvdata 数据 + uvicorn 子进程，e2e marker）。

断言结构契约而非具体数据内容——真实数据会随录制演进；
kind 集合 = 五类可展示口径（services/bff/search/service.py SEARCH_TYPES，
docs/bff_search_fts.md）；「的」等停用词查询现按空结果短路（同文档 Q4），
用例需使用真实命中的查询词。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from gateway.auth import wire_digest

pytestmark = pytest.mark.e2e

BFF_PORT = 18781
BFF_SECRET = "bff-smoke-token"
BFF_TOKEN = wire_digest(BFF_SECRET)  # 请求侧 wire token = 摘要（docs/server_auth.md §3.1）
DATA_ROOT = Path(os.environ.get("LVCHAT_LVDATA_ROOT", "/opt/lvdata"))

SEARCH_KINDS = {"chat", "transcribe", "translate", "agent_job", "video"}
BFF_URL = f"http://127.0.0.1:{BFF_PORT}"


def _server_env() -> dict:
    # 服务端子进程自行加载仓库根 .env（docs/ali_bailian_provider.md §9）；
    # 此处只覆盖鉴权 token 保证测试 URL 一致
    env = {**os.environ}
    env["APP_AUTH_TOKEN"] = BFF_SECRET
    # 测试实例关日志文件（避免与真实实例共写 logs/，docs/server_logging.md）
    env["LIVACHAT__OBSERVABILITY__LOG_FILE__ENABLED"] = "false"
    return env


@pytest.fixture(scope="module")
def bff_server():
    if not (DATA_ROOT / "lvdata_inventory.db").exists():
        pytest.skip(f"真实 lvdata 数据不存在: {DATA_ROOT}")
    proc = subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(BFF_PORT), "--log-level", "warning"],
        env=_server_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.25)
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early with code {proc.returncode}")
        try:
            urllib.request.urlopen(f"{BFF_URL}/api/mylife/overview?token={BFF_TOKEN}")
            break
        except Exception:
            pass
    else:
        proc.terminate()
        raise RuntimeError("uvicorn did not serve /api/mylife/overview")

    yield BFF_URL

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)


def _get(bff_server: str, path: str, **params):
    url = f"{bff_server}/api/mylife/{path}?" + urllib.parse.urlencode({"token": BFF_TOKEN, **params})
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.status, json.load(r)


def test_overview_accessible(bff_server):
    status, data = _get(bff_server, "overview")
    assert status == 200
    assert data["schema_version"] == 1
    assert data["timezone"] == "Asia/Shanghai"
    assert isinstance(data["days"], list)
    # 真实数据非空（本机 /opt/lvdata 有录制）；v2：session 维度不再暴露（§9.1.1）
    assert len(data["days"]) > 0
    assert all(set(d) >= {"date", "counts"} for d in data["days"])


def test_timeline_regression(bff_server):
    status, data = _get(bff_server, "timeline", limit=10)
    assert status == 200
    assert "days" in data and "next_cursor" in data
    assert data["total"] > 0


def test_search_structure_contract(bff_server):
    status, data = _get(bff_server, "search", q="今天")
    assert status == 200
    assert data["schema_version"] == 1
    assert data["query"] == "今天"
    assert data["total"] >= 0
    for h in data["hits"]:
        # v2：session 维度不再暴露（§9.1.1），hit 只含 date/ts_ms/kind/snippet
        assert set(h) == {"date", "ts_ms", "kind", "snippet"}
        assert h["date"].count("-") == 2
        assert h["ts_ms"] is None or h["ts_ms"] > 0
        assert h["kind"] in SEARCH_KINDS
        assert isinstance(h["snippet"], str) and h["snippet"]


def test_search_empty_query_400(bff_server):
    url = f"{bff_server}/api/mylife/search?token={BFF_TOKEN}"
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(url, timeout=15)
    assert excinfo.value.code == 400
    body = json.load(excinfo.value)
    assert body == {"error": {"code": "bad_request", "message": "empty query"}}


def test_search_pagination_consistent(bff_server):
    """分页一致性。停用词（如「的」）现按空结果短路（docs/bff_search_fts.md Q4），
    需先选一个真实命中且 total>3 的查询词，否则无法构造翻页。"""
    for q in ("今天", "什么", "天气"):
        _, p1 = _get(bff_server, "search", q=q, limit=3)
        if p1["total"] > 3:
            break
    else:
        pytest.skip("真实语料中无 total>3 的查询，无法构造分页断言")
    assert p1["next_cursor"] is not None
    _, p2 = _get(bff_server, "search", q=q, limit=3, cursor=p1["next_cursor"])
    assert p2["total"] == p1["total"]
    keys1 = {(h["date"], h["ts_ms"], h["snippet"]) for h in p1["hits"]}
    keys2 = {(h["date"], h["ts_ms"], h["snippet"]) for h in p2["hits"]}
    assert not keys1 & keys2


def test_search_kinds_observed_within_contract(bff_server):
    """真实数据中实际出现的 kind 必须是契约五类之一（未来新增 kind 需先升级契约）。"""
    observed_all = set()
    for q in ("今天", "天气", "股票", "什么"):
        _, data = _get(bff_server, "search", q=q, limit=50)
        observed = {h["kind"] for h in data["hits"]}
        assert observed <= SEARCH_KINDS, f"q={q} 出现契约外 kind: {observed - SEARCH_KINDS}"
        observed_all |= observed
    assert observed_all, "所有查询词零命中——索引缺失或语料为空（冒烟前提被破坏）"


def test_search_timezone_param(bff_server):
    status, data = _get(bff_server, "search", q="今天", timezone="UTC")
    assert status == 200
    for h in data["hits"]:
        assert h["date"].count("-") == 2
