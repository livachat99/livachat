#!/usr/bin/env python3
"""Generate Pydantic v2 DTOs for the LivaChat WebSocket protocol.

The JSON Schema files under ``packages/api-schema`` are the protocol SSOT.
This generator targets the project's wrapper shape (type/seq/timestamp/payload)
and emits four files:

    protocol_models.py  - shared enums, type aliases, every payload model
    client_message.py   - ClientMessage discriminated union + C2S wrappers
    server_message.py   - ServerMessage discriminated union + S2C wrappers
    __init__.py         - re-exports for ergonomic imports

Design notes:

* Targets **Pydantic v2** and **Python 3.12+**. Uses ``X | None`` syntax,
  ``from __future__ import annotations`` for relaxed forward refs, and
  ``enum.StrEnum``.
* String-enum schema $defs (``type: "string"`` + ``enum``) and ``oneOf`` of
  string consts become real ``StrEnum`` subclasses, preserving JSON values
  as enum member values for round-trip safe serialization.
* Numeric ``oneOf`` of consts and the wrapper's ``const`` ``type`` field
  become ``typing.Literal[...]`` aliases — the literal value participates
  in the discriminated union.
* Numeric range / pattern constraints from ``common.json`` are preserved
  as ``Annotated[int, Field(ge=..., le=...)]`` and
  ``Annotated[str, StringConstraints(pattern=...)]`` aliases, so Pydantic
  enforces the wire-protocol contract at runtime.
* Optional fields (i.e. **not** in ``required``) become ``T | None = None``;
  required-but-nullable (``oneOf: [null, ...]``) become ``T | None`` with
  no default, preserving the "must be present" contract. The recommended
  serialization is ``model_dump_json(by_alias=True, exclude_unset=True)``,
  which mirrors the JSON Schema semantic exactly: required fields and any
  explicitly-set optional fields hit the wire; defaults stay implicit.
  ``exclude_none=True`` is **incorrect** here — it would drop required
  ``session_id: None`` on a fresh session.start.
* Payloads are namespaced by direction (``ClientPingPayload`` vs.
  ``ServerPingPayload``) so two messages that share a type name cannot
  silently collapse.
* The discriminated unions use Pydantic's
  ``Annotated[Union[...], Field(discriminator="type")]`` form and ship with
  pre-built ``TypeAdapter`` instances (``ClientMessageAdapter``,
  ``ServerMessageAdapter``) for hot-path parsing.
* JSON Schema ``description`` is preserved as docstrings on classes/enums
  and via ``Field(description=...)`` on properties.
* ``--check`` mode renders in memory and exits non-zero on drift, intended
  for CI / pre-commit.

Usage::

    python3 scripts/generate_python_api.py \\
        --schema packages/api-schema \\
        --out packages/sdk-python/src/livachat_protocol/generated

    # CI gate:
    python3 scripts/generate_python_api.py --check \\
        --out packages/sdk-python/src/livachat_protocol/generated

Consumer usage::

    from livachat_protocol.generated import (
        ClientMessage, ClientMessageAdapter,
        ServerMessage, ServerMessageAdapter,
        ClientSessionStart, ClientSessionStartPayload, ClientSessionStartConfig,
    )

    # Parse an incoming frame (str, bytes, or dict):
    msg = ServerMessageAdapter.validate_json(raw_text)
    match msg:
        case ServerStatus(payload=p):
            ...
        case ServerError(payload=p) if p.fatal:
            ...

    # Build an outgoing frame:
    out = ClientSessionStart(
        seq=1,
        timestamp=now_ms(),
        payload=ClientSessionStartPayload(
            session_id=None,
            config=ClientSessionStartConfig(
                enable_video=True,
                audio_sample_rate=24000,
                vad_sensitivity="medium",
                client_version="ios-1.0.0",
            ),
        ),
    )
    wire = out.model_dump_json(by_alias=True, exclude_unset=True)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PACKAGE = "livachat_protocol.generated"

OUTPUT_MODELS = "protocol_models.py"
OUTPUT_CLIENT = "client_message.py"
OUTPUT_SERVER = "server_message.py"
OUTPUT_INIT = "__init__.py"

# Python keywords / dunder collisions to avoid as field identifiers. We only
# alias when the JSON name itself would collide; otherwise we keep the JSON
# name verbatim (the schema is already snake_case Python-friendly).
_PY_RESERVED = {
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield", "match", "case",
}


# ---------------------------------------------------------------------------
# Type model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PyType:
    """A rendered Python type expression, optionally nullable."""

    expr: str
    nullable: bool = False

    def render(self) -> str:
        if not self.nullable:
            return self.expr
        # Union with None
        return f"{self.expr} | None"

    def with_nullable(self, nullable: bool) -> PyType:
        if nullable == self.nullable:
            return self
        return PyType(self.expr, nullable)


@dataclass(frozen=True)
class PropertySpec:
    json_name: str
    py_name: str
    py_type: PyType
    required: bool
    description: str = ""
    # Extra keyword args to pass to pydantic.Field(...) at the property site.
    # Used today for description only; reserved for future constraints
    # (min_length etc.) that can't be expressed via a shared alias.
    field_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassSpec:
    name: str
    properties: list[PropertySpec] = field(default_factory=list)
    description: str = ""
    allow_extras: bool = False  # additionalProperties: true


@dataclass(frozen=True)
class EnumValueSpec:
    json_value: str
    py_name: str


@dataclass
class EnumSpec:
    name: str
    values: list[EnumValueSpec] = field(default_factory=list)
    description: str = ""


@dataclass(frozen=True)
class AliasSpec:
    """A top-level ``Foo = SomeType`` alias rendered into protocol_models.py.

    ``imports`` lists symbols (from ``typing``, ``pydantic`` etc.) that the
    alias's RHS uses; the generator unions all alias imports into the file
    header so we don't accidentally emit dead imports.
    """

    name: str
    rhs: str
    description: str = ""
    imports: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MessageSpec:
    direction: str           # "c2s" or "s2c"
    serial_name: str         # discriminator const, e.g. "session.start"
    class_name: str          # wrapper class, e.g. "ClientSessionStart"
    payload_class: str       # e.g. "ClientSessionStartPayload"
    description: str


@dataclass
class GeneratorState:
    schema_root: Path
    common_defs: dict[str, Any]
    enums: dict[str, EnumSpec] = field(default_factory=dict)
    aliases: dict[str, AliasSpec] = field(default_factory=dict)
    classes: dict[str, ClassSpec] = field(default_factory=dict)
    # Set of common-def names currently being resolved; guards mutual recursion
    # in pathological schemas. None of our defs are recursive today, but the
    # SSOT may evolve.
    _resolving: set[str] = field(default_factory=set)


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


def snake_case(value: str) -> str:
    """Convert any string into snake_case (idempotent for already-snake input)."""
    tokens = _split_tokens(value)
    return "_".join(t.lower() for t in tokens) if tokens else value


def enum_entry_name(value: str) -> str:
    """SCREAMING_SNAKE_CASE for arbitrary string enum values.

    Handles dotted forms like ``"session.start"`` -> ``"SESSION_START"``.
    """
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
    return name[: -len("Payload")] if name.endswith("Payload") else name


def _py_field_name(json_name: str) -> str:
    """Map a JSON property name to a Python identifier.

    The schema is already snake_case-friendly; we only intervene on Python
    keywords (and built-in shadowing) by appending a trailing underscore,
    in which case Pydantic ``alias=`` preserves the original JSON name on
    the wire.
    """
    candidate = snake_case(json_name)
    if candidate in _PY_RESERVED:
        return f"{candidate}_"
    return candidate


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


# ---------------------------------------------------------------------------
# common.json resolution
# ---------------------------------------------------------------------------


def _register_string_enum(
    state: GeneratorState,
    name: str,
    values: list[str],
    description: str,
) -> None:
    if name in state.enums:
        return
    entries = _dedupe_enum_entries(
        [EnumValueSpec(json_value=v, py_name=enum_entry_name(v)) for v in values]
    )
    state.enums[name] = EnumSpec(name=name, values=entries, description=description)


def _register_alias(
    state: GeneratorState,
    name: str,
    rhs: str,
    description: str = "",
    imports: Iterable[str] = (),
) -> None:
    if name in state.aliases:
        return
    state.aliases[name] = AliasSpec(
        name=name,
        rhs=rhs,
        description=description,
        imports=frozenset(imports),
    )


def _int_constraints(schema: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if "minimum" in schema:
        out.append(f"ge={schema['minimum']}")
    if "maximum" in schema:
        out.append(f"le={schema['maximum']}")
    if "exclusiveMinimum" in schema:
        out.append(f"gt={schema['exclusiveMinimum']}")
    if "exclusiveMaximum" in schema:
        out.append(f"lt={schema['exclusiveMaximum']}")
    return out


def _string_constraints(schema: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if "pattern" in schema:
        # repr() yields a valid Python string literal with appropriate
        # quoting/escaping for any regex content.
        out.append(f"pattern={_py_str(schema['pattern'])}")
    # ``minLength: 0`` is trivially satisfied by every string; emitting the
    # constraint just adds noise without changing behaviour.
    min_len = schema.get("minLength")
    if isinstance(min_len, int) and min_len > 0:
        out.append(f"min_length={min_len}")
    if "maxLength" in schema:
        out.append(f"max_length={schema['maxLength']}")
    return out


def _py_str(value: str) -> str:
    """Render a Python string literal. Prefer raw strings for regex content."""
    if "\\" in value and '"' not in value:
        return f'r"{value}"'
    return repr(value)


def _resolve_common_def(state: GeneratorState, def_name: str) -> PyType:
    """Map a ``common.json#/$defs/<name>`` entry to a Python type expression.

    Side-effect: registers enums and constrained aliases lazily as we walk
    the def graph. Idempotent.
    """
    if def_name not in state.common_defs:
        # Unknown ref — fall back to ``str`` so we don't break schema evolution.
        return PyType("str")
    if def_name in state._resolving:
        # Cycle guard: emit a bare alias name; the alias will be registered
        # later in the call chain. Safe because Python annotations are
        # already string-deferred via __future__ annotations.
        return PyType(def_name)

    if def_name in state.aliases or def_name in state.enums:
        return PyType(def_name)

    schema = state.common_defs[def_name]
    state._resolving.add(def_name)
    try:
        py_type = _build_common_alias(state, def_name, schema)
    finally:
        state._resolving.discard(def_name)
    return py_type


def _build_common_alias(
    state: GeneratorState, def_name: str, schema: dict[str, Any]
) -> PyType:
    """Register the alias/enum for a common-def schema and return its name."""

    # ── 1. String enum -> StrEnum class
    if schema.get("type") == "string" and "enum" in schema:
        _register_string_enum(
            state, def_name, list(schema["enum"]), schema.get("description", "")
        )
        return PyType(def_name)

    # ── 2. Bare const -> Literal[value]
    if "const" in schema:
        const_value = schema["const"]
        rhs = f"Literal[{_render_literal_value(const_value)}]"
        _register_alias(
            state, def_name, rhs,
            description=schema.get("description", ""),
            imports={"Literal"},
        )
        return PyType(def_name)

    # ── 3. oneOf-of-consts
    if _is_const_oneof(schema):
        consts = [item["const"] for item in schema["oneOf"]]
        # All-string -> StrEnum so unknown values fail validation cleanly.
        if all(isinstance(v, str) for v in consts):
            _register_string_enum(
                state, def_name, consts, schema.get("description", "")
            )
            return PyType(def_name)
        # Numeric / boolean -> Literal alias.
        rendered = ", ".join(_render_literal_value(v) for v in consts)
        _register_alias(
            state, def_name, f"Literal[{rendered}]",
            description=schema.get("description", ""),
            imports={"Literal"},
        )
        return PyType(def_name)

    # ── 4. Transitive $ref (e.g. JpegQuality -> UInt8). We register the base
    # alias first, then layer this def's own constraints on top.
    if "$ref" in schema:
        base_type = _resolve_common_def(state, _ref_tail(schema["$ref"]))
        # Determine whether this def adds extra constraints worth materializing.
        constraints: list[str] = []
        imports: set[str] = set()
        # Heuristic: if the base type is a constrained int/str alias and this
        # def narrows the range, emit a new Annotated alias. We treat any
        # presence of constraint keywords as a narrowing.
        if any(k in schema for k in ("minimum", "maximum",
                                      "exclusiveMinimum", "exclusiveMaximum")):
            constraints.extend(_int_constraints(schema))
            if constraints:
                args = ", ".join(constraints)
                rhs = f"Annotated[int, Field({args})]"
                imports |= {"Annotated", "Field"}
                _register_alias(
                    state, def_name, rhs,
                    description=schema.get("description", ""),
                    imports=imports,
                )
                return PyType(def_name)
        if any(k in schema for k in ("pattern", "minLength", "maxLength")):
            constraints.extend(_string_constraints(schema))
            if constraints:
                args = ", ".join(constraints)
                rhs = f"Annotated[str, StringConstraints({args})]"
                imports |= {"Annotated", "StringConstraints"}
                _register_alias(
                    state, def_name, rhs,
                    description=schema.get("description", ""),
                    imports=imports,
                )
                return PyType(def_name)

        # No additional constraints — alias is a transparent re-export.
        _register_alias(
            state, def_name, base_type.expr,
            description=schema.get("description", ""),
        )
        return PyType(def_name)

    # ── 5. Constrained primitive (string with pattern, int with range, ...)
    t = schema.get("type")
    if t == "string":
        sc = _string_constraints(schema)
        if sc:
            args = ", ".join(sc)
            rhs = f"Annotated[str, StringConstraints({args})]"
            _register_alias(
                state, def_name, rhs,
                description=schema.get("description", ""),
                imports={"Annotated", "StringConstraints"},
            )
            return PyType(def_name)
        _register_alias(state, def_name, "str", schema.get("description", ""))
        return PyType(def_name)

    if t == "integer":
        ic = _int_constraints(schema)
        if "enum" in schema:
            # Integer enum -> Literal alias (preserves on-wire ints).
            consts = schema["enum"]
            rendered = ", ".join(_render_literal_value(v) for v in consts)
            _register_alias(
                state, def_name, f"Literal[{rendered}]",
                description=schema.get("description", ""),
                imports={"Literal"},
            )
            return PyType(def_name)
        if ic:
            args = ", ".join(ic)
            rhs = f"Annotated[int, Field({args})]"
            _register_alias(
                state, def_name, rhs,
                description=schema.get("description", ""),
                imports={"Annotated", "Field"},
            )
            return PyType(def_name)
        _register_alias(state, def_name, "int", schema.get("description", ""))
        return PyType(def_name)

    if t == "boolean":
        _register_alias(state, def_name, "bool", schema.get("description", ""))
        return PyType(def_name)

    if t == "number":
        _register_alias(state, def_name, "float", schema.get("description", ""))
        return PyType(def_name)

    # ── 6. Fallback: treat as Any so schema evolution can't break codegen.
    _register_alias(
        state, def_name, "Any", schema.get("description", ""),
        imports={"Any"},
    )
    return PyType(def_name)


def _render_literal_value(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _py_str(value)
    if value is None:
        return "None"
    return repr(value)


# ---------------------------------------------------------------------------
# Property / class resolution
# ---------------------------------------------------------------------------


def _resolve_property_type(
    state: GeneratorState,
    schema: dict[str, Any],
    class_name_hint: str,
) -> PyType:
    """Resolve a property schema to a Python type expression.

    ``class_name_hint`` is used when the property is an inline object/array
    and we need to invent a class name. For ``$ref``-ed shared definitions
    the hint is ignored.
    """
    nullable = _is_nullable_oneof(schema)
    inner = _non_null_branch(schema) if nullable else schema

    if "$ref" in inner:
        ref_target = _ref_tail(inner["$ref"])
        # If the property schema itself adds a constraint on top of the $ref
        # (e.g. JpegQuality's quality field overlays nothing here, but
        # something like ``{"$ref": "...UInt8", "maximum": 100}`` would),
        # we currently honor only the shared alias for clarity. The schema
        # repository's convention is to put narrowed types into common.json.
        return _resolve_common_def(state, ref_target).with_nullable(nullable)

    # Inline string enum (defensive; none present today)
    if inner.get("type") == "string" and "enum" in inner:
        _register_string_enum(
            state,
            class_name_hint,
            list(inner["enum"]),
            inner.get("description", ""),
        )
        return PyType(class_name_hint, nullable)

    # Inline const literal
    if "const" in inner:
        return PyType(
            f"Literal[{_render_literal_value(inner['const'])}]", nullable
        )

    # Inline oneOf of consts
    if _is_const_oneof(inner):
        consts = [item["const"] for item in inner["oneOf"]]
        rendered = ", ".join(_render_literal_value(v) for v in consts)
        return PyType(f"Literal[{rendered}]", nullable)

    # Inline nested object -> register a child class
    if inner.get("type") == "object":
        register_class(state, class_name_hint, inner)
        return PyType(class_name_hint, nullable)

    # Array
    if inner.get("type") == "array":
        items = inner.get("items", {})
        item_hint = class_name_hint + "Item"
        item_type = _resolve_property_type(state, items, item_hint)
        return PyType(f"list[{item_type.render()}]", nullable)

    # Primitive
    t = inner.get("type")
    if t == "string":
        # Inline string with constraints (defensive — most are in common.json)
        sc = _string_constraints(inner)
        if sc:
            return PyType(
                f"Annotated[str, StringConstraints({', '.join(sc)})]", nullable
            )
        return PyType("str", nullable)
    if t == "boolean":
        return PyType("bool", nullable)
    if t == "integer":
        ic = _int_constraints(inner)
        if ic:
            return PyType(
                f"Annotated[int, Field({', '.join(ic)})]", nullable
            )
        return PyType("int", nullable)
    if t == "number":
        return PyType("float", nullable)

    return PyType("Any", nullable)


def register_class(
    state: GeneratorState,
    class_name: str,
    schema: dict[str, Any],
) -> None:
    """Register an object-shaped schema as a Pydantic model.

    No-op if already registered (assumes the same name implies the same
    shape; class names are derived deterministically from the schema).

    Children are registered BEFORE the parent is added to the dict, so
    iterating ``state.classes`` in insertion order yields a topologically
    sorted emission order (children first).
    """
    if class_name in state.classes:
        return

    required_fields: set[str] = set(schema.get("required") or [])
    properties: list[PropertySpec] = []
    base_for_children = _strip_payload_suffix(class_name)

    for json_name, prop_schema in (schema.get("properties") or {}).items():
        child_hint = f"{base_for_children}{upper_camel(json_name)}"
        py_type = _resolve_property_type(state, prop_schema, child_hint)
        is_required = json_name in required_fields
        # Optional fields are rendered as ``T | None = None``. Combined with
        # ``model_dump_json(exclude_unset=True)`` at serialization time, this
        # cleanly maps to "absent on the wire" — see this module's docstring.
        if not is_required:
            py_type = py_type.with_nullable(True)

        properties.append(
            PropertySpec(
                json_name=json_name,
                py_name=_py_field_name(json_name),
                py_type=py_type,
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
    """Defensive: if two JSON values map to the same Python name, suffix _N."""
    seen: dict[str, int] = {}
    out: list[EnumValueSpec] = []
    for v in values:
        name = v.py_name
        if name in seen:
            seen[name] += 1
            name = f"{v.py_name}_{seen[name]}"
        else:
            seen[name] = 1
        out.append(EnumValueSpec(json_value=v.json_value, py_name=name))
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
        class_name = f"{prefix}{upper_camel(type_const)}"
        payload_class = f"{class_name}Payload"
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
DOCSTRING_WIDTH = 92


def _wrap_paragraph(text: str, width: int) -> list[str]:
    paragraph = " ".join(text.split())
    if not paragraph:
        return []
    words = paragraph.split(" ")
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for w in words:
        if current and current_len + 1 + len(w) > width:
            lines.append(" ".join(current))
            current = [w]
            current_len = len(w)
        elif current:
            current_len += 1 + len(w)
            current.append(w)
        else:
            current = [w]
            current_len = len(w)
    if current:
        lines.append(" ".join(current))
    return lines


def _render_docstring(text: str, indent: str = INDENT) -> list[str]:
    """Render text as a triple-quoted docstring placed at ``indent``."""
    if not text:
        return []
    width = max(20, DOCSTRING_WIDTH - len(indent))
    lines = _wrap_paragraph(text, width)
    if len(lines) == 1 and '"""' not in lines[0]:
        return [f'{indent}"""{lines[0]}"""']
    out = [f'{indent}"""{lines[0]}']
    for line in lines[1:]:
        out.append(f"{indent}{line}")
    out.append(f'{indent}"""')
    return out


