#!/usr/bin/env python3
"""Generate Kotlin DTOs for the LivaChat WebSocket protocol.

The JSON Schema files under ``packages/api-schema`` are the protocol SSOT.
This generator targets the project's wrapper shape (type/seq/timestamp/payload)
and emits three files:

    ProtocolModels.kt   - shared enums + every payload data class
    ClientMessage.kt    - sealed class ClientMessage + C2S subtypes
    ServerMessage.kt    - sealed class ServerMessage + S2C subtypes

Design notes (vs. earlier drafts; see the protocol review):

* Optional fields (i.e. not in ``required``) are emitted as nullable Kotlin
  types with ``= null`` defaults. This is the only protocol-safe mapping for
  kotlinx.serialization: missing JSON fields stay distinguishable from
  present-but-empty ones, and serialization with ``explicitNulls = false``
  omits them on the wire.
* ``required`` + nullable schemas (``oneOf: [null, $ref]``) become
  ``val x: T?`` *without* a default, preserving the "must be present" contract.
* Enum detection is purely schema-driven (``type == "string" && enum``); no
  hand-maintained allow/deny list.
* Payload classes are namespaced by direction (``ClientPingPayload`` vs.
  ``ServerPingPayload``) so two messages that happen to share a type name
  cannot silently collapse.
* Each message gets ``@SerialName(<const>)`` and the sealed class is annotated
  with ``@JsonClassDiscriminator("type")`` so consumers do not have to rely on
  the kotlinx default.
* JSON Schema ``description`` is preserved as KDoc on classes, properties,
  and enums.
* ``--check`` mode compares the rendered output against the on-disk files and
  exits non-zero on drift (intended for CI / pre-commit).

Consumer Json configuration contract (must be respected by every SDK call
site)::

    val json = Json {
        classDiscriminator = "type"   // matches @JsonClassDiscriminator
        ignoreUnknownKeys = true      // session.start.config has
                                      // additionalProperties: true
        explicitNulls = false         // omit null optionals on the wire
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PACKAGE = "dev.livachat.protocol.generated"

OUTPUT_MODELS = "ProtocolModels.kt"
OUTPUT_CLIENT = "ClientMessage.kt"
OUTPUT_SERVER = "ServerMessage.kt"

INT32_MAX = 2_147_483_647

# ---------------------------------------------------------------------------
# Type model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KotlinType:
    """A reference to a Kotlin type, optionally nullable."""

    name: str
    nullable: bool = False

    def render(self) -> str:
        return f"{self.name}?" if self.nullable else self.name

    def with_nullable(self, nullable: bool) -> "KotlinType":
        if nullable == self.nullable:
            return self
        return KotlinType(self.name, nullable)


@dataclass(frozen=True)
class PropertySpec:
    json_name: str
    kotlin_name: str
    kotlin_type: KotlinType
    required: bool
    description: str = ""


@dataclass
class ClassSpec:
    name: str
    properties: list[PropertySpec] = field(default_factory=list)
    description: str = ""
    allow_extras: bool = False  # additionalProperties: true


@dataclass(frozen=True)
class EnumValueSpec:
    json_value: str
    kotlin_name: str


@dataclass
class EnumSpec:
    name: str
    values: list[EnumValueSpec] = field(default_factory=list)
    description: str = ""


@dataclass(frozen=True)
class MessageSpec:
    direction: str           # "c2s" or "s2c"
    serial_name: str         # discriminator const, e.g. "session.start"
    class_name: str          # sealed-class member, e.g. "SessionStart"
    payload_class: str       # e.g. "ClientSessionStartPayload"
    description: str


@dataclass
class GeneratorState:
    schema_root: Path
    common_defs: dict[str, Any]
    enums: dict[str, EnumSpec] = field(default_factory=dict)
    classes: dict[str, ClassSpec] = field(default_factory=dict)


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


def enum_entry_name(value: str) -> str:
    """SCREAMING_SNAKE for arbitrary string enum values (handles ``asr.final``)."""
    chars: list[str] = []
    for ch in value:
        chars.append(ch.upper() if ch.isalnum() else "_")
    name = "".join(chars).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    if not name:
        return "UNKNOWN"
    if name[0].isdigit():
        return f"VALUE_{name}"
    return name


def _ref_tail(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _strip_payload_suffix(name: str) -> str:
    return name[:-len("Payload")] if name.endswith("Payload") else name


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
    """Return the non-null branch of a ``oneOf: [null, X]`` pattern."""
    for item in schema.get("oneOf", []):
        if item.get("type") != "null":
            return item
    return schema


def _is_const_oneof(schema: dict[str, Any]) -> bool:
    one_of = schema.get("oneOf")
    return bool(one_of) and all("const" in item for item in one_of)


def _integer_kotlin_type(schema: dict[str, Any]) -> str:
    maximum = schema.get("maximum")
    if isinstance(maximum, int) and maximum > INT32_MAX:
        return "Long"
    return "Int"


def _primitive_kotlin_type(schema: dict[str, Any]) -> str | None:
    t = schema.get("type")
    if t == "string":
        return "String"
    if t == "boolean":
        return "Boolean"
    if t == "number":
        return "Double"
    if t == "integer":
        return _integer_kotlin_type(schema)
    if t == "array":
        return None  # handled by caller (needs class_name_hint for items)
    return None


def _const_kotlin_type(const_value: Any) -> str:
    if isinstance(const_value, bool):
        return "Boolean"
    if isinstance(const_value, int):
        return "Int"
    if isinstance(const_value, float):
        return "Double"
    return "String"


# ---------------------------------------------------------------------------
# common.json resolution
# ---------------------------------------------------------------------------


def _resolve_common_def(state: GeneratorState, def_name: str) -> KotlinType:
    """Map a ``common.json#/$defs/<name>`` entry to a Kotlin type.

    Side-effect: registers the entry as an enum if it has ``type:string + enum``.
    """
    if def_name not in state.common_defs:
        # Unknown ref — fall back to String so we don't crash schema-evolution.
        return KotlinType("String")
    schema = state.common_defs[def_name]

    # String enum -> register and return the enum type
    if schema.get("type") == "string" and "enum" in schema:
        if def_name not in state.enums:
            values = [
                EnumValueSpec(json_value=v, kotlin_name=enum_entry_name(v))
                for v in schema["enum"]
            ]
            state.enums[def_name] = EnumSpec(
                name=def_name,
                values=_dedupe_enum_entries(values),
                description=schema.get("description", ""),
            )
        return KotlinType(def_name)

    # const -> primitive of the const's type
    if "const" in schema:
        return KotlinType(_const_kotlin_type(schema["const"]))

    # oneOf of consts (e.g. AudioDurationMs)
    if _is_const_oneof(schema):
        consts = [item["const"] for item in schema["oneOf"]]
        if all(isinstance(v, str) for v in consts):
            # If a $def name is given but values are strings, treat as enum.
            values = [
                EnumValueSpec(json_value=v, kotlin_name=enum_entry_name(v))
                for v in consts
            ]
            if def_name not in state.enums:
                state.enums[def_name] = EnumSpec(
                    name=def_name,
                    values=_dedupe_enum_entries(values),
                    description=schema.get("description", ""),
                )
            return KotlinType(def_name)
        if all(isinstance(v, bool) for v in consts):
            return KotlinType("Boolean")
        if all(isinstance(v, int) for v in consts):
            return KotlinType("Int")
        if all(isinstance(v, float) for v in consts):
            return KotlinType("Double")

    # integer enum (e.g. AudioSampleRate) -> Int/Long
    if schema.get("type") == "integer":
        return KotlinType(_integer_kotlin_type(schema))

    # transitive $ref (e.g. JpegQuality -> UInt8)
    if "$ref" in schema:
        return _resolve_common_def(state, _ref_tail(schema["$ref"]))

    primitive = _primitive_kotlin_type(schema)
    return KotlinType(primitive or "String")


# ---------------------------------------------------------------------------
# Property / class resolution
# ---------------------------------------------------------------------------


def _resolve_property_type(
    state: GeneratorState,
    schema: dict[str, Any],
    class_name_hint: str,
) -> KotlinType:
    """Resolve a property schema to a Kotlin type.

    ``class_name_hint`` is used when the property is an inline object/enum and
    we need to invent a class/enum name. For ``$ref``-ed shared definitions the
    hint is ignored.
    """
    nullable = _is_nullable_oneof(schema)
    inner = _non_null_branch(schema) if nullable else schema

    if "$ref" in inner:
        ref_target = _ref_tail(inner["$ref"])
        return _resolve_common_def(state, ref_target).with_nullable(nullable)

    # Inline string enum (none currently in the schema, but defensive).
    if inner.get("type") == "string" and "enum" in inner:
        enum_name = class_name_hint
        if enum_name not in state.enums:
            values = [
                EnumValueSpec(json_value=v, kotlin_name=enum_entry_name(v))
                for v in inner["enum"]
            ]
            state.enums[enum_name] = EnumSpec(
                name=enum_name,
                values=_dedupe_enum_entries(values),
                description=inner.get("description", ""),
            )
        return KotlinType(enum_name, nullable)

    # Inline const literal (rare at the property level)
    if "const" in inner:
        return KotlinType(_const_kotlin_type(inner["const"]), nullable)

    # oneOf with all-const branches at property level
    if _is_const_oneof(inner):
        consts = [item["const"] for item in inner["oneOf"]]
        if all(isinstance(v, int) for v in consts):
            return KotlinType("Int", nullable)
        if all(isinstance(v, str) for v in consts):
            return KotlinType("String", nullable)

    # Nested object -> register a child class
    if inner.get("type") == "object":
        register_class(state, class_name_hint, inner)
        return KotlinType(class_name_hint, nullable)

    # Array
    if inner.get("type") == "array":
        items = inner.get("items", {})
        item_hint = class_name_hint + "Item"
        item_type = _resolve_property_type(state, items, item_hint)
        return KotlinType(f"List<{item_type.render()}>", nullable)

    primitive = _primitive_kotlin_type(inner)
    return KotlinType(primitive or "String", nullable)


def register_class(
    state: GeneratorState,
    class_name: str,
    schema: dict[str, Any],
) -> None:
    """Register an object-shaped schema as a Kotlin data class.

    No-op if already registered (assumes the same name implies the same shape;
    the schema is the SSOT and class names are derived deterministically).
    """
    if class_name in state.classes:
        return

    required_fields: set[str] = set(schema.get("required", []))
    properties: list[PropertySpec] = []
    base_for_children = _strip_payload_suffix(class_name)

    for json_name, prop_schema in (schema.get("properties") or {}).items():
        child_hint = f"{base_for_children}{upper_camel(json_name)}"
        kotlin_type = _resolve_property_type(state, prop_schema, child_hint)
        is_required = json_name in required_fields
        # Optional fields are nullable with a null default — protocol-safe.
        if not is_required:
            kotlin_type = kotlin_type.with_nullable(True)

        properties.append(
            PropertySpec(
                json_name=json_name,
                kotlin_name=lower_camel(json_name),
                kotlin_type=kotlin_type,
                required=is_required,
                description=prop_schema.get("description", ""),
            )
        )

    allow_extras = schema.get("additionalProperties", False) is True

    state.classes[class_name] = ClassSpec(
        name=class_name,
        properties=properties,
        description=schema.get("description", ""),
        allow_extras=allow_extras,
    )


def _dedupe_enum_entries(values: Iterable[EnumValueSpec]) -> list[EnumValueSpec]:
    """Defensive: if two JSON values map to the same Kotlin name, suffix _N."""
    seen: dict[str, int] = {}
    out: list[EnumValueSpec] = []
    for v in values:
        name = v.kotlin_name
        if name in seen:
            seen[name] += 1
            name = f"{v.kotlin_name}_{seen[name]}"
        else:
            seen[name] = 1
        out.append(EnumValueSpec(json_value=v.json_value, kotlin_name=name))
    return out


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
    for path in _load_union_paths(state.schema_root, direction):
        schema = _load_json(path)
        type_const = schema["properties"]["type"]["const"]
        class_name = upper_camel(type_const)
        payload_class = f"{prefix}{class_name}Payload"
        payload_schema = schema["properties"]["payload"]
        register_class(state, payload_class, payload_schema)
        specs.append(
            MessageSpec(
                direction=direction,
                serial_name=type_const,
                class_name=class_name,
                payload_class=payload_class,
                description=schema.get("description", ""),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


INDENT = "    "


def _render_kdoc(text: str, indent: str = "") -> list[str]:
    if not text:
        return []
    # Collapse internal whitespace and split very long lines into chunks of ~96
    # characters for readability without being pedantic.
    paragraph = " ".join(text.split())
    words = paragraph.split(" ")
    line_limit = 96
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for w in words:
        if current and current_len + 1 + len(w) > line_limit:
            lines.append(" ".join(current))
            current = [w]
            current_len = len(w)
        else:
            if current:
                current_len += 1 + len(w)
                current.append(w)
            else:
                current = [w]
                current_len = len(w)
    if current:
        lines.append(" ".join(current))

    out = [f"{indent}/**"]
    for line in lines:
        out.append(f"{indent} * {line}")
    out.append(f"{indent} */")
    return out


def _file_header(package: str, source_files: list[str]) -> list[str]:
    return [
        "// Auto-generated by scripts/generate_kotlin_api.py.",
        "// DO NOT EDIT MANUALLY. Edit the JSON Schema in packages/api-schema/",
        "// and re-run the generator instead.",
        "//",
        "// Source files (SSOT):",
        *(f"//   - {s}" for s in source_files),
        "//",
        "// Consumer Json configuration contract:",
        "//   val json = Json {",
        "//       classDiscriminator = \"type\"   // matches @JsonClassDiscriminator",
        "//       ignoreUnknownKeys = true      // session.start.config has",
        "//                                     // additionalProperties: true",
        "//       explicitNulls = false         // omit null optionals on the wire",
        "//   }",
        "",
        "@file:Suppress(\"unused\", \"RedundantVisibilityModifier\")",
        "",
        f"package {package}",
        "",
        "import kotlinx.serialization.ExperimentalSerializationApi",
        "import kotlinx.serialization.SerialName",
        "import kotlinx.serialization.Serializable",
        "import kotlinx.serialization.json.JsonClassDiscriminator",
        "",
    ]


def _render_enum(enum: EnumSpec) -> list[str]:
    lines: list[str] = []
    lines.extend(_render_kdoc(enum.description))
    lines.append("@Serializable")
    lines.append(f"enum class {enum.name} {{")
    entries = []
    for v in enum.values:
        entries.append(f"{INDENT}@SerialName(\"{v.json_value}\") {v.kotlin_name}")
    lines.append(",\n".join(entries) + ("," if entries else ""))
    lines.append("}")
    return lines


def _render_class(spec: ClassSpec) -> list[str]:
    lines: list[str] = []

    # Combine schema description with the additionalProperties advisory.
    description = spec.description
    if spec.allow_extras:
        extras_note = (
            "Schema allows additionalProperties; unknown fields are dropped on "
            "deserialization. Configure Json with ignoreUnknownKeys = true."
        )
        description = f"{description}\n\n{extras_note}".strip()

    lines.extend(_render_kdoc(description))
    lines.append("@Serializable")

    if not spec.properties:
        # Empty payload (e.g. ping). Emit a singleton-ish empty class so
        # ``payload: {}`` deserializes cleanly.
        lines.append(f"class {spec.name}")
        return lines

    lines.append(f"data class {spec.name}(")
    rendered_props: list[str] = []
    for prop in spec.properties:
        block: list[str] = []
        block.extend(_render_kdoc(prop.description, indent=INDENT))
        prefix = ""
        if prop.json_name != prop.kotlin_name:
            prefix = f"@SerialName(\"{prop.json_name}\") "
        default = " = null" if not prop.required else ""
        block.append(
            f"{INDENT}{prefix}val {prop.kotlin_name}: "
            f"{prop.kotlin_type.render()}{default}"
        )
        rendered_props.append("\n".join(block))
    lines.append(",\n".join(rendered_props))
    lines.append(")")
    return lines


def _render_sealed_class(
    name: str,
    specs: list[MessageSpec],
) -> list[str]:
    lines: list[str] = [
        "@OptIn(ExperimentalSerializationApi::class)",
        "@Serializable",
        "@JsonClassDiscriminator(\"type\")",
        f"sealed class {name} {{",
        f"{INDENT}abstract val seq: Long",
        f"{INDENT}abstract val timestamp: Long",
    ]

    for spec in specs:
        lines.append("")
        if spec.description:
            lines.extend(_render_kdoc(spec.description, indent=INDENT))
        lines.append(f"{INDENT}@Serializable")
        lines.append(f"{INDENT}@SerialName(\"{spec.serial_name}\")")
        lines.append(f"{INDENT}data class {spec.class_name}(")
        lines.append(f"{INDENT}{INDENT}override val seq: Long,")
        lines.append(f"{INDENT}{INDENT}override val timestamp: Long,")
        lines.append(f"{INDENT}{INDENT}val payload: {spec.payload_class},")
        lines.append(f"{INDENT}) : {name}()")

    lines.append("}")
    return lines


# ---------------------------------------------------------------------------
# File assembly
# ---------------------------------------------------------------------------


def _join_blocks(blocks: list[list[str]]) -> str:
    parts: list[str] = []
    for block in blocks:
        if not block:
            continue
        parts.append("\n".join(block))
    return "\n\n".join(parts).rstrip() + "\n"


def _list_schema_sources(schema_root: Path) -> list[str]:
    files = sorted(
        p.relative_to(schema_root.parent.parent)
        for p in schema_root.rglob("*.json")
    )
    return [str(p) for p in files]


def render_models_file(
    package: str,
    state: GeneratorState,
    schema_sources: list[str],
) -> str:
    header = _file_header(package, schema_sources)

    blocks: list[list[str]] = [header]

    if state.enums:
        for name in sorted(state.enums):
            blocks.append(_render_enum(state.enums[name]))

    if state.classes:
        for name in sorted(state.classes):
            blocks.append(_render_class(state.classes[name]))

    return _join_blocks(blocks)


def render_sealed_file(
    package: str,
    sealed_name: str,
    specs: list[MessageSpec],
    schema_sources: list[str],
) -> str:
    header = _file_header(package, schema_sources)
    blocks: list[list[str]] = [header, _render_sealed_class(sealed_name, specs)]
    return _join_blocks(blocks)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Kotlin protocol DTOs from the LivaChat JSON Schema "
            "(packages/api-schema)."
        ),
    )
    parser.add_argument(
        "--schema",
        default="packages/api-schema",
        help="Path to the api-schema directory (default: packages/api-schema).",
    )
    parser.add_argument(
        "--out",
        default=(
            "packages/sdk-kotlin/src/main/kotlin/dev/livachat/"
            "protocol/generated"
        ),
        help="Output directory for the generated Kotlin files.",
    )
    parser.add_argument(
        "--package",
        default=DEFAULT_PACKAGE,
        help=f"Kotlin package name (default: {DEFAULT_PACKAGE}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Do not write any files. Render outputs in memory and exit "
            "non-zero if they differ from the on-disk files. Intended for CI."
        ),
    )
    return parser.parse_args(argv)


def _files_to_write(
    package: str, state: GeneratorState,
    client_specs: list[MessageSpec], server_specs: list[MessageSpec],
    schema_sources: list[str],
) -> dict[str, str]:
    return {
        OUTPUT_MODELS: render_models_file(package, state, schema_sources),
        OUTPUT_CLIENT: render_sealed_file(
            package, "ClientMessage", client_specs, schema_sources,
        ),
        OUTPUT_SERVER: render_sealed_file(
            package, "ServerMessage", server_specs, schema_sources,
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

    client_specs = _build_message_specs(state, "c2s")
    server_specs = _build_message_specs(state, "s2c")

    schema_sources = _list_schema_sources(schema_root)
    rendered = _files_to_write(
        args.package, state, client_specs, server_specs, schema_sources,
    )

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
            print("generate_kotlin_api: drift detected", file=sys.stderr)
            for line in drift:
                print(f"  {line}", file=sys.stderr)
            print(
                "Run scripts/generate_kotlin_api.py to refresh.",
                file=sys.stderr,
            )
            return 1
        print("generate_kotlin_api: up to date")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in rendered.items():
        (out_dir / filename).write_text(content, encoding="utf-8")

    summary = [
        f"  models  -> {out_dir / OUTPUT_MODELS} "
        f"({len(state.enums)} enums, {len(state.classes)} data classes)",
        f"  client  -> {out_dir / OUTPUT_CLIENT} "
        f"({len(client_specs)} messages)",
        f"  server  -> {out_dir / OUTPUT_SERVER} "
        f"({len(server_specs)} messages)",
    ]
    print("Generated:")
    for line in summary:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
