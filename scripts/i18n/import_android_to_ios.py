#!/usr/bin/env python3
"""Import one Android locale into the iOS string catalog.

Only the requested locale is written. Existing en / zh-Hans values are never
touched. Shared keys come from Android; iOS-only/new keys come from
authored_translations.json.
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANDROID_RES = ROOT / "apps/android/LivaChatApp/src/main/res"
DEFAULT_XCSTRINGS = ROOT / "apps/ios/LivaChatApp/Resources/Localizable.xcstrings"
DEFAULT_AUTHORED = ROOT / "scripts/i18n/authored_translations.json"

LOCALE_DIRS = {
    "zh-Hant": "values-b+zh+Hant",
    "ja": "values-ja",
    "ko": "values-ko",
    "ru": "values-ru",
    "de": "values-de",
    "fr": "values-fr",
    "it": "values-it",
    "es": "values-es",
    "pt": "values-pt",
    "hi": "values-hi",
    "id": "values-in",
    "th": "values-th",
    "vi": "values-vi",
    "ar": "values-ar",
}

REMOVE_KEYS = {
    "app_name",
    "avatar_custom_image_desc",
    "common_coming_soon",
    "conv_permission_lost",
    "webview_unsupported_link",
}

PLURAL_KEYS = {
    "conv_agent_jobs_title",
    "conv_agent_jobs_running",
    "conv_job_remaining_chars",
    "history_item_meta",
    "mylife_audio_entry_desc",
    "mylife_frames_count",
    "mylife_analyzing_frames",
    "mylife_delete_confirm",
    "mylife_segments_count",
    "mylife_search_total",
    "mylife_photos_deleted",
    "mylife_period_summary",
}

# Android still carries a technical detail placeholder for this key; iOS shows
# only the localized title and logs the detail.
FORCE_AUTHORED_KEYS = {"conv_avatar_save_failed"}

PLACEHOLDER = re.compile(r"%(?:(\d+)\$)?([sdif])")


def android_unescape(value: str) -> str:
    value = value.replace(r"\n", "\n").replace(r"\t", "\t")
    value = value.replace(r"\'", "'").replace(r'\"', '"').replace(r"\\", "\\")
    return value


def convert_placeholders(value: str, key: str, locale: str) -> str:
    def replace(match: re.Match[str]) -> str:
        position = match.group(1)
        kind = match.group(2)
        prefix = f"%{position}$" if position else "%"

        if key == "conv_job_remaining_chars":
            return f"{prefix}lld"
        if key == "mylife_photos_deleted_partial":
            return f"{prefix}lld"
        if key in {"mylife_overview_month", "mylife_week_label"}:
            return f"{prefix}@"
        if kind == "s":
            return f"{prefix}@"
        if kind == "d":
            return f"{prefix}lld"
        raise ValueError(f"unsupported placeholder {match.group(0)} in {key}")

    converted = PLACEHOLDER.sub(replace, value)

    if key == "mylife_overview_month":
        if locale in {"zh-Hans", "zh-Hant", "ja"}:
            converted = converted.replace("%2$@", "%2$@月")
        elif locale == "ko":
            converted = converted.replace("%2$@", "%2$@월")
    return converted


def parse_android(path: Path) -> dict[str, tuple[str, object]]:
    root = ET.parse(path).getroot()
    values: dict[str, tuple[str, object]] = {}
    for element in root:
        name = element.get("name")
        if not name:
            continue
        if element.tag == "string":
            values[name] = ("string", android_unescape(element.text or ""))
        elif element.tag == "plurals":
            values[name] = (
                "plural",
                {
                    item.get("quantity"): android_unescape(item.text or "")
                    for item in element
                    if item.get("quantity")
                },
            )
    return values


def localizations_for(
    key: str,
    android: dict[str, tuple[str, object]],
    authored: dict[str, object],
    locale: str,
) -> dict:
    if key in android and key not in FORCE_AUTHORED_KEYS:
        kind, value = android[key]
        if kind == "plural":
            if key not in PLURAL_KEYS:
                raise ValueError(f"{key} is plural in Android but not iOS")
            return {
                "variations": {
                    "plural": {
                        quantity: {
                            "stringUnit": {
                                "state": "translated",
                                "value": convert_placeholders(text, key, locale),
                            }
                        }
                        for quantity, text in value.items()
                    }
                }
            }
        return {
            "stringUnit": {
                "state": "translated",
                "value": convert_placeholders(value, key, locale),
            }
        }

    if key not in authored:
        raise KeyError(f"missing authored {locale} value for {key}")
    value = authored[key]
    if key in PLURAL_KEYS:
        if not isinstance(value, dict):
            raise TypeError(f"authored plural {key} must be an object")
        return {
            "variations": {
                "plural": {
                    quantity: {
                        "stringUnit": {
                            "state": "translated",
                            "value": text,
                        }
                    }
                    for quantity, text in value.items()
                }
            }
        }
    if not isinstance(value, str):
        raise TypeError(f"authored string {key} must be a string")
    return {"stringUnit": {"state": "translated", "value": value}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locale", required=True, choices=sorted(LOCALE_DIRS))
    parser.add_argument("--android-res", type=Path, default=DEFAULT_ANDROID_RES)
    parser.add_argument("--xcstrings", type=Path, default=DEFAULT_XCSTRINGS)
    parser.add_argument("--authored", type=Path, default=DEFAULT_AUTHORED)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    catalog = json.loads(args.xcstrings.read_text())
    strings = catalog["strings"]
    for key in REMOVE_KEYS:
        strings.pop(key, None)

    android = parse_android(args.android_res / LOCALE_DIRS[args.locale] / "strings.xml")
    authored_all = json.loads(args.authored.read_text())
    authored = authored_all.get(args.locale)
    if authored is None:
        raise SystemExit(f"no authored translations for {args.locale}")

    missing = []
    for key in strings:
        try:
            localized = localizations_for(key, android, authored, args.locale)
        except KeyError:
            missing.append(key)
            continue
        strings[key].setdefault("localizations", {})[args.locale] = localized

    if missing:
        raise SystemExit(f"missing {len(missing)} authored keys: {missing}")

    empty = [
        key
        for key, entry in strings.items()
        if not entry.get("localizations", {}).get(args.locale)
    ]
    if empty:
        raise SystemExit(f"empty {args.locale} entries: {empty}")

    from_android = [key for key in strings if key in android and key not in FORCE_AUTHORED_KEYS]
    print(
        f"{args.locale}: {len(strings)} keys, "
        f"{len(from_android)} from Android, "
        f"{len(strings) - len(from_android)} authored"
    )
    if args.write:
        args.xcstrings.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
        )
        print(f"wrote {args.xcstrings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