def _file_header(source_files: list[str], extra_doc: str = "") -> list[str]:
    lines = [
        '"""Auto-generated by scripts/generate_python_api.py.',
        "",
        "DO NOT EDIT MANUALLY. Edit the JSON Schema in packages/api-schema/",
        "and re-run the generator instead.",
        "",
        "Source files (SSOT):",
        *(f"    - {s}" for s in source_files),
    ]
    if extra_doc:
        lines += ["", extra_doc]
    lines += ['"""', ""]
    lines += [
        "# ruff: noqa",
        "# mypy: ignore-errors",
        "from __future__ import annotations",
        "",
    ]
    return lines


def _render_models_file(
    state: GeneratorState, source_files: list[str]
) -> str:
    # Collect imports from aliases + classes/enums.
    typing_imports: set[str] = set()
    pydantic_imports: set[str] = {"BaseModel", "ConfigDict", "Field"}
    enum_imports: set[str] = set()

    for alias in state.aliases.values():
        for imp in alias.imports:
            if imp in {"Annotated", "Literal", "Any"}:
                typing_imports.add(imp)
            elif imp in {"Field", "StringConstraints"}:
                pydantic_imports.add(imp)

    if state.enums:
        enum_imports.add("StrEnum")

    # Class properties may also need Literal/Annotated for inline types.
    for cls in state.classes.values():
        for prop in cls.properties:
            expr = prop.py_type.expr
            if "Literal[" in expr:
                typing_imports.add("Literal")
            if "Annotated[" in expr:
                typing_imports.add("Annotated")
            if "StringConstraints(" in expr:
                pydantic_imports.add("StringConstraints")
            if expr == "Any" or "Any" in expr.split("["):
                typing_imports.add("Any")

    header_doc = (
        "This module contains the shared building blocks of the protocol:\n"
        "string/integer enums, constrained type aliases (Annotated[...]),\n"
        "and every payload model. The wrapper messages live in\n"
        "``client_message.py`` and ``server_message.py``."
    )
    out = _file_header(source_files, extra_doc=header_doc)

    # Standard library imports
    stdlib_lines: list[str] = []
    if enum_imports:
        stdlib_lines.append(f"from enum import {', '.join(sorted(enum_imports))}")
    if typing_imports:
        stdlib_lines.append(
            f"from typing import {', '.join(sorted(typing_imports))}"
        )
    if stdlib_lines:
        out.extend(stdlib_lines)
        out.append("")

    out.append(
        f"from pydantic import {', '.join(sorted(pydantic_imports))}"
    )
    out.append("")
    out.append("")  # extra blank line before first declaration

    sections: list[list[str]] = []

    # ── Enums (insertion order so referenced-first appear first)
    for name, enum in state.enums.items():
        sections.append(_render_enum(enum))

    # ── Aliases
    for name, alias in state.aliases.items():
        sections.append(_render_alias(alias))

    # ── Payload classes (already in topological order via insertion)
    for name, cls in state.classes.items():
        sections.append(_render_model(cls))

    out.append(_join_blocks(sections))
    return _finalize(out)


