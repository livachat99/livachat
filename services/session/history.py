from __future__ import annotations

import json
from collections import deque
from typing import Any


class WorkingMemory:
    """Fixed-size working memory for the most recent N conversation turns."""

    def __init__(self, max_turns: int = 10) -> None:
        self._max = max_turns
        self._turns: deque[dict[str, Any]] = deque(maxlen=max_turns * 2)

    def add_turn(self, user_text: str, assistant_text: str, images: list[str] | None = None) -> None:
        self.add_message("user", user_text, images=images)
        self._turns.append({"role": "assistant", "content": assistant_text})

    def add_message(self, role: str, content: str, images: list[str] | None = None,
                    *, source: str | None = None, ref_id: str = "") -> None:
        entry: dict[str, Any] = {"role": role, "content": content}
        # Redis 恢复数据是外部输入：非 list（如字符串）会被 list() 拆成字符数组，
        # 渲染出垃圾 image_url parts 导致 provider 拒绝请求——只接受 list
        if isinstance(images, list) and images:
            entry["images"] = list(images)
        if source:
            entry["source"] = source
        if ref_id:
            entry["ref_id"] = ref_id
        self._turns.append(entry)

    def has_agent_job(self, message_id: str) -> bool:
        """job 结果去重谓词（fd_pipeline_llm_v2）。"""
        return any(
            m.get("source") == "agent_job" and m.get("ref_id") == message_id
            for m in self._turns
        )

    def get_recent(self, count: int | None = None) -> list[dict[str, Any]]:
        # 浅拷贝：外层 list 是新的，dict 条目为共享引用——原地修改条目会写回 history
        items = list(self._turns)
        if count is not None:
            items = items[-count:]
        return items

    def remove_recent_user(self, content: str) -> bool:
        """从尾部找最后一条 role=user 且 content 相同的条目删除（4xx 回滚用，幂等）。

        找不到返回 False（本轮未落记录——如 translate 轮不写、或路径 B 尚未送达）。
        """
        for i in range(len(self._turns) - 1, -1, -1):
            entry = self._turns[i]
            if entry.get("role") == "user" and entry.get("content") == content:
                del self._turns[i]
                return True
        return False

    def to_json(self) -> str:
        return json.dumps(list(self._turns), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str, max_turns: int = 10) -> WorkingMemory:
        wm = cls(max_turns=max_turns)
        items = json.loads(data)
        last_n = max_turns * 2
        for item in items[-last_n:]:
            wm._turns.append(item)
        return wm

    @property
    def is_empty(self) -> bool:
        return len(self._turns) == 0

    def __len__(self) -> int:
        return len(self._turns)
