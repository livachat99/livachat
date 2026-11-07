from __future__ import annotations

from tts.sentence_splitter import SentenceSplitter


class TestSentenceSplitter:
    def test_no_boundary_buffers(self):
        sp = SentenceSplitter()
        results = list(sp.feed("Hello"))
        assert results == []
        assert sp._buf == "Hello"

    def test_period_boundary_yields(self):
        sp = SentenceSplitter()
        results = list(sp.feed("Hello. World"))
        assert results == ["Hello."]
        assert sp._buf == "World"

    def test_chinese_punctuation(self):
        sp = SentenceSplitter()
        results = list(sp.feed("你好。今天"))
        assert results == ["你好。"]
        assert sp._buf == "今天"

    def test_comma_boundary(self):
        sp = SentenceSplitter()
        results = list(sp.feed("Hi, there"))
        assert results == ["Hi,"]
        assert sp._buf == "there"

    def test_multiple_sentences(self):
        sp = SentenceSplitter()
        results = list(sp.feed("A. B. C"))
        assert results == ["A.", "B."]
        assert sp._buf == "C"

    def test_flush_residue(self):
        sp = SentenceSplitter()
        sp.feed("Hello")
        remainder = sp.flush()
        assert remainder == "Hello"
        assert sp._buf == ""

    def test_flush_empty_returns_none(self):
        sp = SentenceSplitter()
        assert sp.flush() is None

    def test_leading_whitespace_stripped(self):
        sp = SentenceSplitter()
        results = list(sp.feed("  Hello.  World"))
        assert results == ["Hello."]