def _render_enum(enum: EnumSpec) -> list[str]:
    lines: list[str] = [f"class {enum.name}(StrEnum):"]
    if enum.description:
        lines.extend(_render_docstring(enum.description))
    for v in enum.values:
        lines.append(f'{INDENT}{v.py_name} = {_py_str(v.json_value)}')
    return lines


def _render_alias(alias: AliasSpec) -> list[str]:
    if alias.description:
        # Aliases can't carry a docstring; put it as a leading comment so the
        # intent survives ``help()``/IDE hover via the original schema.
        lines = [
            f"# {line}" for line in _wrap_paragraph(alias.description, 88)
        ]
        lines.append(f"{alias.name} = {alias.rhs}")
        return lines
    return [f"{alias.name} = {alias.rhs}"]


def _render_model(spec: ClassSpec) -> list[str]:
    lines: list[str] = [f"class {spec.name}(BaseModel):"]

    description = spec.description
    if spec.allow_extras:
        extras_note = (
            "Schema allows additionalProperties; unknown fields are preserved "
            "via Pydantic ``extra='allow'``."
        )
        description = f"{description}\n\n{extras_note}".strip() if description \
            else extras_note

    if description:
        lines.extend(_render_docstring(description))

    extra_setting = "allow" if spec.allow_extras else "forbid"
    lines.append(
        f'{INDENT}model_config = ConfigDict(extra="{extra_setting}", '
        f'populate_by_name=True)'
    )

    if not spec.properties:
        # Empty payload (e.g. ping) — Pydantic still needs *something* in
        # the body. The ConfigDict line above satisfies that.
        return lines

    lines.append("")
    for prop in spec.properties:
        _emit_property(lines, prop)

    return lines


