from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from shutil import which

from smoke_env import BASE_DIR, local_env

NPM = "npm.cmd" if sys.platform == "win32" else "npm"
ESLINT_BIN = BASE_DIR / "node_modules" / ".bin" / (
    "eslint.cmd" if sys.platform == "win32" else "eslint"
)

SMOKE_STEPS = [
    (
        "Agent documentation rule smoke tests",
        ["scripts/check_agent_docs_rules.py", "--quiet"],
    ),
    ("Agent documentation shape smoke tests", ["scripts/check_agent_docs.py", "--quiet"]),
    (
        "Markdown local-link rule smoke tests",
        ["scripts/check_markdown_links_rules.py", "--quiet"],
    ),
    ("Markdown local-link smoke tests", ["scripts/check_markdown_links.py", "--quiet"]),
    (
        "Makefile target rule smoke tests",
        ["scripts/check_make_targets_rules.py", "--quiet"],
    ),
    ("Makefile target smoke tests", ["scripts/check_make_targets.py", "--quiet"]),
    (
        "Local smoke coverage rule smoke tests",
        ["scripts/check_local_smoke_rules.py", "--quiet"],
    ),
    (
        "Python syntax rule smoke tests",
        ["scripts/check_python_syntax_rules.py", "--quiet"],
    ),
    ("Python syntax smoke tests", ["scripts/check_python_syntax.py", "--quiet"]),
    ("Django system check", ["manage.py", "check"]),
    (
        "Migration graph rule smoke tests",
        ["scripts/check_migration_graph_rules.py", "--quiet"],
    ),
    ("Migration graph smoke tests", ["scripts/check_migration_graph.py", "--quiet"]),
    ("Migration dry run", ["manage.py", "makemigrations", "--check", "--dry-run"]),
    (
        "Management command discovery rule smoke tests",
        ["scripts/check_management_commands_rules.py", "--quiet"],
    ),
    (
        "Management command import smoke tests",
        ["scripts/check_management_commands.py", "--quiet"],
    ),
    (
        "Service import rule smoke tests",
        ["scripts/check_service_imports_rules.py", "--quiet"],
    ),
    ("Service import smoke tests", ["scripts/check_service_imports.py", "--quiet"]),
    (
        "Django template compile rule smoke tests",
        ["scripts/check_templates_rules.py", "--quiet"],
    ),
    ("Django template compile smoke tests", ["scripts/check_templates.py", "--quiet"]),
    (
        "Template layout rule smoke tests",
        ["scripts/check_template_layout_rules.py", "--quiet"],
    ),
    ("Template layout smoke tests", ["scripts/check_template_layout.py", "--quiet"]),
    (
        "Template URL rule smoke tests",
        ["scripts/check_template_urls_rules.py", "--quiet"],
    ),
    ("Template URL smoke tests", ["scripts/check_template_urls.py", "--quiet"]),
    ("URL configuration rule smoke tests", ["scripts/check_urls_rules.py", "--quiet"]),
    ("URL configuration smoke tests", ["scripts/check_urls.py", "--quiet"]),
    (
        "Static reference rule smoke tests",
        ["scripts/check_static_references_rules.py", "--quiet"],
    ),
    (
        "Static reference smoke tests",
        ["scripts/check_static_references.py", "--quiet"],
    ),
    (
        "Template accessibility rule smoke tests",
        ["scripts/check_template_accessibility_rules.py", "--quiet"],
    ),
    (
        "Template accessibility smoke tests",
        ["scripts/check_template_accessibility.py", "--quiet"],
    ),
    (
        "Template button rule smoke tests",
        ["scripts/check_template_buttons_rules.py", "--quiet"],
    ),
    (
        "Template button smoke tests",
        ["scripts/check_template_buttons.py", "--quiet"],
    ),
    (
        "Template id rule smoke tests",
        ["scripts/check_template_ids_rules.py", "--quiet"],
    ),
    ("Template id smoke tests", ["scripts/check_template_ids.py", "--quiet"]),
    ("CSS rule smoke tests", ["scripts/check_css_static_rules.py", "--quiet"]),
    ("CSS smoke tests", ["scripts/check_css_static.py", "--quiet"]),
    ("Mobile table rule smoke tests", ["scripts/check_table_mobile_rules.py", "--quiet"]),
    ("Mobile table smoke tests", ["scripts/check_table_mobile.py", "--quiet"]),
    ("Table header rule smoke tests", ["scripts/check_table_headers_rules.py", "--quiet"]),
    ("Table header smoke tests", ["scripts/check_table_headers.py", "--quiet"]),
    ("Template CSRF rule smoke tests", ["scripts/check_template_csrf_rules.py", "--quiet"]),
    ("Template CSRF smoke tests", ["scripts/check_template_csrf.py", "--quiet"]),
    (
        "Template drawer/dialog rule smoke tests",
        ["scripts/check_template_drawers_rules.py", "--quiet"],
    ),
    (
        "Template drawer/dialog smoke tests",
        ["scripts/check_template_drawers.py", "--quiet"],
    ),
    (
        "Template label rule smoke tests",
        ["scripts/check_template_labels_rules.py", "--quiet"],
    ),
    ("Template label smoke tests", ["scripts/check_template_labels.py", "--quiet"]),
    (
        "Template inline-style rule smoke tests",
        ["scripts/check_template_inline_styles_rules.py", "--quiet"],
    ),
    (
        "Template inline-style smoke tests",
        ["scripts/check_template_inline_styles.py", "--quiet"],
    ),
    (
        "Template link rule smoke tests",
        ["scripts/check_template_links_rules.py", "--quiet"],
    ),
    ("Template link smoke tests", ["scripts/check_template_links.py", "--quiet"]),
    (
        "Destructive action rule smoke tests",
        ["scripts/check_destructive_actions_rules.py", "--quiet"],
    ),
    (
        "Destructive action smoke tests",
        ["scripts/check_destructive_actions.py", "--quiet"],
    ),
    (
        "View export wrapper rule smoke tests",
        ["scripts/check_view_exports_rules.py", "--quiet"],
    ),
    ("View export smoke tests", ["scripts/check_view_exports.py", "--quiet"]),
    (
        "UI partial wrapper rule smoke tests",
        ["scripts/check_ui_partials_rules.py", "--quiet"],
    ),
    ("UI partial smoke tests", ["scripts/check_ui_partials.py", "--quiet"]),
    (
        "JavaScript syntax rule smoke tests",
        ["scripts/check_js_syntax_rules.py", "--quiet"],
    ),
    ("JavaScript syntax smoke tests", ["scripts/check_js_syntax.py", "--quiet"]),
    (
        "JavaScript DOM safety rule smoke tests",
        ["scripts/check_js_dom_safety_rules.py", "--quiet"],
    ),
    ("JavaScript DOM safety smoke tests", ["scripts/check_js_dom_safety.py", "--quiet"]),
    (
        "JavaScript accessibility rule smoke tests",
        ["scripts/check_js_accessibility_rules.py", "--quiet"],
    ),
    (
        "JavaScript accessibility smoke tests",
        ["scripts/check_js_accessibility.py", "--quiet"],
    ),
    (
        "JavaScript table-label rule smoke tests",
        ["scripts/check_js_table_labels_rules.py", "--quiet"],
    ),
    (
        "JavaScript table-label smoke tests",
        ["scripts/check_js_table_labels.py", "--quiet"],
    ),
    (
        "Secret-pattern rule smoke tests",
        ["scripts/check_secret_patterns_rules.py", "--quiet"],
    ),
    ("Secret-pattern smoke tests", ["scripts/check_secret_patterns.py", "--quiet"]),
    ("Doc drift rule smoke tests", ["scripts/check_doc_drift_rules.py", "--quiet"]),
    ("Doc drift check", ["scripts/check_doc_drift.py", "--quiet"]),
]
SMOKE_DESCRIPTION = [
    "Local smoke baseline:",
    "- checks agent documentation shape rules.",
    "- checks required agent documentation files and protocol anchors.",
    "- checks Markdown local-link warning rules.",
    "- checks local Markdown links in repository documentation.",
    "- checks Makefile target warning rules.",
    "- checks Makefile targets for focused repository smoke scripts.",
    "- checks local smoke includes focused repository smoke scripts.",
    "- checks Python syntax warning rules.",
    "- checks project Python source syntax.",
    "- validates Django settings/imports with manage.py check.",
    "- checks migration graph warning rules.",
    "- checks migration graph conflicts and dependencies without a database connection.",
    "- verifies no model migration is pending with makemigrations --check --dry-run.",
    "- checks management command discovery warning rules.",
    "- checks local management command imports.",
    "- checks service module discovery warning rules.",
    "- checks local service module imports.",
    "- checks Django template compile warning rules.",
    "- checks local Django template compilation.",
    "- checks template layout warning rules.",
    "- checks full-page templates use the shared base layout and page header pattern.",
    "- checks template URL warning rules.",
    "- checks literal template URL names.",
    "- checks URL configuration warning rules.",
    "- checks local URL configuration shape.",
    "- checks static reference warning rules.",
    "- checks literal template static references.",
    "- checks template accessibility warning rules.",
    "- checks icon-only actions, images, choice controls, and text/search inputs have accessible labels.",
    "- checks template button warning rules.",
    "- checks template buttons declare explicit, valid types.",
    "- checks template id warning rules.",
    "- checks literal template ids are unique within each template.",
    "- checks CSS rule warning rules.",
    "- checks static CSS for merge markers, balanced braces, and stable typography rules.",
    "- checks mobile table warning rules.",
    "- checks table-mobile cells have mobile labels.",
    "- checks table header warning rules.",
    "- checks template table headers declare scope.",
    "- checks template CSRF warning rules.",
    "- checks POST forms include CSRF tokens.",
    "- checks template drawer/dialog warning rules.",
    "- checks drawers and dialogs have accessible control markup.",
    "- checks template label warning rules.",
    "- checks label for targets match literal ids or rendered Django form fields.",
    "- checks template inline-style warning rules.",
    "- checks template visual styles stay in static CSS.",
    "- checks template link safety warning rules.",
    "- checks templates avoid javascript: hrefs and unsafe target=_blank links.",
    "- checks destructive action warning rules.",
    "- checks destructive POST controls require confirmation prompts.",
    "- checks view export wrapper warning rules.",
    "- checks view export architecture boundaries.",
    "- checks UI partial wrapper warning rules.",
    "- checks shared UI partial and template consistency boundaries.",
    "- checks JavaScript syntax warning rules.",
    "- checks static JavaScript syntax with node --check.",
    "- checks JavaScript DOM safety warning rules.",
    "- checks static JavaScript and templates for unsafe DOM HTML injection APIs.",
    "- checks JavaScript accessibility warning rules.",
    "- checks generated JavaScript checkbox/radio controls have accessible labels.",
    "- checks JavaScript generated-table warning rules.",
    "- checks generated JavaScript table cells have mobile labels or colspan.",
    "- checks changed files for obvious secret patterns.",
    "- runs npm JavaScript lint when node_modules is installed.",
    "- checks documentation drift warning rules.",
    "- runs warning-only documentation drift detection.",
    "Note: without local PostgreSQL credentials, Django may warn while checking migration history; this script still fails on real command errors.",
]


