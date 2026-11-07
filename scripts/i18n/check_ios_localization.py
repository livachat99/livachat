#!/usr/bin/env python3
"""Validate iOS localization resources for every supported app locale.

Checks the string catalog coverage/placeholders, per-locale InfoPlist.strings,
CFBundleLocalizations, knownRegions, and the InfoPlist variant group.
"""
from __future__ import annotations

import json
import plistlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
XCSTRINGS = ROOT / "apps/ios/LivaChatApp/Resources/Localizable.xcstrings"
INFO_PLIST = ROOT / "apps/ios/LivaChatApp/Resources/Info.plist"
RESOURCES = ROOT / "apps/ios/LivaChatApp/Resources"
PROJECT = ROOT / "apps/ios/LivaChatApp/LivaChatApp.xcodeproj/project.pbxproj"
AUTHORED = ROOT / "scripts/i18n/authored_translations.json"

LOCALES = [
    "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de",
    "fr", "it", "es", "pt", "hi", "id", "th", "vi", "ar",
]
INFO_PLIST_KEYS = [
    "NSMicrophoneUsageDescription",
    "NSLocalNetworkUsageDescription",
    "NSCameraUsageDescription",
    "NSPhotoLibraryAddUsageDescription",
]

PLACEHOLDER = re.compile(r"%(?:(\d+)\$)?(lld|@|[sdif])")
KIND = {"s": "@", "d": "lld", "i": "lld", "f": "f", "lld": "lld"}
STRINGS_LINE = re.compile(
    r'^\s*"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;\s*$'
)
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ASCII_DIGIT = re.compile(r"[0-9]")
FRENCH_ASCII_PUNCTUATION = re.compile(r" [?!:;]")
SWIFT_KEY_CALL = re.compile(r'\bL10n\.(?:t|format|plural)\(\s*"([^"]+)"')
SWIFT_LOCALIZED_STRING = re.compile(r'NSLocalizedString\(\s*"([^"]+)"')
SWIFT_LOCAL_T = re.compile(r'(?<![\w.])t\(\s*"([^"]+)"')
SWIFT_APP_ERROR = re.compile(r'AppErrorMessage\(\s*"([^"]+)"')
SWIFT_TITLE_KEY = re.compile(r'titleKey:\s*"([^"]+)"')

# Categories required for the app's count ranges. CLDR also defines `many` for
# fr/it/es/pt (counts >= 1e6) and `one` for tr; those fall back to `other`,
# whose reviewed text is identical for these locales, so they are not required.
REQUIRED_PLURAL_CATEGORIES = {
    "en": {"one", "other"},
    "zh-Hans": {"other"},
    "zh-Hant": {"other"},
    "ja": {"other"},
    "ko": {"other"},
    "ru": {"one", "few", "many", "other"},
    "de": {"one", "other"},
    "fr": {"one", "other"},
    "it": {"one", "other"},
    "es": {"one", "other"},
    "pt": {"one", "other"},
    "hi": {"one", "other"},
    "id": {"other"},
    "th": {"other"},
    "vi": {"other"},
    "ar": {"zero", "one", "two", "few", "many", "other"},
}


def plural_variants(entry: dict) -> dict[str, dict] | None:
    plural = entry.get("variations", {}).get("plural")
    if isinstance(plural, dict):
        return plural
    return None


def value_of(entry: dict) -> str:
    return entry.get("stringUnit", {}).get("value", "")


def placeholder_kinds(value: str) -> list[str]:
    return sorted(KIND.get(match.group(2), match.group(2)) for match in PLACEHOLDER.finditer(value))


def unescape(value: str) -> str:
    return (
        value.replace(r"\\", "\\")
        .replace(r"\"", '"')
        .replace(r"\n", "\n")
        .replace(r"\t", "\t")
    )