def _emit_property(lines: list[str], prop: PropertySpec) -> None:
    type_expr = prop.py_type.render()
    field_kwargs: list[str] = []

    needs_alias = prop.py_name != prop.json_name
    if needs_alias:
        # Use ``alias`` for population-by-alias semantics. Combined with
        # ``populate_by_name=True`` on the model, instances can be constructed
        # using either the Python name or the JSON name.
        field_kwargs.append(f"alias={_py_str(prop.json_name)}")

    if prop.description:
        field_kwargs.append(f"description={_py_str(prop.description)}")

    if not prop.required:
        field_kwargs.insert(0, "default=None")

    if field_kwargs:
        rhs = f"Field({', '.join(field_kwargs)})"
        lines.append(f"{INDENT}{prop.py_name}: {type_expr} = {rhs}")
    else:
        # Required, no description, no alias — bare annotation.
        lines.append(f"{INDENT}{prop.py_name}: {type_expr}")


# ---------------------------------------------------------------------------
# Wrapper file rendering (ClientMessage / ServerMessage)
# ---------------------------------------------------------------------------


def _render_wrapper_file(
    union_name: str,
    adapter_name: str,
    specs: list[MessageSpec],
    source_files: list[str],
) -> str:
    payload_imports = sorted({s.payload_class for s in specs})
    # Determine which shared types are referenced by the wrappers themselves.
    # All wrappers use SeqNumber and Timestamp from common.json -> aliases.
    shared_imports = ["SeqNumber", "Timestamp"] + payload_imports

    extra_doc = (
        f"This module defines the {union_name} discriminated union and its\n"
        "per-type wrapper models. Use the pre-built ``"
        f"{adapter_name}`` for hot-path JSON parsing.\n"
        "\n"
        "Example::\n"
        "\n"
        f"    msg = {adapter_name}.validate_json(raw_text)\n"
        "    match msg:\n"
        "        ...\n"
    )
    out = _file_header(source_files, extra_doc=extra_doc)

    out.append("from typing import Annotated, Any, Literal, Union")
    out.append("")
    out.append("from pydantic import BaseModel, ConfigDict, Field, TypeAdapter")
    out.append("")
    out.append(
        "from .protocol_models import (\n"
        + "".join(f"    {name},\n" for name in shared_imports)
        + ")"
    )
    out.append("")
    out.append("")

    sections: list[list[str]] = []

    # Shared base class — pin the discriminator into ``__pydantic_fields_set__``
    # so ``model_dump_json(exclude_unset=True)`` keeps ``type`` on the wire.
    # The protocol *requires* every frame to carry ``type``, but Pydantic
    # considers a field with a class-level default to be "unset" unless the
    # caller passes it explicitly. ``model_post_init`` is the documented
    # Pydantic v2 hook for this kind of post-construction adjustment.
    sections.append([
        "class _MessageBase(BaseModel):",
        f'{INDENT}"""Internal base class. See module docstring for the rationale."""',
        f'{INDENT}model_config = ConfigDict(extra="forbid", populate_by_name=True)',
        "",
        f"{INDENT}def model_post_init(self, __context: Any) -> None:",
        f'{INDENT}{INDENT}# ``type`` carries a class-level default for ergonomic',
        f"{INDENT}{INDENT}# construction; force-mark it as set so the discriminator",
        f"{INDENT}{INDENT}# always reaches the wire under ``exclude_unset=True``.",
        f'{INDENT}{INDENT}self.__pydantic_fields_set__.add("type")',
    ])

    for spec in specs:
        sections.append(_render_wrapper(spec))

    sections.append(_render_union(union_name, adapter_name, specs))

    out.append(_join_blocks(sections))
    return _finalize(out)


