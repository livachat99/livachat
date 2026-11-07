from __future__ import annotations

import pytest

from tests.unit.bff.conftest import DEVICE_A, SESS_A, SESS_B, TS_B1


def test_graph_nodes_and_weights(store):
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, None, 1, 1)
    nodes = {n.id: n for n in resp.nodes}

    ev = nodes[f"evt:{SESS_A[:8]}"]
    assert ev.kind == "event"
    assert ev.weight == 6  # A 的事件数
    assert ev.first_seen_ms is not None  # P6-b：event 节点 first_seen_ms 非空

    topic = nodes["topic:旅游与天气"]
    assert topic.weight == 1  # P4：每 session 出现计 1（仅 B 08-18）
    assert topic.first_seen_ms is not None

    assert nodes["topic:股票与投资"].weight == 1
    assert nodes["topic:早间新闻"].weight == 3  # A/C/F 同 session_date 08-17
    assert nodes["emotion:happy"].weight == 1
    assert nodes["emotion:neutral"].weight == 1
    assert nodes["object:杯子"].weight == 1
    assert nodes["object:马克杯"].weight == 1  # video objects 仍入图（§9 保留字段）
    # §9（D2）：新 extract schema 无 scene/apps → 不建 scene/app 节点；place 不入图
    assert "app:google chrome" not in nodes
    assert "scene:browser" not in nodes
    assert not any(nid.startswith("place:") for nid in nodes)


def test_graph_legacy_video_payload_keeps_scene_app_nodes(store, lvdata_root):
    """§9（D2）兼容：旧 schema video payload（scene/apps）仍建 scene/app 节点
    ——读取分支保留，仅新数据不再产生。"""
    import json as _json
    import sqlite3

    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
            (f"{SESS_B}:video:legacy", DEVICE_A, SESS_B, "video",
             TS_B1 + 2000, "files", "旧帧",
             _json.dumps({"frame_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                          "scene": "browser",
                          "apps": [{"name": "Google Chrome"}]})))

    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, None, 1, 1)
    nodes = {n.id: n for n in resp.nodes}
    assert nodes["app:google chrome"].weight == 1
    assert nodes["scene:browser"].weight == 1


def _ekey(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def test_graph_edges_cooccur(store):
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, None, 1, 1)
    edge_map = {(e.source, e.target): e for e in resp.edges}

    # 实体间共现：旅游与天气 与 股票与投资 仅在 B 同现 → weight 1
    assert edge_map[_ekey("topic:旅游与天气", "topic:股票与投资")].weight == 1
    assert edge_map[_ekey("topic:旅游与天气", "emotion:sad")].weight == 1

    # event ↔ 实体 每条 session 一条边（A 的日 topic = 早间新闻）
    ev = f"evt:{SESS_A[:8]}"
    assert edge_map[_ekey(ev, "topic:早间新闻")].weight == 1


def test_graph_kind_filter(store):
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, "event,topic", None, 1, 1)
    kinds = {n.kind for n in resp.nodes}
    assert kinds == {"event", "topic"}


def test_graph_focus_ego(store):
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, "topic:旅游与天气", 1, 1)
    ids = {n.id for n in resp.nodes}
    assert "topic:旅游与天气" in ids
    assert f"evt:{SESS_B[:8]}" in ids  # 旅游在 B 的日 topic
    assert f"evt:{SESS_A[:8]}" not in ids
    assert "topic:股票与投资" in ids  # 与旅游在 B 共现 → depth 1 邻居
    assert "emotion:sad" in ids  # 与旅游在 B 同 session 共现
    # 边端点都在子图内
    for e in resp.edges:
        assert e.source in ids and e.target in ids


def test_graph_focus_event_ego_limited_to_session(store):
    """聚焦 A 的 event 节点：depth 1 只含 A 的实体，不含 B 的实体。"""
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, f"evt:{SESS_A[:8]}", 1, 1)
    ids = {n.id for n in resp.nodes}
    assert f"evt:{SESS_B[:8]}" not in ids
    assert "topic:股票与投资" not in ids  # B 独有 topic
    assert "emotion:neutral" in ids  # A 的 emotion


