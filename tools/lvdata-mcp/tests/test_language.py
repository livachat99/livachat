"""分析产物目标语言 prompt 单测（docs/lvdata_multilingual.md）。"""

from __future__ import annotations

import pytest
from lvdata_mcp.analysis.audio import _audio_system_prompt
from lvdata_mcp.analysis.vision import _extract_system_prompt, _filter_system_prompt
from lvdata_mcp.config import SUPPORTED_LANGUAGES
from lvdata_mcp.language import LANGUAGE_NAMES, output_language_rule
from lvdata_mcp.summary.day import _day_system_prompt


def test_language_names_match_supported_codes():
    assert set(LANGUAGE_NAMES) == set(SUPPORTED_LANGUAGES)
    assert "tr" not in LANGUAGE_NAMES


@pytest.mark.parametrize("code", SUPPORTED_LANGUAGES)
def test_output_language_rule(code):
    assert output_language_rule(code) == f"{LANGUAGE_NAMES[code]} ({code})"


def test_output_language_rule_unknown_falls_back_to_en():
    assert output_language_rule("tr") == "English (en)"


@pytest.mark.parametrize("code", ["en", "ja", "ar", "th"])
def test_audio_prompt_targets_generated_fields_and_keeps_raw(code):
    prompt = _audio_system_prompt(code)
    assert output_language_rule(code) in prompt
    assert "transcript 保留原语言" in prompt
    assert "language 字段填检测到的源语言代码" in prompt
    assert "value/emotions[].emotion" in prompt


@pytest.mark.parametrize("code", ["en", "ja", "ar", "th"])
def test_vision_prompts_target_generated_fields_and_keep_ocr(code):
    target = output_language_rule(code)
    filt = _filter_system_prompt(code)
    extract = _extract_system_prompt(code)
    assert target in filt and "window_caption" in filt and "reason" in filt
    assert target in extract and "caption" in extract and "ocr[].location" in extract
    assert "ocr[].text 保留画面原文" in extract


@pytest.mark.parametrize("code", ["en", "ja", "ar", "th"])
def test_day_prompt_targets_summary_and_keeps_search_words(code):
    prompt = _day_system_prompt(code)
    assert output_language_rule(code) in prompt
    assert "summary、events、topics[].topic" in prompt
    assert "topics[].search_words 必须保持事件摘要原文连续片段" in prompt
