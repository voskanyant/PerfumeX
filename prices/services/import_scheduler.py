from __future__ import annotations

import shlex
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.urls import reverse_lazy
from django.utils import timezone

from prices import models


CRON_MARKER = "PERFUMEX_IMPORT_CRON"


@dataclass(frozen=True)
class ImportSchedulerActionResult:
    message_level: str
    message: str
    handled: bool = True


def runner_script_path() -> Path:
    base_dir = Path(settings.BASE_DIR)
    return base_dir.parent / "run_import_emails.sh"


def render_runner_script() -> str:
    base_dir = Path(settings.BASE_DIR)
    log_dir = base_dir / "logs"
    log_file = log_dir / "perfumex_email_import.log"
    python_bin = Path(sys.executable)
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            f"mkdir -p {shlex.quote(str(log_dir))}",
            f"exec >>{shlex.quote(str(log_file))} 2>&1",
            "echo \"=== START $(date '+%F %T') ===\"",
            f"cd {shlex.quote(str(base_dir))}",
            "if [ -f .env ]; then",
            "  set -a",
            "  . ./.env",
            "  set +a",
            "fi",
            f"{shlex.quote(str(python_bin))} manage.py import_emails",
            "rc=$?",
            "echo \"=== END $(date '+%F %T') rc=$rc ===\"",
            "exit $rc",
            "",
        ]
    )


def ensure_runner_script() -> Path:
    script_path = runner_script_path()
    content = render_runner_script()
    script_path.write_text(content, encoding="utf-8")
    current_mode = script_path.stat().st_mode
    script_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def read_crontab_lines() -> list[str]:
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").lower()
        if "no crontab" in stderr:
            return []
        raise RuntimeError(result.stderr.strip() or "Failed to read crontab.")
    text = result.stdout or ""
    return [line.rstrip("\n") for line in text.splitlines()]


def write_crontab_lines(lines: list[str]) -> None:
    payload = "\n".join(lines).strip("\n")
    if payload:
        payload = payload + "\n"
    subprocess.run(
        ["crontab", "-"],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )


def cron_minute_expression(interval_minutes: int) -> str:
    interval = max(int(interval_minutes or 5), 1)
    if interval <= 1:
        return "*"
    if interval <= 59:
        return f"*/{interval}"
    return "0"


def build_cron_line(script_path: Path, interval_minutes: int | None = None) -> str:
    settings_obj = models.ImportSettings.get_solo()
    interval = int(interval_minutes or settings_obj.interval_minutes or 5)
    timeout_seconds = max(1800, interval * 60)
    return (
        f"{cron_minute_expression(interval)} * * * * "
        "/usr/bin/flock -n /tmp/perfumex_import.lock "
        f"/usr/bin/timeout {timeout_seconds}s /bin/bash {shlex.quote(str(script_path))} "
        f"# {CRON_MARKER}"
    )


def install_import_scheduler_cron(
    *,
    settings_obj=None,
    ensure_runner_script_func=ensure_runner_script,
    read_crontab_lines_func=read_crontab_lines,
    write_crontab_lines_func=write_crontab_lines,
    build_cron_line_func=build_cron_line,
) -> None:
    settings_obj = settings_obj or models.ImportSettings.get_solo()
    script_path = ensure_runner_script_func()
    lines = [line for line in read_crontab_lines_func() if CRON_MARKER not in line]
    lines.append(build_cron_line_func(script_path, settings_obj.interval_minutes))
    write_crontab_lines_func(lines)


def remove_import_scheduler_cron(
    *,
    read_crontab_lines_func=read_crontab_lines,
    write_crontab_lines_func=write_crontab_lines,
) -> None:
    lines = [line for line in read_crontab_lines_func() if CRON_MARKER not in line]
    write_crontab_lines_func(lines)


def run_import_scheduler_action(
    action: str,
    *,
    install_func=install_import_scheduler_cron,
    remove_func=remove_import_scheduler_cron,
) -> ImportSchedulerActionResult:
    if action == "install_cron":
        try:
            install_func()
        except Exception as exc:
            return ImportSchedulerActionResult(
                "error",
                f"Failed to install scheduler: {exc}",
            )
        return ImportSchedulerActionResult(
            "success",
            "Scheduler installed (cron + runner script).",
        )

    if action == "remove_cron":
        try:
            remove_func()
        except Exception as exc:
            return ImportSchedulerActionResult(
                "error",
                f"Failed to remove scheduler: {exc}",
            )
        return ImportSchedulerActionResult(
            "success",
            "Scheduler cron entry removed.",
        )

    return ImportSchedulerActionResult("", "", handled=False)


