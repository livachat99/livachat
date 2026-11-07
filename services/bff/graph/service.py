from __future__ import annotations

from datetime import datetime

from bff.common.deps import resolve_request_tz
from bff.common.schemas import (
    EntityAppearance,
    EntityResponse,
    GraphEdge,
    GraphNode,
    GraphResponse,
    GraphStats,
)
from bff.common.store import LvdataStore
from bff.common.topics import topic_names
from shared.timeutil import resolve_tzinfo

ENTITY_KINDS = ("event", "topic", "emotion", "object", "app", "scene", "person")

# person 归桶（§5.2）：audio people[] 的 role/gender 结构化归桶，确定性；
# gender-null 的 other 多为 AI 助手，跳过不建桶。label 供图中展示。
_PERSON_BUCKETS = {
    ("user", None): ("user", "我"),
    ("other", "female"): ("female", "女性联系人"),
    ("other", "male"): ("male", "男性联系人"),
}

_PERSON_LABELS = {"user": "我", "female": "女性联系人", "male": "男性联系人"}


def _person_bucket(role: str | None, gender: str | None) -> tuple[str, str] | None:
    if not role:
        return None
    for (r, g), bucket in _PERSON_BUCKETS.items():
        if r == role and (g is None or g == gender):
            return bucket
    return None


def _person_label(bucket_id: str) -> str:
    return _PERSON_LABELS.get(bucket_id, bucket_id)


def _local_date(ts_ms: int, tz: str) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, resolve_tzinfo(tz)).strftime("%Y-%m-%d")


class _Node:
    __slots__ = ("first", "id", "kind", "label", "last", "sessions", "weight")

    def __init__(self, nid: str, kind: str, label: str):
        self.id = nid
        self.kind = kind
        self.label = label
        self.weight = 0
        self.first: int | None = None
        self.last: int | None = None
        self.sessions: set[str] = set()


class _Edge:
    __slots__ = ("sessions", "source", "target", "weight")

    def __init__(self, source: str, target: str):
        self.source = source
        self.target = target
        self.weight = 0
        self.sessions: set[str] = set()


def build_graph(
    store: LvdataStore,
    device_id: str | None,
    date_from: str | None,
    date_to: str | None,
    types: str | None,
    focus: str | None,
    depth: int,
    min_weight: int,
    default_tz: str = "Asia/Shanghai",
) -> GraphResponse:
    tz = resolve_request_tz(store, None, default_tz)
    kind_set = set(types.split(",")) if types else set(ENTITY_KINDS)
    invalid = kind_set - set(ENTITY_KINDS)
    if invalid:
        from bff.common.store import BffError

        raise BffError("bad_request", f"unknown entity kind(s): {','.join(sorted(invalid))}")

    nodes: dict[str, _Node] = {}
    edges: dict[tuple[str, str], _Edge] = {}
    sessions_in_range: list[dict] = []
    date_range: list[str] = []

    for s in store.list_sessions(device_id):
        sid = s["session_id"]
        events = store.timeline_events(sid)
        if not events:
            continue
        first_ts = events[0]["ts_ms"]
        last_ts = events[-1]["ts_ms"]
        d = _local_date(first_ts, tz)
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        sessions_in_range.append(s)
        date_range.append(d)

        _accumulate(store, nodes, edges, s, events, first_ts, last_ts, tz)

    nodes = {nid: n for nid, n in nodes.items() if n.kind in kind_set}
    edges = {k: e for k, e in edges.items() if e.source in nodes and e.target in nodes}

    if min_weight > 1:
        nodes = {nid: n for nid, n in nodes.items() if n.weight >= min_weight}
        edges = {k: e for k, e in edges.items() if e.weight >= min_weight and e.source in nodes and e.target in nodes}

    if focus and focus in nodes:
        # focus 已被 types/min_weight 裁剪时忽略（回退全图），不返回空图
        nodes, edges = _ego(nodes, edges, focus, depth)

    dates = sorted(set(date_range))
    return GraphResponse(
        nodes=[_to_node(n) for n in nodes.values()],
        edges=[_to_edge(e) for e in edges.values()],
        stats=GraphStats(
            sessions=len(sessions_in_range),
            date_range=[dates[0]] if len(dates) == 1 else [dates[0], dates[-1]] if dates else [],
        ),
    )


