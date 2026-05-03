"""Run the local gate that mirrors GitHub CI before deploying main.

This is intentionally slower than local smoke checks. Use it before pushing to
main so deploy failures are found locally instead of after the GitHub run starts.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from smoke_env import local_env


PYTHON = sys.executable
NPX = "npx.cmd" if sys.platform == "win32" else "npx"


@dataclass(frozen=True)
class Step:
    name: str
    command: Sequence[str]
    required_executables: Sequence[str] = ()


STEPS = (
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


def _missing_executables(step: Step) -> list[str]:
    return [executable for executable in step.required_executables if shutil.which(executable) is None]


def run_step(step: Step) -> None:
    missing = _missing_executables(step)
    if missing:
        names = ", ".join(missing)
        raise SystemExit(f"{step.name}: missing executable(s): {names}. Run npm install first.")

    print(f"\n==> {step.name}")
    print("$ " + " ".join(step.command))
    subprocess.run(step.command, check=True, env=local_env())


def main() -> int:
    print("Running deploy gate. This mirrors the GitHub CI jobs required before deploy.")
    print("Use this before pushing to main; use local-smoke only for faster iteration.")
    for step in STEPS:
        run_step(step)
    print("\nDeploy gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
