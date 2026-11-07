"""主动对话文本的 17 语言静态表与目标语言解析。

docs/server_multilingual.md §4.1/§4.4/附录 A/B。
"""

from __future__ import annotations

SOURCE_LANGUAGE = "zh-Hans"
DEFAULT_LANGUAGE = "en"

SUPPORTED_PROACTIVE_LANGUAGES = frozenset({
    "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de", "fr",
    "it", "es", "pt", "tr", "hi", "id", "th", "vi", "ar",
})

CAMERA_LABEL: dict[str, str] = {
    "en": "[Camera view]",
    "zh-Hans": "[相机画面]",
    "zh-Hant": "[相機畫面]",
    "ja": "[カメラ映像]",
    "ko": "[카메라 화면]",
    "ru": "[Изображение с камеры]",
    "de": "[Kamerabild]",
    "fr": "[Vue caméra]",
    "it": "[Vista fotocamera]",
    "es": "[Vista de la cámara]",
    "pt": "[Imagem da câmera]",
    "tr": "[Kamera görüntüsü]",
    "hi": "[कैमरा दृश्य]",
    "id": "[Tampilan kamera]",
    "th": "[ภาพจากกล้อง]",
    "vi": "[Hình ảnh camera]",
    "ar": "[عرض الكاميرا]",
}

ENV_PREFIX: dict[str, str] = {
    "en": "[Environment event] ",
    "zh-Hans": "[环境事件]",
    "zh-Hant": "[環境事件]",
    "ja": "[環境イベント]",
    "ko": "[환경 이벤트]",
    "ru": "[Событие окружения] ",
    "de": "[Umgebungsereignis] ",
    "fr": "[Événement ambiant] ",
    "it": "[Evento ambientale] ",
    "es": "[Evento del entorno] ",
    "pt": "[Evento do ambiente] ",
    "tr": "[Ortam olayı] ",
    "hi": "[पर्यावरण घटना] ",
    "id": "[Peristiwa lingkungan] ",
    "th": "[เหตุการณ์สภาพแวดล้อม] ",
    "vi": "[Sự kiện môi trường] ",
    "ar": "[حدث بيئي] ",
}

LANGUAGE_NAMES: dict[str, str] = {
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
    "tr": "Turkish",
    "hi": "Hindi",
    "id": "Indonesian",
    "th": "Thai",
    "vi": "Vietnamese",
    "ar": "Arabic",
}


def resolve_proactive_language(code: str | None) -> str:
    """空/未知 → 默认英文；已知 code → 原样返回。"""
    code = (code or "").strip()
    return code if code in SUPPORTED_PROACTIVE_LANGUAGES else DEFAULT_LANGUAGE


def camera_label(language: str) -> str:
    return CAMERA_LABEL.get(language, CAMERA_LABEL[DEFAULT_LANGUAGE])


def env_prefix(language: str) -> str:
    return ENV_PREFIX.get(language, ENV_PREFIX[DEFAULT_LANGUAGE])


def language_name(language: str) -> str:
    return LANGUAGE_NAMES.get(language, LANGUAGE_NAMES[DEFAULT_LANGUAGE])


def build_desc_language_clause(language: str) -> str:
    """返回检测器 desc 目标语言子句；zh-Hans 源语言返回空串。"""
    lang = resolve_proactive_language(language)
    if lang == SOURCE_LANGUAGE:
        return ""
    return (
        f"语言要求：desc 字段必须使用 {language_name(lang)} ({lang}) 书写，"
        "不超过40个字符；event 仍只能是 normal 或 danger，不要翻译 event。"
        "输出格式不变，仍是一行 JSON。"
    )


def build_reply_language_directive(language: str) -> str:
    """主动回合的回复语言指令；zh-Hans 源语言返回空串。"""
    lang = resolve_proactive_language(language)
    if lang == SOURCE_LANGUAGE:
        return ""
    return f"请使用 {language_name(lang)} ({lang}) 回复。"


def append_reply_language_directive(note: str, language: str) -> str:
    directive = build_reply_language_directive(language)
    return f"{note}\n{directive}" if directive else note


def build_output_language_rule(language: str) -> str:
    """chat 普通回合的输出语言规则（docs/server_multilingual.md §4.12）。

    优先用户当前消息的显式语言要求，其次当前消息语言，最后 session 语言；
    只注入固定表解析后的 name/code，原始客户端字符串不进入 prompt。
    """
    lang = resolve_proactive_language(language)
    name = language_name(lang)
    return (
        "Reply language rules (highest priority first):\n"
        '1. If the user\'s current message explicitly asks you to reply in a specific '
        'language (for example "reply in English"), reply in that language.\n'
        "2. Otherwise, reply in the language used by the user's current message.\n"
        "3. If the current message is language-neutral, too short, or its language "
        "cannot be determined, reply in the default language configured for this "
        f"session: {name} ({lang}).\n"
        "Do not choose the reply language from earlier conversation history or from "
        "the language of this system prompt."
    )