def test_graph_focus_missing_falls_back_to_full(store):
    """focus 被 types/min_weight 过滤掉时回退全图，不返回空图。"""
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, "topic:不存在的", 1, 1)
    assert len(resp.nodes) > 0

    resp2 = build_graph(store, None, None, None, "event", "topic:旅游与天气", 1, 1)
    assert {n.kind for n in resp2.nodes} == {"event"}


def test_graph_min_weight(store):
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, None, 1, 8)
    ids = {n.id for n in resp.nodes}
    assert ids == set()  # v2 事件级权重均 < 8


def test_graph_date_range(store):
    from bff.graph.service import build_graph

    # 仅 08-17 本地日 → SESS_C + SESS_F 事件
    resp = build_graph(store, None, "2026-08-17", "2026-08-17", None, None, 1, 1)
    assert resp.stats.sessions == 2
    assert resp.stats.date_range == ["2026-08-17"]


def test_entity_detail(store):
    from bff.graph.service import build_entity

    resp = build_entity(store, "topic:旅游与天气")
    assert resp.kind == "topic"
    assert resp.label == "旅游与天气"
    assert resp.weight == 1  # P4：每 session 出现计 1（仅 B）
    dates = {a.session_date for a in resp.appearances}
    assert dates == {"2026-08-18"}
    assert len(resp.appearances) == 1

    # appearances 时间倒序
    dates_sorted = [a.session_date for a in resp.appearances]
    assert dates_sorted == sorted(dates_sorted, reverse=True)


def test_entity_event_detail(store):
    """event 实体（evt:{sid前8}）详情：weight = 对应 session 事件数，appearances = 1。"""
    from bff.graph.service import build_entity

    resp = build_entity(store, f"evt:{SESS_A[:8]}")
    assert resp.kind == "event"
    assert resp.weight == 6  # A 的事件数
    assert len(resp.appearances) == 1
    assert resp.appearances[0].session_date == "2026-08-17"

    # 其他 session 的 event 前缀不匹配 → weight 0（不在该 session present）
    resp_b = build_entity(store, f"evt:{SESS_A[:8]}")
    assert resp_b.weight == 6


def test_api_entity_event_detail(client):
    resp = client.get(f"/api/mylife/graph/entity/evt:{SESS_A[:8]}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "event"
    assert body["weight"] == 6


def test_entity_not_found(store):
    from bff.common.store import BffError
    from bff.graph.service import build_entity

    with pytest.raises(BffError) as ei:
        build_entity(store, "nonsense")
    assert ei.value.code == "not_found"


def test_api_graph(client):
    resp = client.get("/api/mylife/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == 1
    assert any(n["kind"] == "event" for n in body["nodes"])
    assert body["stats"]["sessions"] == 4  # A/B/C/F 有事件
    # §9.1.1：session 维度不再暴露
    assert all("session_id" not in n for n in body["nodes"])


def test_api_graph_focus(client):
    resp = client.get("/api/mylife/graph?focus=topic:%E6%97%85%E6%B8%B8%E4%B8%8E%E5%A4%A9%E6%B0%94&depth=1")
    assert resp.status_code == 200
    assert len(resp.json()["nodes"]) >= 3


def test_api_entity_detail(client):
    resp = client.get("/api/mylife/graph/entity/topic:%E6%97%85%E6%B8%B8%E4%B8%8E%E5%A4%A9%E6%B0%94")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "topic:旅游与天气"
    assert body["weight"] == 1  # P4：每 session 出现计 1


def test_api_graph_bad_kind(client):
    resp = client.get("/api/mylife/graph?types=bogus")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


# -- person（§5.2） ------------------------------------------------------------


def test_graph_person_buckets(store):
    """role/gender 归桶：user/female/male 三桶；gender-null other（AI 助手）跳过。"""
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, None, 1, 1)
    nodes = {n.id: n for n in resp.nodes}

    user = nodes["person:user"]
    assert user.kind == "person"
    assert user.label == "我"
    assert nodes["person:female"].label == "女性联系人"
    assert nodes["person:male"].label == "男性联系人"
    assert "person:other" not in nodes
    assert "person:AI助手" not in nodes


def test_graph_person_weight_per_session(store):
    """★ per-session 每桶 +1：A 同桶 2 条只计 1，跨 session（B）再 +1 → user=2。"""
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, None, 1, 1)
    weights = {n.id: n.weight for n in resp.nodes}
    assert weights["person:user"] == 2  # A:1（2 条去重）+ B:1
    assert weights["person:female"] == 1
    assert weights["person:male"] == 1


