#!/usr/bin/env python
"""Smoke tests for Python syntax checking rules."""

from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import check_python_syntax


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class PythonSyntaxRuleTests(unittest.TestCase):
    def test_valid_python_passes_compile_loop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            script = base_dir / "scripts" / "valid.py"
            script.parent.mkdir()
            script.write_text("def ok():\n    return 1\n", encoding="utf-8")

            with patch.object(check_python_syntax, "BASE_DIR", base_dir):
                with patch.object(check_python_syntax, "SOURCE_DIRS", ["scripts"]):
                    with redirect_stdout(io.StringIO()):
                        result = check_python_syntax.main(quiet=True)

            self.assertEqual(result, 0)

    def test_syntax_error_fails_compile_loop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            script = base_dir / "scripts" / "invalid.py"
            script.parent.mkdir()
            script.write_text("def broken(:\n    pass\n", encoding="utf-8")

            with patch.object(check_python_syntax, "BASE_DIR", base_dir):
                with patch.object(check_python_syntax, "SOURCE_DIRS", ["scripts"]):
                    with redirect_stdout(io.StringIO()):
                        result = check_python_syntax.main(quiet=True)

            self.assertEqual(result, 1)

    def test_cache_paths_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            good = base_dir / "scripts" / "valid.py"
            ignored = base_dir / "scripts" / "__pycache__" / "bad.py"
            good.parent.mkdir()
            ignored.parent.mkdir()
            good.write_text("value = 1\n", encoding="utf-8")
            ignored.write_text("def broken(:\n    pass\n", encoding="utf-8")

            with patch.object(check_python_syntax, "BASE_DIR", base_dir):
                with patch.object(check_python_syntax, "SOURCE_DIRS", ["scripts"]):
                    files = [
                        path.relative_to(base_dir).as_posix()
                        for path in check_python_syntax.python_files()
                    ]

            self.assertEqual(files, ["scripts/valid.py"])

    def test_source_dirs_limit_discovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            included = base_dir / "prices" / "models.py"
            excluded = base_dir / "scratch" / "notes.py"
            included.parent.mkdir()
            excluded.parent.mkdir()
            included.write_text("value = 1\n", encoding="utf-8")
            excluded.write_text("value = 2\n", encoding="utf-8")

            with patch.object(check_python_syntax, "BASE_DIR", base_dir):
                with patch.object(check_python_syntax, "SOURCE_DIRS", ["prices"]):
                    files = [
                        path.relative_to(base_dir).as_posix()
                        for path in check_python_syntax.python_files()
                    ]

            self.assertEqual(files, ["prices/models.py"])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PythonSyntaxRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