def next_import_scheduler_run_at(settings_obj):
    if not getattr(settings_obj, "last_run_at", None):
        return None
    return settings_obj.last_run_at + timezone.timedelta(
        minutes=settings_obj.interval_minutes
    )


def get_cron_status(
    read_crontab_lines_func: Callable[[], list[str]] | None = None,
) -> dict:
    script_path = runner_script_path()
    expected_line = build_cron_line(script_path)
    settings_obj = models.ImportSettings.get_solo()
    now = timezone.now()
    next_run_at = next_import_scheduler_run_at(settings_obj)
    late_by_seconds = 0
    grace_seconds = max(300, int(settings_obj.interval_minutes or 5) * 60 // 2)
    if next_run_at:
        late_by_seconds = max(int((now - next_run_at).total_seconds()), 0)
    stale = bool(next_run_at and late_by_seconds > grace_seconds)
    log_path = Path(settings.BASE_DIR) / "logs" / "perfumex_email_import.log"
    read_lines = read_crontab_lines_func or read_crontab_lines
    try:
        lines = read_lines()
        cron_line = next((line for line in lines if CRON_MARKER in line), "")
        return {
            "supported": True,
            "installed": bool(cron_line),
            "line": cron_line,
            "expected_line": expected_line,
            "needs_reinstall": bool(cron_line and cron_line != expected_line),
            "script_path": str(script_path),
            "script_exists": script_path.exists(),
            "log_path": str(log_path),
            "stale": stale,
            "late_by_seconds": late_by_seconds,
            "late_by_minutes": late_by_seconds // 60,
        }
    except Exception as exc:
        return {
            "supported": False,
            "installed": False,
            "line": "",
            "expected_line": expected_line,
            "needs_reinstall": False,
            "script_path": str(script_path),
            "script_exists": script_path.exists(),
            "log_path": str(log_path),
            "stale": stale,
            "late_by_seconds": late_by_seconds,
            "late_by_minutes": late_by_seconds // 60,
            "error": str(exc),
        }


def build_import_settings_context(
    *,
    settings_obj=None,
    form_class=None,
    mailbox_options_func=None,
    supplier_options_func=None,
    next_run_func=next_import_scheduler_run_at,
    cron_status_func=get_cron_status,
    read_crontab_lines_func=read_crontab_lines,
) -> dict:
    settings_obj = settings_obj or models.ImportSettings.get_solo()
    if form_class is None:
        from prices.forms import ImportSettingsForm

        form_class = ImportSettingsForm

    mailboxes = (
        mailbox_options_func()
        if mailbox_options_func
        else models.Mailbox.objects.order_by("name")
    )
    suppliers = (
        supplier_options_func()
        if supplier_options_func
        else models.Supplier.objects.filter(is_active=True).order_by("name")
    )
    return {
        "form": form_class(instance=settings_obj),
        "settings_obj": settings_obj,
        "mailboxes": mailboxes,
        "suppliers": suppliers,
        "next_run_at": next_run_func(settings_obj),
        "cron_status": cron_status_func(
            read_crontab_lines_func=read_crontab_lines_func
        ),
        "import_section": "settings",
        "overview_url": reverse_lazy("prices:supplier_overview"),
        "detailed_logs_url": reverse_lazy("prices:import_detailed_logs"),
        "import_settings_url": reverse_lazy("prices:import_settings"),
    }


def run_import_settings_post_action(
    post_data,
    *,
    settings_func=None,
    form_class=None,
    scheduler_action_func=run_import_scheduler_action,
    manual_import_func=None,
) -> ImportSchedulerActionResult:
    action = post_data.get("action")
    scheduler_result = scheduler_action_func(action)
    if scheduler_result.handled:
        return scheduler_result

    if action == "run_now":
        if manual_import_func is None:
            from prices.services.email_import_runs import run_manual_email_import_action

            manual_import_func = run_manual_email_import_action
        return manual_import_func()

    settings_func = settings_func or models.ImportSettings.get_solo
    settings_obj = settings_func()
    if form_class is None:
        from prices.forms import ImportSettingsForm

        form_class = ImportSettingsForm

    form = form_class(post_data, instance=settings_obj)
    if form.is_valid():
        form.save()
        return ImportSchedulerActionResult("success", "Import settings updated.")
    return ImportSchedulerActionResult("error", "Please fix the errors and try again.")
