from __future__ import annotations

from session.orchestrator import _TTS_MAX_TEXT_UNITS, _clip_for_tts, _text_units


class TestTextUnits:
    def test_cjk_char_counts_one(self):
        assert _text_units("你好世界") == 4

    def test_cjk_extension_plane_char_counts_one(self):
        # 扩展 B（U+20000）+ 扩展 G/H（U+30000–U+323AF）：全平面区间覆盖
        assert _text_units("\U00020000") == 1
        assert _text_units("\U0002a6df\U00030000\U000323af") == 3

    def test_english_word_counts_one(self):
        assert _text_units("hello world") == 2

    def test_punctuation_and_space_count_zero(self):
        assert _text_units("你好, world! 今天 天气不错。") == 2 + 1 + 2 + 4

    def test_hyphen_apostrophe_words_count_once(self):
        assert _text_units("don't stop the well-being") == 4

    def test_digits_count_as_words(self):
        assert _text_units("abc123 def456") == 2


class TestClipForTts:
    def test_short_text_passthrough(self):
        text = "你好，今天天气不错。Hello, sunny day."
        assert _clip_for_tts(text) is text

    def test_exactly_limit_cjk_passthrough(self):
        text = "长" * _TTS_MAX_TEXT_UNITS
        assert _clip_for_tts(text) == text

    def test_zero_unit_tail_over_limit_chars_passthrough(self):
        # 标点/空白计 0 unit：99 字 + "!" 共 99 units，未超限则整段保留
        text = "长" * 99 + "!"
        assert _text_units(text) == 99
        assert _clip_for_tts(text) == text

    def test_long_cjk_cut_at_last_punctuation(self):
        text = "第一句话。第二句，继续延长。第三句被截掉了。"
        out = _clip_for_tts(text, limit=11)
        assert out == "第一句话。第二句，继续延长。"

    def test_cut_prefers_last_sentence_final_in_budget(self):
        text = "第一句。第二句。第三句尾巴被截掉。"
        out = _clip_for_tts(text, limit=9)
        assert out == "第一句。第二句。"

    def test_sentence_final_beats_mid_punctuation(self):
        # 句末标点存在时优先收在完整句，即使句中逗号更靠后——避免播报断在半句
        text = "结论。继续，继续，继续。"
        out = _clip_for_tts(text, limit=5)
        assert out == "结论。"

    def test_mid_punctuation_fallback_without_sentence_final(self):
        # 长句只有逗号没有句号：退到最后一个句中停顿处截断
        text = "结论继续，继续，继续，继续"
        out = _clip_for_tts(text, limit=5)
        assert out == "结论继续，"

    def test_punctuation_beyond_budget_not_kept(self):
        # 第 12 个字才出现标点，已超预算 → 在第 11 个 content token 处按词边界截断
        text = "一二三四五六七八九十十一，后面内容被忽略。"
        out = _clip_for_tts(text, limit=10)
        assert out == "一二三四五六七八九十"

    def test_boundary_punctuation_at_limit_kept(self):
        text = "一" * 100 + "。后面全被忽略。"
        out = _clip_for_tts(text)
        assert out == "一" * 100 + "。"

    def test_cjk_hard_cut_without_punctuation(self):
        text = "没" * 200
        out = _clip_for_tts(text)
        assert len(out) == _TTS_MAX_TEXT_UNITS
        assert out == "没" * _TTS_MAX_TEXT_UNITS

    def test_latin_cut_at_last_punctuation(self):
        text = "Alpha bravo! charlie delta echo."
        out = _clip_for_tts(text, limit=3)
        assert out == "Alpha bravo!"

    def test_latin_hard_cut_keeps_words_whole(self):
        text = "one two three four five"
        out = _clip_for_tts(text, limit=3)
        assert out == "one two three"

    def test_latin_hard_cut_strips_trailing_space(self):
        text = "one two three    four"
        out = _clip_for_tts(text, limit=3)
        assert out == "one two three"

    def test_mixed_cjk_and_english_count_separately(self):
        # 6 个 CJK 字 + Today/is/sunny/and/warm 5 词 = 11 units；limit 9 只留首句
        text = "今天天气很好。Today is sunny and warm."
        assert _text_units(text) == 11
        out = _clip_for_tts(text, limit=9)
        assert out == "今天天气很好。"

    def test_mixed_english_words_share_cjk_budget(self):
        # 6 CJK 字 + one..four 4 词 + five/six 到 12 units 后，第七个词 seven 超限被截
        text = "今天天气不错。one two three four. five six seven"
        out = _clip_for_tts(text, limit=12)
        assert out == "今天天气不错。one two three four."