def _render_wrapper(spec: MessageSpec) -> list[str]:
    lines: list[str] = [f"class {spec.class_name}(_MessageBase):"]
    if spec.description:
        lines.extend(_render_docstring(spec.description))
    lines.append(
        f'{INDENT}type: Literal[{_py_str(spec.serial_name)}] = '
        f'{_py_str(spec.serial_name)}'
    )
    lines.append(f"{INDENT}seq: SeqNumber")
    lines.append(f"{INDENT}timestamp: Timestamp")
    lines.append(f"{INDENT}payload: {spec.payload_class}")
    return lines


def _render_union(
    union_name: str, adapter_name: str, specs: list[MessageSpec]
) -> list[str]:
    members = ", ".join(s.class_name for s in specs)
    direction_word = union_name.replace("Message", "")
    lines: list[str] = [
        f"# Discriminated union of every {direction_word}-side message,",
        "# routed by the wire ``type`` field. Use the adapter below to parse.",
        f"{union_name} = Annotated[",
        f"{INDENT}Union[{members}],",
        f'{INDENT}Field(discriminator="type"),',
        "]",
        "",
        "# Pre-built TypeAdapter for parsing raw frames. Prefer the adapter over",
        f"# ``{union_name}.model_validate*`` because ``Annotated[Union[...], ...]``",
        "# is not itself a Pydantic model class.",
        f"{adapter_name}: TypeAdapter[{union_name}] = TypeAdapter({union_name})",
    ]
    return lines


