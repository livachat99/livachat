from __future__ import annotations

from session.history import WorkingMemory


class TestWorkingMemory:
    def test_add_turn(self):
        wm = WorkingMemory(max_turns=10)
        wm.add_turn("hello", "hi there")
        assert len(wm) == 2
        assert wm.get_recent()[0]["role"] == "user"
        assert wm.get_recent()[1]["role"] == "assistant"

    def test_max_turns_evicts_oldest(self):
        wm = WorkingMemory(max_turns=2)  # 2 turns = 4 messages
        wm.add_turn("1", "a")
        wm.add_turn("2", "b")
        assert len(wm) == 4
        wm.add_turn("3", "c")
        # Oldest turn "1"/"a" should be evicted
        assert len(wm) == 4
        recent = wm.get_recent()
        assert recent[0]["content"] == "2"  # oldest turn "1"/"a" evicted; "2"/"b" remain

    def test_get_recent_count(self):
        wm = WorkingMemory(max_turns=10)
        wm.add_turn("1", "a")
        wm.add_turn("2", "b")
        wm.add_turn("3", "c")
        recent = wm.get_recent(3)  # last 3 messages
        assert len(recent) == 3
        assert recent[-1]["content"] == "c"

    def test_serialize_roundtrip(self):
        wm = WorkingMemory(max_turns=10)
        wm.add_turn("hello world", "hi there")
        data = wm.to_json()
        wm2 = WorkingMemory.from_json(data, max_turns=10)
        assert len(wm2) == 2
        assert wm2.get_recent() == wm.get_recent()

    def test_is_empty(self):
        wm = WorkingMemory()
        assert wm.is_empty
        wm.add_message("user", "hello")
        assert not wm.is_empty

    def test_add_message_with_images(self):
        wm = WorkingMemory()
        imgs = ["data:image/jpeg;base64,aaa", "data:image/jpeg;base64,bbb"]
        wm.add_message("user", "hello", images=imgs)
        entry = wm.get_recent()[-1]
        assert entry["role"] == "user"
        assert entry["content"] == "hello"
        assert entry["images"] == imgs

    def test_add_message_without_images_omits_key(self):
        wm = WorkingMemory()
        wm.add_message("user", "hello")
        assert "images" not in wm.get_recent()[-1]

    def test_add_message_copies_images_list(self):
        """Mutating the caller's list must not leak into history."""
        wm = WorkingMemory()
        imgs = ["data:image/jpeg;base64,aaa"]
        wm.add_message("user", "hello", images=imgs)
        imgs.append("data:image/jpeg;base64,bbb")
        assert wm.get_recent()[-1]["images"] == ["data:image/jpeg;base64,aaa"]

    def test_add_message_ignores_non_list_images(self):
        """Corrupt non-list images (e.g. a string from Redis) must not be stored —
        list("abc") would char-split into garbage image_url parts."""
        wm = WorkingMemory()
        wm.add_message("user", "hello", images="data:image/jpeg;base64,aaa")
        assert "images" not in wm.get_recent()[-1]

    def test_add_turn_with_images(self):
        wm = WorkingMemory()
        wm.add_turn("hello", "hi", images=["data:image/jpeg;base64,aaa"])
        entries = wm.get_recent()
        assert entries[0]["images"] == ["data:image/jpeg;base64,aaa"]
        assert "images" not in entries[1]

    def test_serialize_roundtrip_with_images(self):
        wm = WorkingMemory(max_turns=10)
        wm.add_message("user", "hello", images=["data:image/jpeg;base64,aaa"])
        wm.add_message("assistant", "hi")
        wm2 = WorkingMemory.from_json(wm.to_json(), max_turns=10)
        assert wm2.get_recent() == wm.get_recent()
        assert wm2.get_recent()[0]["images"] == ["data:image/jpeg;base64,aaa"]

    def test_old_snapshot_without_images_restores(self):
        """Legacy entries lacking the images key restore as images=None."""
        wm = WorkingMemory.from_json('[{"role": "user", "content": "hi"}]', max_turns=10)
        assert wm.get_recent()[-1].get("images") is None

    # -- agent job source/ref_id (fd_pipeline_llm_v2) --------------------

    def test_add_message_with_source_and_ref_id(self):
        wm = WorkingMemory()
        wm.add_message("assistant", "[Agent job result]\nresult", source="agent_job", ref_id="msg_agent_job_jk1")
        entry = wm.get_recent()[-1]
        assert entry["role"] == "assistant"
        assert entry["content"] == "[Agent job result]\nresult"
        assert entry["source"] == "agent_job"
        assert entry["ref_id"] == "msg_agent_job_jk1"

    def test_add_message_without_source_omits_keys(self):
        """LLM reply 不带 source/ref_id——旧消费路径零感知（向后兼容）。"""
        wm = WorkingMemory()
        wm.add_message("assistant", "plain reply")
        entry = wm.get_recent()[-1]
        assert "source" not in entry
        assert "ref_id" not in entry

    def test_serialize_roundtrip_preserves_source_ref_id(self):
        wm = WorkingMemory(max_turns=10)
        wm.add_message("assistant", "x", source="agent_job", ref_id="msg_agent_job_jk1")
        wm.add_message("assistant", "plain")
        wm2 = WorkingMemory.from_json(wm.to_json(), max_turns=10)
        entries = wm2.get_recent()
        assert entries[0]["source"] == "agent_job"
        assert entries[0]["ref_id"] == "msg_agent_job_jk1"
        assert "source" not in entries[1]
        assert wm2.has_agent_job("msg_agent_job_jk1")

    def test_has_agent_job_hit(self):
        wm = WorkingMemory()
        wm.add_message("assistant", "x", source="agent_job", ref_id="msg_agent_job_jk1")
        assert wm.has_agent_job("msg_agent_job_jk1")

    def test_has_agent_job_miss(self):
        wm = WorkingMemory()
        wm.add_message("assistant", "x", source="agent_job", ref_id="msg_agent_job_jk1")
        assert not wm.has_agent_job("msg_agent_job_other")

    def test_has_agent_job_ignores_non_agent_job_entries(self):
        """普通 assistant 条目即使带 ref_id（LLM reply 场景）也不算命中。"""
        wm = WorkingMemory()
        wm.add_message("assistant", "x", ref_id="msg_agent_job_jk1")  # 无 source
        assert not wm.has_agent_job("msg_agent_job_jk1")
        wm.add_message("assistant", "y", source="agent_job")  # 无 ref_id
        assert not wm.has_agent_job("")

    # -- remove_recent_user（fd_pipeline_llm_v2.md：4xx 回滚）------------

    def test_remove_recent_user_removes_last_match(self):
        wm = WorkingMemory()
        wm.add_message("user", "hello")
        wm.add_message("assistant", "hi")
        wm.add_message("user", "world")
        assert wm.remove_recent_user("world") is True
        assert [e["content"] for e in wm.get_recent()] == ["hello", "hi"]

    def test_remove_recent_user_matches_tail_first(self):
        """同文重复：从尾部删最近一条。"""
        wm = WorkingMemory()
        wm.add_message("user", "same")
        wm.add_message("assistant", "a")
        wm.add_message("user", "same")
        assert wm.remove_recent_user("same") is True
        assert [e["content"] for e in wm.get_recent()] == ["same", "a"]

    def test_remove_recent_user_ignores_assistant(self):
        wm = WorkingMemory()
        wm.add_message("assistant", "hello")
        assert wm.remove_recent_user("hello") is False
        assert len(wm) == 1

    def test_remove_recent_user_missing_is_idempotent(self):
        wm = WorkingMemory()
        wm.add_message("user", "hello")
        assert wm.remove_recent_user("nope") is False
        assert wm.remove_recent_user("hello") is True
        assert wm.remove_recent_user("hello") is False  # 二次删除 no-op
        assert len(wm) == 0
