"""Run local deployment gates.

The default gate is intentionally fast for focused deploys. Use ``--full`` before
large merges, shared service changes, schema/import/deletion work, or batched
releases where the full safety gate is worth the extra time.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from smoke_env import local_env


PYTHON = sys.executable
NPX = "npx.cmd" if sys.platform == "win32" else "npx"
ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"


@dataclass(frozen=True)
class Step:
    name: str
    command: Sequence[str]
    required_executables: Sequence[str] = ()


FAST_STEPS = (
    Step("django: settings check", (PYTHON, "manage.py", "check")),
    Step(
        "migrations: generated files check",
        (PYTHON, "manage.py", "makemigrations", "--check", "--dry-run"),
    ),
    Step("migrations: plan", (PYTHON, "manage.py", "migrate", "--plan")),
    Step(
        "repo: Makefile target surface",
        (PYTHON, "scripts/check_make_targets.py", "--quiet"),
    ),
)

UI_STEPS = (
    Step("templates: syntax", (PYTHON, "scripts/check_templates.py")),
    Step("static: css references", (PYTHON, "scripts/check_css_static.py")),
    Step("static: js syntax", (PYTHON, "scripts/check_js_syntax.py")),
)

FULL_CORE_STEPS = (
    Step("lint: ruff", (PYTHON, "-m", "ruff", "check", ".")),
    Step("lint: eslint", (NPX, "eslint", "prices/static/prices/js"), (NPX,)),
    Step(
        "migrations: dry run",
        (PYTHON, "manage.py", "makemigrations", "--check", "--dry-run"),
    ),
    Step("migrations: plan", (PYTHON, "manage.py", "migrate", "--plan")),
    Step(
        "test: django suite",
        (PYTHON, "manage.py", "test", "--parallel=4", "--verbosity=1", "--noinput"),
    ),
    Step(
        "security: pip audit",
        (PYTHON, "-m", "pip_audit", "--strict", "-r", "requirements.txt"),
    ),
    Step("security: django deploy check", (PYTHON, "manage.py", "check", "--deploy")),
    Step(
        "security: bandit",
        (
            PYTHON,
            "-m",
            "bandit",
            "-r",
            "prices",
            "assistant_core",
            "assistant_linking",
            "catalog",
            "--severity-level",
            "high",
            "--exclude",
            "*/tests.py,*/tests/*",
        ),
    ),
)


def full_repo_script_steps() -> tuple[Step, ...]:
    return tuple(
        Step(f"repo check: {path.name}", (PYTHON, f"scripts/{path.name}"))
        for path in sorted(SCRIPT_DIR.glob("check_*.py"))
    )


def targeted_test_step(test_labels: Sequence[str]) -> Step | None:
    clean_labels = tuple(label.strip() for label in test_labels if label.strip())
    if not clean_labels:
        return None
    return Step(
        "test: targeted django tests",
        (PYTHON, "manage.py", "test", *clean_labels, "--verbosity=1", "--noinput"),
    )


def _missing_executables(step: Step) -> list[str]:
    return [
        executable
        for executable in step.required_executables
        if shutil.which(executable) is None
    ]


def run_step(step: Step) -> None:
    missing = _missing_executables(step)
    if missing:
        names = ", ".join(missing)
        raise SystemExit(
            f"{step.name}: missing executable(s): {names}. Run npm install first."
        )

    print(f"\n==> {step.name}", flush=True)
    print("$ " + " ".join(step.command), flush=True)
    subprocess.run(step.command, check=True, env=local_env())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PerfumeX deployment gates.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run the full safety gate instead of the fast deploy gate.",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Include template/CSS/JS syntax checks for UI changes.",
    )
    parser.add_argument(
        "--test",
        action="extend",
        nargs="+",
        default=[],
        metavar="LABEL",
        help="Add targeted Django test labels. Can be passed more than once.",
    )
    return parser


def build_steps(args: argparse.Namespace) -> tuple[Step, ...]:
    if args.full:
        return FULL_CORE_STEPS + full_repo_script_steps()

    steps = list(FAST_STEPS)
    if args.ui:
        steps.extend(UI_STEPS)
    test_step = targeted_test_step(args.test)
    if test_step:
        steps.append(test_step)
    return tuple(steps)


def main() -> int:
    args = build_parser().parse_args()
    steps = build_steps(args)
    if args.full:
        print(
            "Running full deploy gate: CI checks, full Django tests, and repo scripts.",
            flush=True,
        )
    else:
        print(
            "Running fast deploy gate: Django, migrations, and workflow checks.",
            flush=True,
        )
        print(
            "Add --ui for UI syntax checks and --test LABEL for targeted tests.",
            flush=True,
        )
        print(
            "Use --full before big merges or shared service/schema/import changes.",
            flush=True,
        )
    for step in steps:
        run_step(step)
    print("\nDeploy gate passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