# ---------------------------------------------------------------------------
# __init__.py rendering
# ---------------------------------------------------------------------------


def _render_init_file(
    state: GeneratorState,
    client_specs: list[MessageSpec],
    server_specs: list[MessageSpec],
    source_files: list[str],
) -> str:
    extra_doc = (
        "Public entry point. Re-exports every generated symbol so call sites\n"
        "can rely on stable ``from livachat_protocol.generated import X``\n"
        "imports without caring which sub-module produced ``X``."
    )
    out = _file_header(source_files, extra_doc=extra_doc)

    model_symbols: list[str] = []
    model_symbols.extend(sorted(state.enums.keys()))
    model_symbols.extend(sorted(state.aliases.keys()))
    model_symbols.extend(sorted(state.classes.keys()))

    out.append("from .protocol_models import (")
    for name in model_symbols:
        out.append(f"    {name},")
    out.append(")")
    out.append("")

    out.append("from .client_message import (")
    out.append("    ClientMessage,")
    out.append("    ClientMessageAdapter,")
    for spec in client_specs:
        out.append(f"    {spec.class_name},")
    out.append(")")
    out.append("")

    out.append("from .server_message import (")
    out.append("    ServerMessage,")
    out.append("    ServerMessageAdapter,")
    for spec in server_specs:
        out.append(f"    {spec.class_name},")
    out.append(")")
    out.append("")

    out.append("__all__ = [")
    all_symbols = [
        *model_symbols,
        "ClientMessage",
        "ClientMessageAdapter",
        *(s.class_name for s in client_specs),
        "ServerMessage",
        "ServerMessageAdapter",
        *(s.class_name for s in server_specs),
    ]
    for name in all_symbols:
        out.append(f'    "{name}",')
    out.append("]")

    return _finalize(out)