def run_step(label: str, args: Sequence[str], env: dict[str, str]) -> int:
    command = [sys.executable, *args]
    quiet_step = "--quiet" in args
    print(f"\n== {label} ==", flush=True)
    print(" ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=BASE_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.stdout:
        print(completed.stdout, end="", flush=True)
    if completed.stderr and (not quiet_step or completed.returncode):
        print(completed.stderr, end="", flush=True)
    return completed.returncode


def run_command_step(label: str, command: Sequence[str], env: dict[str, str]) -> int:
    print(f"\n== {label} ==", flush=True)
    print(" ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=BASE_DIR,
        env=env,
        check=False,
        text=True,
    )
    return completed.returncode


def maybe_run_js_lint(env: dict[str, str]) -> tuple[str, int] | None:
    if not ESLINT_BIN.exists():
        print(
            "\n== JavaScript lint =="
            "\nSkipping npm run lint:js because node_modules is not installed.",
            flush=True,
        )
        return None
    if which(NPM) is None:
        print(
            "\n== JavaScript lint =="
            "\nSkipping npm run lint:js because npm is not on PATH.",
            flush=True,
        )
        return None
    return ("JavaScript lint", run_command_step("JavaScript lint", [NPM, "run", "lint:js"], env))


def main() -> int:
    env = local_env()
    failures = []
    print("\n".join(SMOKE_DESCRIPTION), flush=True)
    for label, args in SMOKE_STEPS:
        returncode = run_step(label, args, env)
        if returncode:
            failures.append((label, returncode))

    js_lint_result = maybe_run_js_lint(env)
    if js_lint_result is not None:
        label, returncode = js_lint_result
        if returncode:
            failures.append((label, returncode))

    if failures:
        print("\nLocal smoke checks failed:")
        for label, returncode in failures:
            print(f"- {label}: exit {returncode}")
        return 1

    print("\nLocal smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
