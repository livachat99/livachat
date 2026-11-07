from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "scripts" / "generate_python_api.py"
SCHEMA_ROOT = REPO_ROOT / "packages" / "api-schema"


class GeneratePythonApiIntegrationTests(unittest.TestCase):
    def test_generator_writes_expected_files_and_check_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "generated"

            subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--schema",
                    str(SCHEMA_ROOT),
                    "--out",
                    str(out_dir),
                ],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

            for filename in (
                "protocol_models.py",
                "client_message.py",
                "server_message.py",
                "__init__.py",
            ):
                self.assertTrue((out_dir / filename).is_file(), filename)

            check = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--schema",
                    str(SCHEMA_ROOT),
                    "--out",
                    str(out_dir),
                    "--check",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(
                check.returncode,
                0,
                check.stderr + check.stdout,
            )
            self.assertIn("up to date", check.stdout)

    def test_checked_in_generated_files_are_current(self) -> None:
        out_dir = (
            REPO_ROOT
            / "packages"
            / "sdk-python"
            / "src"
            / "livachat_protocol"
            / "generated"
        )

        check = subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--schema",
                str(SCHEMA_ROOT),
                "--out",
                str(out_dir),
                "--check",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(check.returncode, 0, check.stderr + check.stdout)


if __name__ == "__main__":
    unittest.main()