# ---------------------------------------------------------------------------
# File assembly
# ---------------------------------------------------------------------------


def _join_blocks(blocks: list[list[str]]) -> str:
    parts: list[str] = []
    for block in blocks:
        if not block:
            continue
        parts.append("\n".join(block))
    # Two blank lines between top-level declarations — PEP 8.
    return "\n\n\n".join(parts)


def _finalize(lines: list[str]) -> str:
    text = "\n".join(lines).rstrip() + "\n"
    # Collapse any accidental 4+ consecutive blank lines.
    while "\n\n\n\n" in text:
        text = text.replace("\n\n\n\n", "\n\n\n")
    return text


def _list_schema_sources(schema_root: Path) -> list[str]:
    repo_root = schema_root.parent.parent
    files = sorted(
        p.relative_to(repo_root) for p in schema_root.rglob("*.json")
    )
    return [str(p) for p in files]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Pydantic v2 protocol DTOs from the LivaChat JSON "
            "Schema (packages/api-schema)."
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
            "packages/sdk-python/src/livachat_protocol/generated"
        ),
        help="Output directory for the generated Python package.",
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
    state: GeneratorState,
    client_specs: list[MessageSpec],
    server_specs: list[MessageSpec],
    schema_sources: list[str],
) -> dict[str, str]:
    return {
        OUTPUT_MODELS: _render_models_file(state, schema_sources),
        OUTPUT_CLIENT: _render_wrapper_file(
            "ClientMessage", "ClientMessageAdapter",
            client_specs, schema_sources,
        ),
        OUTPUT_SERVER: _render_wrapper_file(
            "ServerMessage", "ServerMessageAdapter",
            server_specs, schema_sources,
        ),
        OUTPUT_INIT: _render_init_file(
            state, client_specs, server_specs, schema_sources,
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

    # Pre-resolve common defs that the generated wrappers reference directly
    # (the wrapper template embeds these names verbatim, bypassing the schema-
    # driven property resolver). Doing this up front guarantees they land in
    # protocol_models.py so the cross-module imports resolve.
    for required_def in ("SeqNumber", "Timestamp"):
        if required_def in state.common_defs:
            _resolve_common_def(state, required_def)

    client_specs = _build_message_specs(state, "c2s")
    server_specs = _build_message_specs(state, "s2c")

    schema_sources = _list_schema_sources(schema_root)
    rendered = _files_to_write(
        state, client_specs, server_specs, schema_sources,
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
            print("generate_python_api: drift detected", file=sys.stderr)
            for line in drift:
                print(f"  {line}", file=sys.stderr)
            print(
                "Run scripts/generate_python_api.py to refresh.",
                file=sys.stderr,
            )
            return 1
        print("generate_python_api: up to date")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in rendered.items():
        (out_dir / filename).write_text(content, encoding="utf-8")

    summary = [
        f"  models  -> {out_dir / OUTPUT_MODELS} "
        f"({len(state.enums)} enums, {len(state.aliases)} aliases, "
        f"{len(state.classes)} models)",
        f"  client  -> {out_dir / OUTPUT_CLIENT} "
        f"({len(client_specs)} messages)",
        f"  server  -> {out_dir / OUTPUT_SERVER} "
        f"({len(server_specs)} messages)",
        f"  init    -> {out_dir / OUTPUT_INIT}",
    ]
    print("Generated:")
    for line in summary:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
