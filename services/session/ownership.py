"""Session ownership registry — 被接管的旧连接不得发布 ``session.ended``。

断线重连带 resume_id（客户端复用同一 session_id）时，旧连接可能尚未拆除：其
reader 阻塞在 ``receive_text()``（close 帧在死链上丢失），直到 OS 回收 TCP 或
服务端心跳超时（45s）才走 teardown 并发布 ``session.ended`` —— 晚于新连接的
``session.start``。agent relay 按 session_id 维护 watcher/session，迟到的 ended
会把**活跃**会话摘掉（``agent_relay/session_manager.py::on_session_ended`` 按 sid
pop；其 ``on_session_started`` 对已存在的 sid 早退，不会重建）。

登记"当前归属哪个连接"：``session.start`` 时接管，ended 发布前校验；被接管的旧
连接静默跳过 ended（新连接续用同一 sid，relay 侧会话/watcher 无缝保留）。

进程内单实例语义：跨实例部署时旧实例的登记未被接管，照常发布 ended（与既有
行为一致）。
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_owners: dict[str, Any] = {}


def take(session_id: str, owner: Any) -> None:
    """``session.start`` 时登记/接管：同 session_id 的后续连接成为唯一 owner。"""
    if not session_id:
        return
    with _lock:
        _owners[session_id] = owner


def owns(session_id: str, owner: Any) -> bool:
    """本连接是否仍是该 session 的 owner（未被更新的连接接管）。"""
    if not session_id:
        return False
    with _lock:
        return _owners.get(session_id) is owner


def release(session_id: str, owner: Any) -> None:
    """会话终结时释放；非 owner 调用是 no-op（旧连接不得误删新 owner 的登记）。"""
    if not session_id:
        return
    with _lock:
        if _owners.get(session_id) is owner:
            _owners.pop(session_id, None)