def test_graph_person_kind_filter(store):
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, "person", None, 1, 1)
    kinds = {n.kind for n in resp.nodes}
    assert kinds == {"person"}


def test_graph_person_edges(store):
    """person 参与建边（event ↔ 实体、实体 ↔ 实体），零特判。"""
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, None, 1, 1)
    edge_map = {(e.source, e.target): e for e in resp.edges}
    assert edge_map[_ekey(f"evt:{SESS_A[:8]}", "person:user")].weight == 1
    # topic 与 person:user 仅 B 共现 → weight 1
    assert edge_map[_ekey("topic:旅游与天气", "person:user")].weight == 1
    assert edge_map[_ekey("person:user", "person:female")].weight == 1


def test_entity_person_detail(store):
    """person entity 详情：presence 1/0 语义，appearances 正确。"""
    from bff.graph.service import build_entity

    resp = build_entity(store, "person:user")
    assert resp.kind == "person"
    assert resp.label == "我"
    assert resp.weight == 2  # A + B
    dates = {a.session_date for a in resp.appearances}
    assert dates == {"2026-08-17", "2026-08-18"}

    resp_female = build_entity(store, "person:female")
    assert resp_female.weight == 1
    assert {a.session_date for a in resp_female.appearances} == {"2026-08-17"}


def test_api_graph_types_person(client):
    resp = client.get("/api/mylife/graph?types=person")
    assert resp.status_code == 200
    body = resp.json()
    kinds = {n["kind"] for n in body["nodes"]}
    assert kinds == {"person"}
    assert any(n["label"] == "我" for n in body["nodes"])


def test_api_entity_person_detail(client):
    resp = client.get("/api/mylife/graph/entity/person:user")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "person"
    assert body["weight"] == 2
    assert len(body["appearances"]) == 2


# -- v3 契约条款（§5.6/§5.7）---------------------------------------------------


def test_graph_appearance_dedup_same_date(store):
    """appearances 按 session_date 去重：同日多 session 合并一条（Android date-only 键唯一性保证）。"""
    from bff.graph.service import build_entity

    resp = build_entity(store, "topic:早间新闻")
    # A/C/F 均属 08-17 → weight 3（每 session 计 1），appearances 仅 1 条
    assert resp.weight == 3
    assert [a.session_date for a in resp.appearances] == ["2026-08-17"]
    assert len(resp.appearances) == 1


def test_graph_topic_weight_min_weight(client):
    """P4 钉死：topic 节点 weight ≥ 1（每 session 计 1），不被 min_weight=1 裁掉。"""
    resp = client.get("/api/mylife/graph?types=topic&min_weight=1")
    body = resp.json()
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["topic:旅游与天气"]["weight"] == 1
    assert nodes["topic:早间新闻"]["weight"] == 3
    assert all(n["weight"] >= 1 for n in body["nodes"])


def test_graph_event_node_first_seen_ms_nonnull(store):
    """P6-b 契约条款：event 节点 first_seen_ms 非空（Android 消费处用 ?.let 守卫）。"""
    from bff.graph.service import build_graph

    resp = build_graph(store, None, None, None, None, None, 1, 1)
    events = [n for n in resp.nodes if n.kind == "event"]
    assert len(events) == 4  # A/B/C/F
    assert all(n.first_seen_ms is not None for n in events)
