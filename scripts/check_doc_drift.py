#!/usr/bin/env python
"""Warn when code changes may need matching documentation updates.

This is intentionally lightweight and warning-only. It uses changed file paths
from git, including untracked files, to catch likely documentation drift before
the final task summary.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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
IGNORED_PATH_SUFFIXES = {".pyc", ".pyo", ".pyd"}

CHECK_DESCRIPTION = """\
Doc drift check: warning-only

This check compares changed/untracked paths with the focused repo docs that
normally travel with those changes:
- models/views/forms/services/management commands -> docs/REPO_MAP.md or docs/DOMAIN_MODEL.md
- templates/static UI -> docs/UI_DESIGN_SYSTEM.md
- catalog/import/assistant/alias/linking logic -> docs/DOMAIN_MODEL.md or docs/DECISIONS.md
- AGENTS.md -> related workflow, architecture, decision, task, or checklist docs

Warnings mean "review whether docs need a small durable update"; they do not
mean every code change must edit docs.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print detected warnings or the final no-warning summary.",
    )
    return parser.parse_args()


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
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
        path.startswith(IGNORED_PATH_PREFIXES)
        or bool(parts & IGNORED_PATH_PARTS)
        or Path(path).suffix in IGNORED_PATH_SUFFIXES
    )


def any_path(paths: set[str], predicate) -> bool:
    return any(predicate(path) for path in paths)


def is_local_python(path: str) -> bool:
    return path.endswith(".py") and path.split("/", 1)[0] in {
        "prices",
        "catalog",
        "assistant_core",
        "assistant_linking",
    }


def is_model_view_form_service(path: str) -> bool:
    if not is_local_python(path):
        return False
    name = path.rsplit("/", 1)[-1]
    return (
        name in {"models.py", "views.py", "forms.py"}
        or "/services/" in path
        or "/management/commands/" in path
    )


def is_ui_file(path: str) -> bool:
    if "/templates/" in path and path.endswith(".html"):
        return True
    if "/static/" in path and path.endswith((".css", ".js")):
        return True
    return False


def is_business_logic_file(path: str) -> bool:
    if not is_local_python(path):
        return False
    if path.startswith("catalog/"):
        return True
    if path.startswith("assistant_core/") and (
        "/services/" in path or path.endswith(("models.py", "views.py", "forms.py"))
    ):
        return True
    if path.startswith("assistant_linking/") and (
        "/services/" in path
        or "/management/commands/" in path
        or path.endswith(("models.py", "views.py", "forms.py"))
    ):
        return True
    if path.startswith("prices/management/commands/") and any(
        token in path
        for token in {
            "cbr",
            "email",
            "import",
            "link",
            "price",
            "rate",
            "repair",
            "supplier",
        }
    ):
        return True
    if path.startswith("prices/services/") and any(
        token in path
        for token in {
            "importer.py",
            "email_importer.py",
            "link_importer.py",
            "cbr_rates.py",
            "product_visibility.py",
        }
    ):
        return True
    return path in {"prices/models.py", "prices/views.py", "prices/forms.py"}


def main(*, quiet: bool = False) -> int:
    paths = changed_paths()
    warnings: list[str] = []

    touched_repo_or_domain = bool({"docs/REPO_MAP.md", "docs/DOMAIN_MODEL.md"} & paths)
    touched_ui = "docs/UI_DESIGN_SYSTEM.md" in paths
    touched_domain_or_decision = bool(
        {"docs/DOMAIN_MODEL.md", "docs/DECISIONS.md"} & paths
    )
    touched_agent_related = bool(
        {
            "docs/WORKING_RULES.md",
            "docs/REPO_MAP.md",
            "docs/DECISIONS.md",
            "docs/CODEX_TASKS.md",
            "docs/DRIFT_CHECKLIST.md",
        }
        & paths
    )

    if any_path(paths, is_model_view_form_service) and not touched_repo_or_domain:
        warnings.append(
            "Python models/views/forms/services/management commands changed, but neither docs/REPO_MAP.md "
            "nor docs/DOMAIN_MODEL.md changed. Confirm ownership/domain docs do not need updates."
        )

    if any_path(paths, is_ui_file) and not touched_ui:
        warnings.append(
            "Templates or static UI files changed, but docs/UI_DESIGN_SYSTEM.md did not. "
            "Confirm no durable UI pattern or rule changed."
        )

    if any_path(paths, is_business_logic_file) and not touched_domain_or_decision:
        warnings.append(
            "Business/catalog/import/assistant/alias/linking logic changed, but neither "
            "docs/DOMAIN_MODEL.md nor docs/DECISIONS.md changed. Confirm no durable rule "
            "or decision needs documenting."
        )

    if "AGENTS.md" in paths and not touched_agent_related:
        warnings.append(
            "AGENTS.md changed without related focused docs. Confirm docs/WORKING_RULES.md, "
            "docs/REPO_MAP.md, docs/DECISIONS.md, docs/CODEX_TASKS.md, or "
            "docs/DRIFT_CHECKLIST.md do not need matching updates."
        )

    if not quiet:
        print(CHECK_DESCRIPTION)
    if not paths:
        print("No changed files detected.")
        return 0

    if warnings:
        print("\nPotential documentation drift:")
        for warning in warnings:
            print(f"- {warning}")
        print(
            "\nThis check exits 0 intentionally. Review the warnings before finishing."
        )
    else:
        print("No documentation drift warnings.")

    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
