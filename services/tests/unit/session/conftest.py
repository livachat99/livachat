"""会话单测共享 fake：chat store 同步钩子记录调用（不落盘）。"""

from __future__ import annotations


class FakeChatStore:
    """Duck-typed ChatStore：on_device/on_message/delete_message 记录（不落盘）。

    delete_message 同时按 5 元组应用删除，模拟真实表语义（fd_pipeline_llm_v2.md）。
    """

    def __init__(self):
        self.devices: list[dict] = []
        self.messages: list[dict] = []
        self.deleted_messages: list[dict] = []

    def on_device(self, **kwargs) -> None:
        self.devices.append(kwargs)

    def on_message(self, **kwargs) -> None:
        self.messages.append(kwargs)

    def delete_message(self, *, device_uuid: str, session_id: str, kind: str,
                       role: str, ts_ms: int) -> None:
        self.deleted_messages.append({
            "device_uuid": device_uuid, "session_id": session_id,
            "kind": kind, "role": role, "ts_ms": ts_ms,
        })
        self.messages = [
            m for m in self.messages
            if not (m.get("device_uuid") == device_uuid
                    and m.get("session_id") == session_id
                    and m.get("kind") == kind
                    and m.get("role") == role
                    and m.get("ts_ms") == ts_ms)
        ]
