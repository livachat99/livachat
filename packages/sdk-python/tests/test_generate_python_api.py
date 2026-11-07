from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_python_api.py"


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_python_api", GENERATOR_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GeneratePythonApiUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gen = load_generator()

    def test_nullable_required_ref_preserves_required_contract(self) -> None:
        state = self.gen.GeneratorState(
            schema_root=REPO_ROOT / "packages" / "api-schema",
            common_defs={
                "SessionId": {
                    "type": "string",
                    "pattern": "^sess_[a-z0-9]{18}$",
                }
            },
        )
        schema = {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {
                    "oneOf": [
                        {"type": "null"},
                        {"$ref": "../common.json#/$defs/SessionId"},
                    ]
                }
            },
            "additionalProperties": False,
        }

        self.gen.register_class(state, "ExamplePayload", schema)

        field = state.classes["ExamplePayload"].properties[0]
        self.assertTrue(field.required)
        self.assertEqual(field.py_type.render(), "SessionId | None")
        self.assertNotIn("default=None", "\n".join(self.gen._render_model(
            state.classes["ExamplePayload"]
        )))

    def test_optional_field_gets_none_default(self) -> None:
        state = self.gen.GeneratorState(
            schema_root=REPO_ROOT / "packages" / "api-schema",
            common_defs={},
        )
        schema = {
            "type": "object",
            "properties": {"hint": {"type": "string"}},
            "additionalProperties": False,
        }

        self.gen.register_class(state, "ExamplePayload", schema)
        rendered = "\n".join(self.gen._render_model(
            state.classes["ExamplePayload"]
        ))

        self.assertIn("hint: str | None = Field(default=None)", rendered)

    def test_keyword_property_is_aliased(self) -> None:
        state = self.gen.GeneratorState(
            schema_root=REPO_ROOT / "packages" / "api-schema",
            common_defs={},
        )
        schema = {
            "type": "object",
            "required": ["class"],
            "properties": {"class": {"type": "string"}},
            "additionalProperties": False,
        }

        self.gen.register_class(state, "KeywordPayload", schema)
        rendered = "\n".join(self.gen._render_model(
            state.classes["KeywordPayload"]
        ))

        self.assertIn("class_: str = Field(alias='class')", rendered)


if __name__ == "__main__":
    unittest.main()
