#!/usr/bin/env python3
"""Generate Swift DTOs for the LivaChat WebSocket protocol.

Independent from the Kotlin generator: this script parses the JSON Schema
under ``packages/api-schema`` itself and renders Swift. The JSON Schema is the
single source of truth; the Kotlin generator and its generated files are not
touched.

Outputs:

    ProtocolModels.swift   - shared enums + every payload struct
    ClientMessage.swift    - ClientMessage enum + Codable
    ServerMessage.swift    - ServerMessage enum + Codable

Contract (must match ProtocolJSON.swift):

* Base64 wire strings are carried as ``Data`` (JSONEncoder/Decoder .base64).
* Optional properties are omitted when nil; required-nullable properties (e.g.
  ``session_id``) are always emitted, as ``null`` when nil.
* Unknown JSON fields are ignored by synthesized Codable.
* Unknown message types throw ``ProtocolDecodingError.unknownMessageType``.
* Unknown enum values fail decoding (strict enums).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_OUT = "packages/sdk-swift/Sources/LivaChatProtocol/Generated"

OUTPUT_MODELS = "ProtocolModels.swift"
OUTPUT_CLIENT = "ClientMessage.swift"
OUTPUT_SERVER = "ServerMessage.swift"

# Integer enums we render as Swift enums. Unregistered integer enums degrade to
# Int, matching the Kotlin generator's behavior.
INTEGER_ENUM_CASES: dict[str, dict[int, str]] = {
    "Orientation": {
        0: "degrees0",
        90: "degrees90",
        180: "degrees180",
        270: "degrees270",
    },
    "AudioSampleRate": {
        16000: "hz16000",
        24000: "hz24000",
    },
}

# Fields whose schema type is an inline string but whose payload is Base64.
# ai_text.image.data is not a $ref to Base64String, but its description says
# "Base64-encoded JPEG"; keep it as Data for consistency with audio/video.
BASE64_STRING_FIELDS = {("ServerAiTextImage", "data")}

SWIFT_KEYWORDS = {
    "associatedtype", "class", "deinit", "enum", "extension", "fileprivate",
    "func", "import", "init", "inout", "internal", "let", "open", "operator",
    "private", "precedencegroup", "protocol", "public", "rethrows", "static",
    "struct", "subscript", "typealias", "var", "break", "case", "continue",
    "default", "defer", "do", "else", "fallthrough", "for", "guard", "if",
    "in", "repeat", "return", "switch", "where", "while", "as", "catch",
    "false", "is", "nil", "super", "self", "throw", "throws", "true", "try",
    # Swift 5.7+ / Swift 6 concurrency and declaration keywords.
    "any", "some", "actor", "async", "await", "nonisolated", "isolated",
    "consuming", "borrowing", "sending", "package", "macro", "distributed",
    "freestanding",
    # Contextual keywords that are safe to escape.
    "associativity", "convenience", "didSet", "dynamic", "final", "get",
    "indirect", "infix", "lazy", "mutating", "nonmutating", "optional",
    "override", "postfix", "prefix", "required", "set", "unowned", "weak",
    "willSet",
}


class SchemaConflictError(ValueError):
    """One generated Swift name maps to different schema definitions."""


# ---------------------------------------------------------------------------
# Type model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwiftType:
    name: str
    nullable: bool = False

    def render(self) -> str:
        return f"{self.name}?" if self.nullable else self.name

    def with_nullable(self, nullable: bool) -> "SwiftType":
        if nullable == self.nullable:
            return self
        return SwiftType(self.name, nullable)


@dataclass(frozen=True)
class PropertySpec:
    json_name: str
    swift_name: str
    swift_type: SwiftType
    required: bool
    description: str = ""


@dataclass
class ClassSpec:
    name: str
    properties: list[PropertySpec] = field(default_factory=list)
    description: str = ""
    required_nullable: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EnumValueSpec:
    json_value: Any
    swift_name: str


@dataclass
class EnumSpec:
    name: str
    values: list[EnumValueSpec]
    raw_kind: str  # "String" or "Int"
    description: str = ""


@dataclass(frozen=True)
class MessageSpec:
    direction: str  # "c2s" or "s2c"
    serial_name: str
    class_name: str
    case_name: str
    payload_class: str
    description: str

    @property
    def case_ident(self) -> str:
        return _escape_identifier(self.case_name)


@dataclass
class GeneratorState:
    schema_root: Path
    common_defs: dict[str, Any]
    enums: dict[str, EnumSpec] = field(default_factory=dict)
    classes: dict[str, ClassSpec] = field(default_factory=dict)
    class_schemas: dict[str, str] = field(default_factory=dict)
    enum_fingerprints: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def _split_tokens(value: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for ch in value:
        if ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    return out


def upper_camel(value: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in _split_tokens(value))


def lower_camel(value: str) -> str:
    camel = upper_camel(value)
    return camel[:1].lower() + camel[1:] if camel else value


def swift_enum_case_name(value: str) -> str:
    name = lower_camel(value)
    if not name:
        return "unknown"
    if name[0].isdigit():
        name = "value" + name[0].upper() + name[1:]
    return name


def _escape_identifier(name: str) -> str:
    return f"`{name}`" if name in SWIFT_KEYWORDS else name


def _dedupe_enum_values(values: Iterable[EnumValueSpec]) -> list[EnumValueSpec]:
    seen: dict[str, int] = {}
    out: list[EnumValueSpec] = []
    for value in values:
        name = value.swift_name.strip("`")
        if name in seen:
            seen[name] += 1
            name = f"{name}{seen[name]}"
        else:
            seen[name] = 1
        out.append(EnumValueSpec(json_value=value.json_value, swift_name=name))
    return out


def _ref_tail(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _strip_payload_suffix(name: str) -> str:
    return name[: -len("Payload")] if name.endswith("Payload") else name


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _is_nullable_oneof(schema: dict[str, Any]) -> bool:
    if "oneOf" not in schema:
        return False
    return any(item.get("type") == "null" for item in schema["oneOf"])


def _non_null_branch(schema: dict[str, Any]) -> dict[str, Any]:
    for item in schema.get("oneOf", []):
        if item.get("type") != "null":
            return item
    return schema


def _is_const_oneof(schema: dict[str, Any]) -> bool:
    one_of = schema.get("oneOf")
    return bool(one_of) and all("const" in item for item in one_of)


def _integer_swift_type(schema: dict[str, Any]) -> str:
    maximum = schema.get("maximum")
    if isinstance(maximum, int):
        if maximum <= 255:
            return "UInt8"
        if maximum <= 65535:
            return "UInt16"
        if maximum <= 4294967295:
            return "UInt32"
        return "UInt64"
    return "Int"


def _primitive_swift_type(schema: dict[str, Any]) -> str | None:
    t = schema.get("type")
    if t == "string":
        return "String"
    if t == "boolean":
        return "Bool"
    if t == "number":
        return "Double"
    if t == "integer":
        return _integer_swift_type(schema)
    return None


def _const_swift_type(value: Any) -> str:
    if isinstance(value, bool):
        return "Bool"
    if isinstance(value, int):
        return "Int"
    if isinstance(value, float):
        return "Double"
    return "String"


def _literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _enum_fingerprint(
    raw_kind: str, values: Iterable[Any], description: str
) -> str:
    return _canonical_json(
        {"kind": raw_kind, "values": list(values), "description": description}
    )


# ---------------------------------------------------------------------------
# common.json resolution
# ---------------------------------------------------------------------------


def _register_string_enum(
    state: GeneratorState,
    name: str,
    values: list[str],
    description: str = "",
) -> None:
    fingerprint = _enum_fingerprint("String", values, description)
    if name in state.classes:
        raise SchemaConflictError(
            f"schema conflict for Swift type '{name}': already registered as a "
            "struct, cannot register as an enum"
        )
    if name in state.enums:
        previous = state.enum_fingerprints.get(name)
        if previous != fingerprint:
            raise SchemaConflictError(
                f"schema conflict for Swift enum '{name}': the same generated "
                "name maps to different definitions\n"
                f"  registered: {previous}\n"
                f"  new:        {fingerprint}\n"
                "Rename one of the conflicting properties."
            )
        return
    entries = [
        EnumValueSpec(json_value=v, swift_name=swift_enum_case_name(v))
        for v in values
    ]
    state.enums[name] = EnumSpec(
        name=name,
        values=_dedupe_enum_values(entries),
        raw_kind="String",
        description=description,
    )
    state.enum_fingerprints[name] = fingerprint


def _register_integer_enum(
    state: GeneratorState,
    name: str,
    values: list[int],
    description: str = "",
) -> None:
    mapping = INTEGER_ENUM_CASES.get(name)
    if mapping is None:
        return
    fingerprint = _enum_fingerprint("Int", values, description)
    if name in state.classes:
        raise SchemaConflictError(
            f"schema conflict for Swift type '{name}': already registered as a "
            "struct, cannot register as an enum"
        )
    if name in state.enums:
        previous = state.enum_fingerprints.get(name)
        if previous != fingerprint:
            raise SchemaConflictError(
                f"schema conflict for Swift enum '{name}': the same generated "
                "name maps to different definitions\n"
                f"  registered: {previous}\n"
                f"  new:        {fingerprint}\n"
                "Rename one of the conflicting properties."
            )
        return
    entries: list[EnumValueSpec] = []
    for value in values:
        case_name = mapping.get(value, f"value{value}")
        entries.append(EnumValueSpec(json_value=value, swift_name=case_name))
    state.enums[name] = EnumSpec(
        name=name,
        values=_dedupe_enum_values(entries),
        raw_kind="Int",
        description=description,
    )
    state.enum_fingerprints[name] = fingerprint


def _resolve_common_def(state: GeneratorState, def_name: str) -> SwiftType:
    if def_name not in state.common_defs:
        # Unknown ref: fall back to String so schema evolution does not crash
        # code generation.
        return SwiftType("String")

    schema = state.common_defs[def_name]

    if def_name == "Base64String":
        return SwiftType("Data")
    if def_name == "AudioDurationMs":
        return SwiftType("Int")

    if schema.get("type") == "string" and "enum" in schema:
        _register_string_enum(
            state, def_name, schema["enum"], schema.get("description", "")
        )
        return SwiftType(def_name)

    if "const" in schema:
        return SwiftType(_const_swift_type(schema["const"]))

    if _is_const_oneof(schema):
        consts = [item["const"] for item in schema["oneOf"]]
        if all(isinstance(v, str) for v in consts):
            _register_string_enum(
                state, def_name, consts, schema.get("description", "")
            )
            return SwiftType(def_name)
        if all(isinstance(v, bool) for v in consts):
            return SwiftType("Bool")
        if all(isinstance(v, int) for v in consts):
            return SwiftType("Int")
        if all(isinstance(v, float) for v in consts):
            return SwiftType("Double")

    if schema.get("type") == "integer":
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and enum_values:
            if def_name in INTEGER_ENUM_CASES:
                _register_integer_enum(
                    state, def_name, enum_values, schema.get("description", "")
                )
                return SwiftType(def_name)
            return SwiftType("Int")
        return SwiftType(_integer_swift_type(schema))

    if "$ref" in schema:
        return _resolve_common_def(state, _ref_tail(schema["$ref"]))

    primitive = _primitive_swift_type(schema)
    return SwiftType(primitive or "String")


# ---------------------------------------------------------------------------
# Property / class resolution
# ---------------------------------------------------------------------------


def _resolve_property_type(
    state: GeneratorState,
    schema: dict[str, Any],
    class_name_hint: str,
) -> SwiftType:
    nullable = _is_nullable_oneof(schema)
    inner = _non_null_branch(schema) if nullable else schema

    if "$ref" in inner:
        return _resolve_common_def(state, _ref_tail(inner["$ref"])).with_nullable(
            nullable
        )

    if inner.get("type") == "string" and "enum" in inner:
        _register_string_enum(
            state,
            class_name_hint,
            inner["enum"],
            inner.get("description", ""),
        )
        return SwiftType(class_name_hint, nullable)

    if inner.get("type") == "integer" and "enum" in inner:
        # No inline integer enums exist today; keep them as Int unless the
        # hint is one of the explicitly registered definitions.
        if class_name_hint in INTEGER_ENUM_CASES:
            _register_integer_enum(
                state,
                class_name_hint,
                inner["enum"],
                inner.get("description", ""),
            )
            return SwiftType(class_name_hint, nullable)
        return SwiftType("Int", nullable)

    if "const" in inner:
        return SwiftType(_const_swift_type(inner["const"]), nullable)

    if _is_const_oneof(inner):
        consts = [item["const"] for item in inner["oneOf"]]
        if all(isinstance(v, bool) for v in consts):
            return SwiftType("Bool", nullable)
        if all(isinstance(v, int) for v in consts):
            return SwiftType("Int", nullable)
        if all(isinstance(v, float) for v in consts):
            return SwiftType("Double", nullable)
        if all(isinstance(v, str) for v in consts):
            return SwiftType("String", nullable)

    if inner.get("type") == "object":
        register_class(state, class_name_hint, inner)
        return SwiftType(class_name_hint, nullable)

    if inner.get("type") == "array":
        items = inner.get("items", {})
        item_hint = class_name_hint + "Item"
        item_type = _resolve_property_type(state, items, item_hint)
        return SwiftType(f"[{item_type.render()}]", nullable)

    primitive = _primitive_swift_type(inner)
    return SwiftType(primitive or "String", nullable)


def register_class(
    state: GeneratorState,
    class_name: str,
    schema: dict[str, Any],
) -> None:
    fingerprint = _canonical_json(schema)
    if class_name in state.enums:
        raise SchemaConflictError(
            f"schema conflict for Swift type '{class_name}': already registered "
            "as an enum, cannot register as a struct"
        )
    if class_name in state.classes:
        previous = state.class_schemas.get(class_name)
        if previous != fingerprint:
            raise SchemaConflictError(
                f"schema conflict for Swift struct '{class_name}': the same "
                "generated name maps to different definitions\n"
                f"  registered: {previous}\n"
                f"  new:        {fingerprint}\n"
                "Rename one of the conflicting properties."
            )
        return

    required_fields: set[str] = set(schema.get("required", []))
    properties: list[PropertySpec] = []
    required_nullable: list[str] = []
    base_for_children = _strip_payload_suffix(class_name)

    for json_name, prop_schema in (schema.get("properties") or {}).items():
        child_hint = f"{base_for_children}{upper_camel(json_name)}"
        if (class_name, json_name) in BASE64_STRING_FIELDS:
            # Keep the schema's nullability; do not hard-code non-optional Data.
            swift_type = SwiftType("Data", _is_nullable_oneof(prop_schema))
        else:
            swift_type = _resolve_property_type(state, prop_schema, child_hint)
        is_required = json_name in required_fields
        is_nullable_schema = _is_nullable_oneof(prop_schema)

        if not is_required:
            swift_type = swift_type.with_nullable(True)
        elif is_nullable_schema:
            required_nullable.append(json_name)

        properties.append(
            PropertySpec(
                json_name=json_name,
                swift_name=lower_camel(json_name),
                swift_type=swift_type,
                required=is_required,
                description=prop_schema.get("description", ""),
            )
        )

    state.classes[class_name] = ClassSpec(
        name=class_name,
        properties=properties,
        description=schema.get("description", ""),
        required_nullable=required_nullable,
    )
    state.class_schemas[class_name] = fingerprint


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------


def _load_union_paths(schema_root: Path, direction: str) -> list[Path]:
    union = _load_json(schema_root / direction / "message.json")
    return [
        (schema_root / direction / item["$ref"]).resolve()
        for item in union["anyOf"]
    ]


def _build_message_specs(
    state: GeneratorState, direction: str
) -> list[MessageSpec]:
    prefix = "Client" if direction == "c2s" else "Server"
    specs: list[MessageSpec] = []
    seen_serials: dict[str, str] = {}
    seen_cases: dict[str, str] = {}
    for path in _load_union_paths(state.schema_root, direction):
        schema = _load_json(path)
        type_const = schema["properties"]["type"]["const"]
        class_name = upper_camel(type_const)
        case_name = lower_camel(class_name)
        if type_const in seen_serials:
            raise SchemaConflictError(
                f"duplicate message type '{type_const}' in "
                f"{direction}/message.json "
                f"({seen_serials[type_const]} and {path.name})"
            )
        if case_name in seen_cases:
            raise SchemaConflictError(
                f"message types '{seen_cases[case_name]}' and '{type_const}' "
                f"both map to Swift case '{case_name}' in "
                f"{direction}/message.json"
            )
        seen_serials[type_const] = path.name
        seen_cases[case_name] = type_const
        payload_class = f"{prefix}{class_name}Payload"
        register_class(state, payload_class, schema["properties"]["payload"])
        specs.append(
            MessageSpec(
                direction=direction,
                serial_name=type_const,
                class_name=class_name,
                case_name=case_name,
                payload_class=payload_class,
                description=schema.get("description", ""),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

INDENT = "    "


def _render_doc(text: str, indent: str = "") -> list[str]:
    if not text:
        return []
    paragraph = " ".join(text.split())
    words = paragraph.split(" ")
    line_limit = max(40, 96 - len(indent))
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        if current and current_len + 1 + len(word) > line_limit:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            if current:
                current_len += 1 + len(word)
                current.append(word)
            else:
                current = [word]
                current_len = len(word)
    if current:
        lines.append(" ".join(current))
    return [f"{indent}/// {line}" if line else f"{indent}///" for line in lines]


def _file_header(schema_sources: list[str]) -> list[str]:
    return [
        "// Auto-generated by scripts/generate_swift_api.py.",
        "// DO NOT EDIT MANUALLY. Edit the JSON Schema in packages/api-schema/",
        "// and re-run the generator instead.",
        "//",
        "// Source files (SSOT):",
        *(f"//   - {source}" for source in schema_sources),
        "//",
        "// Codec contract:",
        "//   - Base64 wire strings are carried as Data",
        "//   - no key conversion; explicit CodingKeys carry wire names",
        "//   - optional nil fields are omitted; required-nullable fields emit null",
        "",
        "import Foundation",
        "",
    ]


def _render_enum(spec: EnumSpec) -> list[str]:
    lines: list[str] = []
    lines.extend(_render_doc(spec.description))
    lines.append(
        f"public enum {spec.name}: {spec.raw_kind}, Codable, Equatable, Sendable {{"
    )
    for value in spec.values:
        case_name = _escape_identifier(value.swift_name)
        if spec.raw_kind == "String":
            lines.append(f"{INDENT}case {case_name} = {_literal(value.json_value)}")
        else:
            lines.append(f"{INDENT}case {case_name} = {value.json_value}")
    lines.append("}")
    return lines


def _render_init(spec: ClassSpec) -> list[str]:
    if not spec.properties:
        return [f"{INDENT}public init() {{}}"]

    ordered = [p for p in spec.properties if p.required] + [
        p for p in spec.properties if not p.required
    ]
    params: list[str] = []
    for prop in ordered:
        ident = _escape_identifier(prop.swift_name)
        suffix = "" if prop.required else " = nil"
        params.append(f"{ident}: {prop.swift_type.render()}{suffix}")

    lines = [f"{INDENT}public init({', '.join(params)}) {{"]
    for prop in spec.properties:
        ident = _escape_identifier(prop.swift_name)
        lines.append(f"{INDENT * 2}self.{ident} = {ident}")
    lines.append(f"{INDENT}}}")
    return lines


def _render_coding_keys(spec: ClassSpec) -> list[str]:
    lines = [f"{INDENT}private enum CodingKeys: String, CodingKey {{"]
    for prop in spec.properties:
        ident = _escape_identifier(prop.swift_name)
        if prop.swift_name == prop.json_name and ident == prop.swift_name:
            lines.append(f"{INDENT * 2}case {ident}")
        else:
            lines.append(
                f"{INDENT * 2}case {ident} = {_literal(prop.json_name)}"
            )
    lines.append(f"{INDENT}}}")
    return lines


def _render_custom_codable(spec: ClassSpec) -> list[str]:
    lines: list[str] = []
    lines.append(f"{INDENT}public func encode(to encoder: Encoder) throws {{")
    lines.append(
        f"{INDENT * 2}var container = encoder.container(keyedBy: CodingKeys.self)"
    )
    for prop in spec.properties:
        ident = _escape_identifier(prop.swift_name)
        key = f".{ident}"
        if prop.json_name in spec.required_nullable:
            lines.append(f"{INDENT * 2}if let {ident} {{")
            lines.append(
                f"{INDENT * 3}try container.encode({ident}, forKey: {key})"
            )
            lines.append(f"{INDENT * 2}}} else {{")
            lines.append(f"{INDENT * 3}try container.encodeNil(forKey: {key})")
            lines.append(f"{INDENT * 2}}}")
        elif prop.required:
            lines.append(
                f"{INDENT * 2}try container.encode({ident}, forKey: {key})"
            )
        else:
            lines.append(
                f"{INDENT * 2}try container.encodeIfPresent({ident}, forKey: {key})"
            )
    lines.append(f"{INDENT}}}")
    lines.append("")
    lines.append(f"{INDENT}public init(from decoder: Decoder) throws {{")
    lines.append(
        f"{INDENT * 2}let container = try decoder.container(keyedBy: CodingKeys.self)"
    )
    for prop in spec.properties:
        ident = _escape_identifier(prop.swift_name)
        key = f".{ident}"
        if prop.json_name in spec.required_nullable:
            message = _literal(f"{prop.json_name} is required")
            lines.append(f"{INDENT * 2}guard container.contains({key}) else {{")
            lines.append(
                f"{INDENT * 3}throw DecodingError.keyNotFound("
                f"CodingKeys.{ident}, .init("
                f"codingPath: container.codingPath, debugDescription: {message}))"
            )
            lines.append(f"{INDENT * 2}}}")
            lines.append(
                f"{INDENT * 2}self.{ident} = try container.decodeIfPresent("
                f"{prop.swift_type.name}.self, forKey: {key})"
            )
        elif prop.required:
            lines.append(
                f"{INDENT * 2}self.{ident} = try container.decode("
                f"{prop.swift_type.name}.self, forKey: {key})"
            )
        else:
            lines.append(
                f"{INDENT * 2}self.{ident} = try container.decodeIfPresent("
                f"{prop.swift_type.name}.self, forKey: {key})"
            )
    lines.append(f"{INDENT}}}")
    return lines


def _render_class(spec: ClassSpec) -> list[str]:
    lines: list[str] = []
    lines.extend(_render_doc(spec.description))
    lines.append(
        f"public struct {spec.name}: Codable, Equatable, Sendable {{"
    )
    for prop in spec.properties:
        ident = _escape_identifier(prop.swift_name)
        lines.extend(_render_doc(prop.description, INDENT))
        lines.append(f"{INDENT}public let {ident}: {prop.swift_type.render()}")
    lines.append("")
    lines.extend(_render_init(spec))
    if spec.properties:
        lines.append("")
        lines.extend(_render_coding_keys(spec))
        if spec.required_nullable:
            lines.append("")
            lines.extend(_render_custom_codable(spec))
    lines.append("}")
    return lines


def _render_message_enum(
    name: str,
    specs: list[MessageSpec],
    direction_label: str,
) -> list[str]:
    lines: list[str] = [f"/// {direction_label} protocol message."]
    lines.append(f"public enum {name}: Equatable, Sendable {{")
    for spec in specs:
        lines.extend(_render_doc(spec.description, INDENT))
        lines.append(
            f"{INDENT}case {spec.case_ident}("
            f"seq: UInt32, timestamp: UInt64, payload: {spec.payload_class})"
        )
    lines.append("")
    lines.append(f"{INDENT}public var type: String {{")
    lines.append(f"{INDENT * 2}switch self {{")
    for spec in specs:
        lines.append(
            f"{INDENT * 2}case .{spec.case_ident}: return {_literal(spec.serial_name)}"
        )
    lines.append(f"{INDENT * 2}}}")
    lines.append(f"{INDENT}}}")
    lines.append("")
    lines.append(f"{INDENT}public var seq: UInt32 {{")
    lines.append(f"{INDENT * 2}switch self {{")
    for spec in specs:
        lines.append(
            f"{INDENT * 2}case let .{spec.case_ident}(seq, _, _): return seq"
        )
    lines.append(f"{INDENT * 2}}}")
    lines.append(f"{INDENT}}}")
    lines.append("")
    lines.append(f"{INDENT}public var timestamp: UInt64 {{")
    lines.append(f"{INDENT * 2}switch self {{")
    for spec in specs:
        lines.append(
            f"{INDENT * 2}case let .{spec.case_ident}(_, timestamp, _): return timestamp"
        )
    lines.append(f"{INDENT * 2}}}")
    lines.append(f"{INDENT}}}")
    lines.append("}")
    lines.append("")
    lines.append(f"extension {name}: Codable {{")
    lines.append(f"{INDENT}private enum CodingKeys: String, CodingKey {{")
    lines.append(f"{INDENT * 2}case type, seq, timestamp, payload")
    lines.append(f"{INDENT}}}")
    lines.append("")
    lines.append(f"{INDENT}public func encode(to encoder: Encoder) throws {{")
    lines.append(
        f"{INDENT * 2}var container = encoder.container(keyedBy: CodingKeys.self)"
    )
    lines.append(f"{INDENT * 2}switch self {{")
    for spec in specs:
        lines.append(
            f"{INDENT * 2}case let .{spec.case_ident}(seq, timestamp, payload):"
        )
        lines.append(
            f"{INDENT * 3}try container.encode({_literal(spec.serial_name)}, forKey: .type)"
        )
        lines.append(f"{INDENT * 3}try container.encode(seq, forKey: .seq)")
        lines.append(f"{INDENT * 3}try container.encode(timestamp, forKey: .timestamp)")
        lines.append(f"{INDENT * 3}try container.encode(payload, forKey: .payload)")
    lines.append(f"{INDENT * 2}}}")
    lines.append(f"{INDENT}}}")
    lines.append("")
    lines.append(f"{INDENT}public init(from decoder: Decoder) throws {{")
    lines.append(
        f"{INDENT * 2}let container = try decoder.container(keyedBy: CodingKeys.self)"
    )
    lines.append(
        f"{INDENT * 2}let type = try container.decode(String.self, forKey: .type)"
    )
    lines.append(
        f"{INDENT * 2}let seq = try container.decode(UInt32.self, forKey: .seq)"
    )
    lines.append(
        f"{INDENT * 2}let timestamp = try container.decode(UInt64.self, forKey: .timestamp)"
    )
    lines.append(f"{INDENT * 2}switch type {{")
    for spec in specs:
        lines.append(f"{INDENT * 2}case {_literal(spec.serial_name)}:")
        lines.append(
            f"{INDENT * 3}self = .{spec.case_ident}("
            f"seq: seq, timestamp: timestamp, payload: "
            f"try container.decode({spec.payload_class}.self, forKey: .payload))"
        )
    lines.append(f"{INDENT * 2}default:")
    lines.append(
        f"{INDENT * 3}throw ProtocolDecodingError.unknownMessageType("
        f"type: type, seq: seq, timestamp: timestamp)"
    )
    lines.append(f"{INDENT * 2}}}")
    lines.append(f"{INDENT}}}")
    lines.append("}")
    return lines


def _join_blocks(blocks: list[list[str]]) -> str:
    parts = ["\n".join(block) for block in blocks if block]
    return "\n\n".join(parts).rstrip() + "\n"


def _list_schema_sources(schema_root: Path) -> list[str]:
    return [
        p.relative_to(schema_root.parent.parent).as_posix()
        for p in sorted(schema_root.rglob("*.json"))
    ]


def render_models_file(
    state: GeneratorState, schema_sources: list[str]
) -> str:
    blocks: list[list[str]] = [_file_header(schema_sources)]
    for name in sorted(state.enums):
        blocks.append(_render_enum(state.enums[name]))
    for name in sorted(state.classes):
        blocks.append(_render_class(state.classes[name]))
    return _join_blocks(blocks)


def render_message_file(
    schema_sources: list[str],
    sealed_name: str,
    direction_label: str,
    specs: list[MessageSpec],
) -> str:
    return _join_blocks(
        [
            _file_header(schema_sources),
            _render_message_enum(sealed_name, specs, direction_label),
        ]
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Swift protocol DTOs from the LivaChat JSON Schema "
            "(packages/api-schema). Independent of the Kotlin generator."
        ),
    )
    parser.add_argument(
        "--schema",
        default="packages/api-schema",
        help="Path to the api-schema directory (default: packages/api-schema).",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help=f"Output directory for generated Swift files (default: {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Do not write files. Render in memory and exit non-zero if the "
            "on-disk generated files differ. Intended for CI."
        ),
    )
    return parser.parse_args(argv)


def _files_to_write(
    state: GeneratorState,
    client_specs: list[MessageSpec],
    server_specs: list[MessageSpec],
    schema_sources: list[str],
) -> dict[str, str]:
    return {
        OUTPUT_MODELS: render_models_file(state, schema_sources),
        OUTPUT_CLIENT: render_message_file(
            schema_sources, "ClientMessage", "Client-to-server", client_specs
        ),
        OUTPUT_SERVER: render_message_file(
            schema_sources, "ServerMessage", "Server-to-client", server_specs
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    schema_root = Path(args.schema).resolve()
    out_dir = Path(args.out).resolve()

    if not schema_root.is_dir():
        print(
            f"error: schema directory not found: {schema_root}",
            file=sys.stderr,
        )
        return 2

    common = _load_json(schema_root / "common.json")
    state = GeneratorState(
        schema_root=schema_root,
        common_defs=common.get("$defs", {}),
    )

    try:
        client_specs = _build_message_specs(state, "c2s")
        server_specs = _build_message_specs(state, "s2c")
        schema_sources = _list_schema_sources(schema_root)
        rendered = _files_to_write(
            state, client_specs, server_specs, schema_sources
        )
    except SchemaConflictError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        drift: list[str] = []
        for filename, expected in rendered.items():
            target = out_dir / filename
            if not target.exists():
                drift.append(f"{target}: missing")
                continue
            actual = target.read_text(encoding="utf-8")
            if actual != expected:
                drift.append(f"{target}: drift")
        if drift:
            print("generate_swift_api: drift detected", file=sys.stderr)
            for line in drift:
                print(f"  {line}", file=sys.stderr)
            print(
                "Run scripts/generate_swift_api.py to refresh.",
                file=sys.stderr,
            )
            return 1
        print("generate_swift_api: up to date")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in rendered.items():
        (out_dir / filename).write_text(content, encoding="utf-8")

    print("Generated:")
    print(
        f"  models -> {out_dir / OUTPUT_MODELS} "
        f"({len(state.enums)} enums, {len(state.classes)} structs)"
    )
    print(
        f"  client -> {out_dir / OUTPUT_CLIENT} "
        f"({len(client_specs)} messages)"
    )
    print(
        f"  server -> {out_dir / OUTPUT_SERVER} "
        f"({len(server_specs)} messages)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