def _accumulate(
    store: LvdataStore,
    nodes: dict[str, _Node],
    edges: dict[tuple[str, str], _Edge],
    session: dict,
    events: list[dict],
    first_ts: int,
    last_ts: int,
    tz: str,
) -> None:
    sid = session["rsession_id"]
    # v2 实体来源：events payload（audio/video）+ day_summary topics（§9.1）
    topics = _session_topics(store, session, tz)

    # event 节点：label = 日期 + 首要 topic
    first_topic = topics[0] if topics else ""
    label = f"{_local_date(first_ts, tz)} · {first_topic}".strip(" ·")
    ev_node = _upsert_node(nodes, f"evt:{sid[:8]}", "event", label)
    ev_node.weight += len(events)
    _touch(ev_node, first_ts, last_ts, sid)

    entities: list[tuple[str, str]] = []

    for name in topics:
        nid = f"topic:{name}"
        node = _upsert_node(nodes, nid, "topic", name)
        # P4：mentions 数据源已随 session_summary 消亡 → 每 session 出现计 1（与其他实体同语义）
        node.weight += 1
        _touch(node, first_ts, last_ts, sid)
        entities.append((nid, "topic"))

    seen_obj: set[str] = set()
    seen_scene: set[str] = set()
    seen_person: set[str] = set()
    for e in events:
        etype = e["type"]
        if etype == "audio":
            for emo in e.get("emotions") or []:
                name = emo.get("emotion") if isinstance(emo, dict) else None
                if not name:
                    continue
                nid = f"emotion:{name}"
                node = _upsert_node(nodes, nid, "emotion", name)
                node.weight += 1
                _touch(node, first_ts, last_ts, sid)
                entities.append((nid, "emotion"))
            for p in e.get("people") or []:
                if not isinstance(p, dict):
                    continue
                bucket = _person_bucket(p.get("role"), p.get("gender"))
                if not bucket:
                    continue
                bucket_id, plabel = bucket
                if bucket_id in seen_person:
                    continue
                seen_person.add(bucket_id)
                nid = f"person:{bucket_id}"
                node = _upsert_node(nodes, nid, "person", plabel)
                node.weight += 1
                _touch(node, first_ts, last_ts, sid)
                entities.append((nid, "person"))
        elif etype == "video":
            scene = e.get("scene")
            if scene and scene not in seen_scene:
                seen_scene.add(scene)
                nid = f"scene:{scene}"
                node = _upsert_node(nodes, nid, "scene", scene)
                node.weight += 1
                _touch(node, first_ts, last_ts, sid)
                entities.append((nid, "scene"))
            for app in e.get("apps") or []:
                name = app.get("name") if isinstance(app, dict) else None
                if not name:
                    continue
                nid = f"app:{name.lower()}"
                node = _upsert_node(nodes, nid, "app", name)
                node.weight += 1
                _touch(node, first_ts, last_ts, sid)
                entities.append((nid, "app"))
        for obj in e.get("objects") or []:
            name = obj.get("name") if isinstance(obj, dict) else None
            if not name or name in seen_obj:
                continue
            seen_obj.add(name)
            nid = f"object:{name}"
            node = _upsert_node(nodes, nid, "object", name)
            node.weight += 1
            _touch(node, first_ts, last_ts, sid)
            entities.append((nid, "object"))

    # 建边：event ↔ 实体（weight=1）；实体 ↔ 实体（weight=共现 session 数）
    for nid, _kind in entities:
        _bump_edge(edges, ev_node.id, nid, sid)
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            _bump_edge(edges, entities[i][0], entities[j][0], sid)


def _session_topics(store: LvdataStore, session: dict, tz: str) -> list[str]:
    """session 本地日的 day_summary topics（v2；§13 归一化为主题名列表）。"""
    date = session.get("session_date")
    if not date:
        return []
    rows = store.day_summaries(tz, date, date)
    if not rows or rows[0]["status"] != "ok":
        return []
    return topic_names((rows[0].get("doc") or {}).get("topics"))

def _upsert_node(nodes: dict[str, _Node], nid: str, kind: str, label: str) -> _Node:
    node = nodes.get(nid)
    if node is None:
        node = _Node(nid, kind, label)
        nodes[nid] = node
    return node


def _touch(node: _Node, first_ts: int, last_ts: int, sid: str) -> None:
    node.sessions.add(sid)
    node.first = first_ts if node.first is None else min(node.first, first_ts)
    node.last = last_ts if node.last is None else max(node.last, last_ts)


def _bump_edge(edges: dict[tuple[str, str], _Edge], a: str, b: str, sid: str) -> None:
    key = (a, b) if a < b else (b, a)
    edge = edges.get(key)
    if edge is None:
        edge = _Edge(key[0], key[1])
        edges[key] = edge
    if sid not in edge.sessions:
        edge.sessions.add(sid)
        edge.weight += 1


def _ego(
    nodes: dict[str, _Node],
    edges: dict[tuple[str, str], _Edge],
    focus: str,
    depth: int,
) -> tuple[dict[str, _Node], dict[tuple[str, str], _Edge]]:
    if focus not in nodes:
        return {}, {}
    keep: set[str] = {focus}
    frontier = {focus}
    for _ in range(max(1, depth)):
        next_frontier: set[str] = set()
        for a, b in edges:
            if a in frontier and b not in keep:
                keep.add(b)
                next_frontier.add(b)
            elif b in frontier and a not in keep:
                keep.add(a)
                next_frontier.add(a)
        if not next_frontier:
            break
        frontier = next_frontier
    return (
        {nid: n for nid, n in nodes.items() if nid in keep},
        {k: e for k, e in edges.items() if e.source in keep and e.target in keep},
    )