def parse_strings(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("/*") or line.startswith("//"):
            continue
        match = STRINGS_LINE.match(line)
        if match:
            values[unescape(match.group(1))] = unescape(match.group(2))
    return values


def known_regions(project: str) -> set[str]:
    match = re.search(r"knownRegions = \((.*?)\);", project, re.S)
    if not match:
        return set()
    return {
        item.strip().strip('"')
        for item in match.group(1).split(",")
        if item.strip()
    }


def check_state(
    key: str,
    locale: str,
    label: str,
    entry: dict,
    errors: list[str],
) -> None:
    state = entry.get("stringUnit", {}).get("state")
    if state != "translated":
        errors.append(f"{key} ({locale}/{label}): state is {state!r}, expected 'translated'")


def check_typography(
    key: str,
    locale: str,
    label: str,
    value: str,
    errors: list[str],
) -> None:
    if locale == "en" and HAN.search(value):
        errors.append(f"{key} ({locale}/{label}): English value contains Han characters")
    if locale == "fr" and FRENCH_ASCII_PUNCTUATION.search(value):
        errors.append(f"{key} ({locale}/{label}): French punctuation must use NBSP")
    if locale == "fr" and ("« " in value or " »" in value):
        errors.append(f"{key} ({locale}/{label}): French guillemet spacing must use NBSP")
    if locale == "ar":
        without_placeholders = PLACEHOLDER.sub("", value)
        if ASCII_DIGIT.search(without_placeholders):
            errors.append(f"{key} ({locale}/{label}): Arabic value contains ASCII digits")


def app_swift_paths() -> list[Path]:
    app_root = ROOT / "apps/ios/LivaChatApp"
    paths: list[Path] = []
    for path in app_root.rglob("*.swift"):
        parts = path.parts
        if "build" in parts or any(part.endswith("Tests") for part in parts):
            continue
        paths.append(path)
    return paths


def app_swift_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in app_swift_paths()
    )


def referenced_keys() -> set[str]:
    keys: set[str] = set()
    for path in app_swift_paths():
        text = path.read_text(encoding="utf-8", errors="ignore")
        keys.update(SWIFT_KEY_CALL.findall(text))
        keys.update(SWIFT_LOCALIZED_STRING.findall(text))
        keys.update(SWIFT_APP_ERROR.findall(text))
        keys.update(SWIFT_TITLE_KEY.findall(text))
        if path.name == "L10n.swift":
            keys.update(SWIFT_LOCAL_T.findall(text))
    return keys


