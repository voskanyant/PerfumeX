#!/usr/bin/env python
"""Check changed files for obvious committed secret patterns."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
IGNORED_PATH_PREFIXES = (
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "logs/",
    "media/",
    "node_modules/",
    "staticfiles/",
    "tmp/",
    "tmp_",
)
IGNORED_PATH_PARTS = {".cache", "__pycache__"}
IGNORED_EXACT_PATHS = {
    "scripts/check_secret_patterns_rules.py",
}
IGNORED_PATH_SUFFIXES = {
    ".7z",
    ".db",
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".png",
    ".pyc",
    ".pyd",
    ".pyo",
    ".sqlite3",
    ".webp",
    ".xlsx",
    ".zip",
}
TEXT_SUFFIXES = {
    ".cfg",
    ".cmd",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".sql",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TOKEN_ASSIGNMENT_RE = re.compile(
    r"(?i)['\"]?\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|token)\b['\"]?"
    r"\s*[:=]\s*['\"]?([^'\"\s#;,}]+)"
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
CRED_URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]+@")
TOKEN_VALUE_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
)
PLACEHOLDER_VALUES = {
    "",
    "0",
    "1",
    "changeme",
    "change-me",
    "dummy",
    "example",
    "false",
    "local",
    "none",
    "not-secret",
    "placeholder",
    "pass",
    "password",
    "bool",
    "dict",
    "float",
    "int",
    "list",
    "set",
    "str",
    "test",
    "true",
    "tuple",
}
PLACEHOLDER_TOKENS = ("dummy", "example", "local", "not-secret", "placeholder", "test")
REFERENCE_PREFIXES = (
    "%s",
    "csrf",
    "env(",
    "environ",
    "form.",
    "forms.",
    "getenv(",
    "os.getenv(",
    "os.environ",
    "request.",
    "self.",
    "settings.",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line_number: int
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print failures or the final no-finding summary.",
    )
    return parser.parse_args()


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or "git command failed", file=sys.stderr)
        return []
    return [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def changed_paths() -> set[str]:
    changed = set(git_lines("diff", "--name-only", "HEAD"))
    changed.update(git_lines("ls-files", "--others", "--exclude-standard"))
    return {path for path in changed if not is_ignored_path(path)}


def is_ignored_path(path: str) -> bool:
    parts = set(path.split("/"))
    return (
        path in IGNORED_EXACT_PATHS
        or path.startswith(IGNORED_PATH_PREFIXES)
        or bool(parts & IGNORED_PATH_PARTS)
        or Path(path).suffix.lower() in IGNORED_PATH_SUFFIXES
    )


def is_text_path(path: Path) -> bool:
    if path.name in {"Makefile", "Dockerfile"}:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    if normalized in PLACEHOLDER_VALUES:
        return True
    return any(token in normalized for token in PLACEHOLDER_TOKENS)


def is_reference_value(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    return normalized.startswith(REFERENCE_PREFIXES)


def is_test_path(relative_path: str) -> bool:
    return "/tests/" in relative_path or relative_path.endswith("tests.py")


def find_secret_findings(relative_path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if PRIVATE_KEY_RE.search(line):
            findings.append(Finding(relative_path, line_number, "private key block"))
        if CRED_URL_RE.search(line):
            findings.append(Finding(relative_path, line_number, "credentialed URL"))
        for token_re in TOKEN_VALUE_RES:
            if token_re.search(line):
                findings.append(Finding(relative_path, line_number, "token value"))

        assignment = TOKEN_ASSIGNMENT_RE.search(line)
        if (
            assignment
            and not is_test_path(relative_path)
            and not is_placeholder(assignment.group(2))
            and not is_reference_value(assignment.group(2))
        ):
            findings.append(
                Finding(relative_path, line_number, f"secret assignment: {assignment.group(1)}")
            )
    return findings


def scan_changed_files() -> list[Finding]:
    findings: list[Finding] = []
    for relative_path in sorted(changed_paths()):
        path = BASE_DIR / relative_path
        if not path.is_file() or not is_text_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue
        findings.extend(find_secret_findings(relative_path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    findings = scan_changed_files()
    if findings:
        print("\nPotential secret patterns found:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.reason}")
        print("\nRemove real credentials from repository files before continuing.")
        return 1

    if not quiet:
        print("Secret-pattern check:")
        print("- scanned changed text files")
        print("- ignored runtime, cache, media, and binary paths")
    print("No secret patterns found.")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