def _to_node(n: _Node) -> GraphNode:
    return GraphNode(
        id=n.id,
        kind=n.kind,
        label=n.label,
        weight=n.weight,
        first_seen_ms=n.first,
        last_seen_ms=n.last,
    )


def _to_edge(e: _Edge) -> GraphEdge:
    return GraphEdge(source=e.source, target=e.target, weight=e.weight)


# -- entity detail -------------------------------------------------------------


def build_entity(store: LvdataStore, entity_id: str, device_id: str | None = None,
                 default_tz: str = "Asia/Shanghai") -> EntityResponse:
    if ":" not in entity_id:
        from bff.common.store import BffError

        raise BffError("not_found", f"entity not found: {entity_id}")
    kind, label = entity_id.split(":", 1)
    if kind == "evt":
        # 图节点 id 前缀为 evt:{sid前8}（build_graph event 节点）；实体详情兼容映射到 event
        kind = "event"
    if kind not in ENTITY_KINDS:
        from bff.common.store import BffError

        raise BffError("bad_request", f"unknown entity kind: {kind}")
    # person 图节点 label 为桶展示名（"我"/"女性联系人"/…），entity id 携带桶 id（person:user）；
    # _entity_in_session 需用桶 id 匹配，展示名仅用于响应输出
    display_label = _person_label(label) if kind == "person" else label
    # topic 数据源是 day_summary（存于服务配置时区），与 build_graph 同口径；
    # session.timezone 是设备写的（可能 ≠ 配置时区），用它查会恒落空。
    tz = resolve_request_tz(store, None, default_tz)

    # 收集出现该实体的 session（device_id 过滤 + 白名单；全量扫描，规模小可接受）；
    # appearances 按 session_date 去重（同日多 session 合并一条，保最新——list_sessions 按
    # started_at 倒序，首个遇到的同日 session 即最新），Android date-only 键唯一性的服务端保证
    appearances: list[EntityAppearance] = []
    seen_dates: set[str] = set()
    weight = 0
    first: int | None = None
    last: int | None = None

    for s in store.list_sessions(device_id):
        sid = s["session_id"]
        events = store.timeline_events(sid)
        if not events:
            continue
        present = _entity_in_session(store, sid, kind, label, tz)
        if not present:
            continue
        session = store.get_session(sid) or {}
        fts = events[0]["ts_ms"]
        lts = events[-1]["ts_ms"]
        first = fts if first is None else min(first, fts)
        last = lts if last is None else max(last, lts)
        weight += present
        date = session.get("session_date") or _local_date(fts, default_tz)
        if date not in seen_dates:
            seen_dates.add(date)
            appearances.append(EntityAppearance(session_date=date, snippet=None))

    appearances.sort(key=lambda a: a.session_date, reverse=True)
    return EntityResponse(
        id=entity_id,
        kind=kind,
        label=display_label,
        weight=weight,
        first_seen_ms=first,
        last_seen_ms=last,
        appearances=appearances[:50],
    )


def _entity_in_session(store: LvdataStore, sid: str, kind: str, label: str,
                       tz: str) -> int:
    """Return node weight contribution of this entity in this session, or 0 if absent."""
    if kind == "event":
        # event 实体（evt:{sid前8}）：仅匹配对应 session，weight = 该 session 事件数
        events = store.timeline_events(sid)
        if not events or label != sid[:8]:
            return 0
        return len(events)
    events = store.timeline_events(sid) or []
    if not events:
        return 0
    session = store.get_session(sid) or {}

    if kind == "topic" and label in _session_topics(store, session, tz):
        return 1  # P4：每 session 出现计 1（mentions 数据源已消亡）
    if kind == "person":
        seen: set[str] = set()
        for e in events:
            for p in e.get("people") or []:
                if isinstance(p, dict):
                    bucket = _person_bucket(p.get("role"), p.get("gender"))
                    if bucket and bucket[0] == label:
                        seen.add(label)
        return len(seen)
    names: set[str] = set()
    if kind == "object":
        for e in events:
            for o in e.get("objects") or []:
                if isinstance(o, dict) and o.get("name"):
                    names.add(o["name"])
        return 1 if label in names else 0
    if kind == "app":
        for e in events:
            for a in e.get("apps") or []:
                if isinstance(a, dict) and a.get("name") and a["name"].lower() == label.lower():
                    return 1
        return 0
    if kind == "scene":
        for e in events:
            if e.get("scene") == label:
                return 1
        return 0
    if kind == "emotion":
        n = 0
        for e in events:
            for emo in e.get("emotions") or []:
                if isinstance(emo, dict) and emo.get("emotion") == label:
                    n += 1
        return n
    return 0
