"""分析产物目标语言（docs/lvdata_multilingual.md）。"""

from __future__ import annotations

from .config import SUPPORTED_LANGUAGES

LANGUAGE_NAMES = {
    "en": "English",
    "zh-Hans": "Simplified Chinese",
    "zh-Hant": "Traditional Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "pt": "Portuguese",
    "hi": "Hindi",
    "id": "Indonesian",
    "th": "Thai",
    "vi": "Vietnamese",
    "ar": "Arabic",
}


def output_language_rule(language: str) -> str:
    """返回 `Language Name (code)`；非法值防御性回落 en。"""
    lang = language if language in SUPPORTED_LANGUAGES else "en"
    return f"{LANGUAGE_NAMES[lang]} ({lang})"