def main() -> int:
    errors: list[str] = []
    catalog = json.loads(XCSTRINGS.read_text(encoding="utf-8"))
    strings = catalog.get("strings", {})
    source = catalog.get("sourceLanguage", "en")

    if source != "en":
        errors.append(f"sourceLanguage is {source!r}, expected 'en'")
    if not strings:
        errors.append("catalog has no strings")

    catalog_locales = {source}
    for entry in strings.values():
        catalog_locales.update(entry.get("localizations", {}))
    for locale in sorted(catalog_locales - set(LOCALES)):
        errors.append(f"catalog has unexpected locale {locale!r}")

    for key, entry in strings.items():
        localizations = entry.get("localizations", {})
        if not localizations.get(source):
            errors.append(f"{key}: missing source localization {source!r}")
            continue
        source_variants = plural_variants(localizations[source])
        fallback = (
            source_variants.get("other") if source_variants else localizations[source]
        )
        assert fallback is not None
        fallback_kinds = placeholder_kinds(value_of(fallback))

        for locale in LOCALES:
            localization = localizations.get(locale)
            if not localization:
                errors.append(f"{key}: missing locale {locale!r}")
                continue
            variants = plural_variants(localization)
            if source_variants is not None and variants is None:
                errors.append(f"{key} ({locale}): expected plural variations")
                continue
            if source_variants is None and variants is not None:
                errors.append(f"{key} ({locale}): unexpected plural variations")
                continue

            if variants is None:
                value = value_of(localization)
                if not value:
                    errors.append(f"{key} ({locale}): empty value")
                check_state(key, locale, "string", localization, errors)
                check_typography(key, locale, "string", value, errors)
                kinds = placeholder_kinds(value)
                if kinds != fallback_kinds:
                    errors.append(
                        f"{key} ({locale}): placeholders {kinds} != {fallback_kinds}"
                    )
                continue

            for quantity, variant in variants.items():
                value = value_of(variant)
                if not value:
                    errors.append(f"{key} ({locale}/{quantity}): empty value")
                check_state(key, locale, quantity, variant, errors)
                check_typography(key, locale, quantity, value, errors)
                anchor = source_variants.get(quantity) or source_variants.get("other")
                expected = placeholder_kinds(value_of(anchor)) if anchor else fallback_kinds
                kinds = placeholder_kinds(value)
                if kinds != expected:
                    errors.append(
                        f"{key} ({locale}/{quantity}): placeholders {kinds} != {expected}"
                    )

    # Required CLDR plural categories for every plural key.
    for key, entry in strings.items():
        source_variants = plural_variants(entry.get("localizations", {}).get(source, {}))
        if source_variants is None:
            continue
        for locale in LOCALES:
            variants = plural_variants(entry.get("localizations", {}).get(locale, {}))
            if variants is None:
                continue
            missing = REQUIRED_PLURAL_CATEGORIES[locale] - set(variants)
            if missing:
                errors.append(
                    f"{key} ({locale}): missing plural categories {sorted(missing)}"
                )

    # InfoPlist.strings coverage.
    for locale in LOCALES:
        path = RESOURCES / f"{locale}.lproj/InfoPlist.strings"
        if not path.is_file():
            errors.append(f"missing {path.relative_to(ROOT)}")
            continue
        values = parse_strings(path)
        for key in INFO_PLIST_KEYS:
            if not values.get(key):
                errors.append(f"{path.relative_to(ROOT)}: missing or empty {key}")

    # CFBundleLocalizations.
    info = plistlib.loads(INFO_PLIST.read_bytes())
    declared = info.get("CFBundleLocalizations", [])
    for locale in LOCALES:
        if locale not in declared:
            errors.append(f"Info.plist: CFBundleLocalizations is missing {locale!r}")
    for locale in declared:
        if locale not in LOCALES:
            errors.append(f"Info.plist: unexpected CFBundleLocalization {locale!r}")

    # .lproj directories.
    lproj = {path.stem for path in RESOURCES.glob("*.lproj") if path.is_dir()}
    for locale in sorted(set(LOCALES) - lproj):
        errors.append(f"missing {locale}.lproj directory")
    for locale in sorted(lproj - set(LOCALES)):
        errors.append(f"unexpected .lproj directory {locale!r}")

    # Xcode project regions and InfoPlist variant group.
    project = PROJECT.read_text(encoding="utf-8")
    regions = known_regions(project)
    for locale in LOCALES:
        if locale not in regions:
            errors.append(f"project.pbxproj: knownRegions is missing {locale!r}")
        reference = re.compile(
            rf'name = ("{re.escape(locale)}"|{re.escape(locale)}); '
            rf'path = ("{re.escape(locale)}\.lproj/InfoPlist\.strings"|'
            rf'{re.escape(locale)}\.lproj/InfoPlist\.strings);'
        )
        if not reference.search(project):
            errors.append(f"project.pbxproj: missing InfoPlist.strings reference for {locale!r}")
    for locale in sorted(regions - set(LOCALES) - {"Base"}):
        errors.append(f"project.pbxproj: unexpected knownRegion {locale!r}")

    # Unused catalog keys (ignore test-only references).
    swift_source = app_swift_source()
    for key in strings:
        if f'"{key}"' not in swift_source:
            errors.append(f"unused catalog key {key!r}")

    # Every key referenced from Swift must exist in the catalog; otherwise the
    # build succeeds and the app shows the raw key at runtime.
    for key in sorted(referenced_keys() - set(strings)):
        errors.append(f"missing catalog key {key!r} referenced from Swift")

    # authored_translations.json feeds import_android_to_ios.py; drift means a
    # re-import would silently rewrite reviewed copy.
    authored = json.loads(AUTHORED.read_text(encoding="utf-8"))
    for locale in sorted(authored):
        if locale not in LOCALES:
            errors.append(f"authored translations: unexpected locale {locale!r}")
            continue
        for key, value in authored[locale].items():
            entry = strings.get(key)
            if entry is None:
                continue
            localization = entry.get("localizations", {}).get(locale)
            if localization is None:
                continue
            variants = plural_variants(localization)
            actual = (
                {quantity: value_of(variant) for quantity, variant in variants.items()}
                if variants is not None
                else value_of(localization)
            )
            if actual != value:
                errors.append(
                    f"authored translations drift {locale}/{key}: "
                    f"{value!r} != catalog {actual!r}"
                )

    if errors:
        print(f"iOS localization check failed ({len(errors)} issue(s)):")
        for error in errors:
            print(f"  - {error}")
        return 1

    key_count = len(strings)
    plural_count = sum(
        1 for entry in strings.values()
        if plural_variants(entry.get("localizations", {}).get(source, {}))
    )
    print(
        f"iOS localization OK: {key_count} keys, {plural_count} plural, "
        f"{len(LOCALES)} locales, {len(INFO_PLIST_KEYS)} InfoPlist keys"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
