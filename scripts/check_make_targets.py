#!/usr/bin/env python
"""Check that focused repository smoke scripts have matching Makefile targets."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
MAKEFILE = BASE_DIR / "Makefile"
SCRIPTS_DIR = BASE_DIR / "scripts"
TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?![=])")
PHONY_RE = re.compile(r"^\.PHONY:\s*(?P<targets>.+)$")

EXPECTED_TARGETS = {
    "agent-docs-rules": "scripts/check_agent_docs_rules.py",
    "agent-docs-smoke": "scripts/check_agent_docs.py",
    "command-rules": "scripts/check_management_commands_rules.py",
    "command-smoke": "scripts/check_management_commands.py",
    "css-rules": "scripts/check_css_static_rules.py",
    "css-smoke": "scripts/check_css_static.py",
    "destructive-action-rules": "scripts/check_destructive_actions_rules.py",
    "destructive-action-smoke": "scripts/check_destructive_actions.py",
    "doc-drift": "scripts/check_doc_drift.py",
    "doc-drift-rules": "scripts/check_doc_drift_rules.py",
    "js-a11y": "scripts/check_js_accessibility.py",
    "js-a11y-rules": "scripts/check_js_accessibility_rules.py",
    "js-dom-safety": "scripts/check_js_dom_safety.py",
    "js-dom-safety-rules": "scripts/check_js_dom_safety_rules.py",
    "js-rules": "scripts/check_js_syntax_rules.py",
    "js-smoke": "scripts/check_js_syntax.py",
    "js-table-labels": "scripts/check_js_table_labels.py",
    "js-table-labels-rules": "scripts/check_js_table_labels_rules.py",
    "local-smoke-rules": "scripts/check_local_smoke_rules.py",
    "local-smoke": "scripts/check_local_smoke.py",
    "make-target-rules": "scripts/check_make_targets_rules.py",
    "make-target-smoke": "scripts/check_make_targets.py",
    "markdown-link-rules": "scripts/check_markdown_links_rules.py",
    "markdown-link-smoke": "scripts/check_markdown_links.py",
    "migration-graph-rules": "scripts/check_migration_graph_rules.py",
    "migration-graph-smoke": "scripts/check_migration_graph.py",
    "python-rules": "scripts/check_python_syntax_rules.py",
    "python-smoke": "scripts/check_python_syntax.py",
    "secret-rules": "scripts/check_secret_patterns_rules.py",
    "secret-smoke": "scripts/check_secret_patterns.py",
    "service-rules": "scripts/check_service_imports_rules.py",
    "service-smoke": "scripts/check_service_imports.py",
    "static-ref-rules": "scripts/check_static_references_rules.py",
    "static-ref-smoke": "scripts/check_static_references.py",
    "table-mobile-rules": "scripts/check_table_mobile_rules.py",
    "table-mobile-smoke": "scripts/check_table_mobile.py",
    "table-header-rules": "scripts/check_table_headers_rules.py",
    "table-header-smoke": "scripts/check_table_headers.py",
    "template-a11y-rules": "scripts/check_template_accessibility_rules.py",
    "template-a11y-smoke": "scripts/check_template_accessibility.py",
    "template-button-rules": "scripts/check_template_buttons_rules.py",
    "template-button-smoke": "scripts/check_template_buttons.py",
    "template-id-rules": "scripts/check_template_ids_rules.py",
    "template-id-smoke": "scripts/check_template_ids.py",
    "template-csrf-rules": "scripts/check_template_csrf_rules.py",
    "template-csrf-smoke": "scripts/check_template_csrf.py",
    "template-drawer-rules": "scripts/check_template_drawers_rules.py",
    "template-drawer-smoke": "scripts/check_template_drawers.py",
    "template-label-rules": "scripts/check_template_labels_rules.py",
    "template-label-smoke": "scripts/check_template_labels.py",
    "template-inline-style-rules": "scripts/check_template_inline_styles_rules.py",
    "template-inline-style-smoke": "scripts/check_template_inline_styles.py",
    "template-link-rules": "scripts/check_template_links_rules.py",
    "template-link-smoke": "scripts/check_template_links.py",
    "template-rules": "scripts/check_templates_rules.py",
    "template-smoke": "scripts/check_templates.py",
    "template-layout-rules": "scripts/check_template_layout_rules.py",
    "template-layout-smoke": "scripts/check_template_layout.py",
    "template-url-rules": "scripts/check_template_urls_rules.py",
    "template-url-smoke": "scripts/check_template_urls.py",
    "ui-partial-rules": "scripts/check_ui_partials_rules.py",
    "ui-partial-smoke": "scripts/check_ui_partials.py",
    "url-rules": "scripts/check_urls_rules.py",
    "url-smoke": "scripts/check_urls.py",
    "view-export-rules": "scripts/check_view_exports_rules.py",
    "view-export-smoke": "scripts/check_view_exports.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def read_makefile() -> str:
    if not MAKEFILE.is_file():
        raise FileNotFoundError("Makefile is missing")
    return MAKEFILE.read_text(encoding="utf-8-sig")


def target_blocks(makefile_text: str) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current_target: str | None = None

    for line in makefile_text.splitlines():
        target_match = TARGET_RE.match(line)
        if target_match and not line.startswith("."):
            current_target = target_match.group(1)
            blocks[current_target] = [line]
            continue

        if current_target is not None:
            blocks[current_target].append(line)

    return {target: "\n".join(lines) for target, lines in blocks.items()}


def phony_targets(makefile_text: str) -> set[str]:
    targets: set[str] = set()
    for line in makefile_text.splitlines():
        match = PHONY_RE.match(line)
        if match:
            targets.update(match.group("targets").split())
    return targets


def check_script_paths(*, base_dir: Path = BASE_DIR) -> set[str]:
    scripts_dir = base_dir / "scripts"
    return {
        path.relative_to(base_dir).as_posix()
        for path in scripts_dir.glob("check_*.py")
        if path.is_file()
    }


def check_script_target_coverage(
    *,
    expected_targets: dict[str, str] | None = None,
    discovered_scripts: set[str] | None = None,
) -> list[str]:
    target_scripts = set((expected_targets or EXPECTED_TARGETS).values())
    scripts = discovered_scripts if discovered_scripts is not None else check_script_paths()

    failures = [
        f"missing Makefile target mapping for check script: {script_path}"
        for script_path in sorted(scripts - target_scripts)
    ]
    failures.extend(
        f"expected Makefile target script does not exist: {script_path}"
        for script_path in sorted(target_scripts - scripts)
    )
    return failures


def check_rule_pair_coverage(discovered_scripts: set[str] | None = None) -> list[str]:
    scripts = discovered_scripts if discovered_scripts is not None else check_script_paths()
    failures: list[str] = []

    for script_path in sorted(scripts):
        path = Path(script_path)
        stem = path.stem
        if stem.endswith("_rules"):
            base_script = path.with_name(f"{stem.removesuffix('_rules')}.py").as_posix()
            if base_script not in scripts:
                failures.append(
                    f"rule script has no matching checker script: {script_path}"
                )
            continue

        rule_script = path.with_name(f"{stem}_rules.py").as_posix()
        if rule_script not in scripts:
            failures.append(f"checker script has no matching rule script: {script_path}")

    return failures


def check_targets(makefile_text: str) -> list[str]:
    failures: list[str] = []
    blocks = target_blocks(makefile_text)
    phony = phony_targets(makefile_text)

    failures.extend(check_script_target_coverage())
    failures.extend(check_rule_pair_coverage())

    for target, script_path in sorted(EXPECTED_TARGETS.items()):
        block = blocks.get(target)
        if block is None:
            failures.append(f"missing Makefile target: {target}")
            continue
        if target not in phony:
            failures.append(f"Makefile target is not listed in .PHONY: {target}")
        if script_path not in block:
            failures.append(
                f"Makefile target {target!r} does not run expected script: {script_path}"
            )

    return failures


def main(*, quiet: bool = False) -> int:
    try:
        makefile_text = read_makefile()
    except OSError as exc:
        print(f"Makefile target check failed: {exc}")
        return 1

    failures = check_targets(makefile_text)
    if failures:
        print("\nMakefile target check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    if not quiet:
        print("Makefile target check:")
        print("- all scripts/check_*.py files are mapped to expected Makefile targets")
        print("- all scripts/check_*.py checker scripts have matching rule scripts")
        print("- expected focused smoke targets exist")
        print("- expected focused smoke targets are listed in .PHONY")
        print("- expected focused smoke targets run their matching scripts")
    print(f"\nMakefile target check passed for {len(EXPECTED_TARGETS)} target(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
