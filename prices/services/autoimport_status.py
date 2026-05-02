from __future__ import annotations

import re
from collections.abc import Callable

from django.utils import timezone

from prices import models
from prices.services.import_scheduler import get_cron_status
from prices.services.supplier_board import format_local_datetime, short_relative_datetime


def parse_backlog_remaining(message: str) -> int:
    match = re.search(r"(\d+)", message or "")
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def build_autoimport_scan_status(
    get_cron_status_func: Callable[[], dict] | None = None,
) -> dict[str, object]:
    settings_obj = models.ImportSettings.get_solo()
    mailboxes = list(
        models.Mailbox.objects.filter(is_active=True)
        .prefetch_related("folder_cursors")
        .order_by("priority", "id")
    )
    cron_status = (get_cron_status_func or get_cron_status)()
    since = None
    if settings_obj.last_run_at:
        since = settings_obj.last_run_at - timezone.timedelta(minutes=5)
    backlog_qs = models.EmailAttachmentDiagnostic.objects.filter(
        supplier__isnull=True,
        reason_code=models.AttachmentReason.BACKLOG_REMAINING,
    )
    if since:
        backlog_qs = backlog_qs.filter(created_at__gte=since)
    backlog_items = list(backlog_qs.select_related("mailbox").order_by("-created_at", "-id")[:20])
    remaining = sum(parse_backlog_remaining(item.message) for item in backlog_items)
    latest_backlog = backlog_items[0] if backlog_items else None
    if remaining:
        mode_label = "Backlog catch-up"
        class_name = "is-warning"
        mode_note = f"Processing oldest messages first; {remaining} message(s) remain after the latest run."
    elif settings_obj.last_run_at:
        mode_label = "Live cursor scan"
        class_name = "is-success"
        mode_note = "Cron should scan only messages newer than each mailbox cursor."
    else:
        mode_label = "Not run yet"
        class_name = "is-neutral"
        mode_note = "No automatic mailbox scan has been recorded."
    mailbox_rows = []
    for mailbox in mailboxes:
        folder_cursors = [
            {
                "folder": cursor.folder,
                "last_uid": cursor.last_uid or 0,
                "last_checked": short_relative_datetime(cursor.last_checked_at)
                if cursor.last_checked_at
                else "Not checked",
                "last_checked_full": format_local_datetime(cursor.last_checked_at),
            }
            for cursor in sorted(
                mailbox.folder_cursors.all(),
                key=lambda item: (item.folder != "INBOX", item.folder.lower()),
            )
        ]
        mailbox_rows.append(
            {
                "name": mailbox.name,
                "last_checked": short_relative_datetime(mailbox.last_checked_at)
                if mailbox.last_checked_at
                else "Not checked",
                "last_checked_full": format_local_datetime(mailbox.last_checked_at),
                "inbox_uid": mailbox.last_inbox_uid or 0,
                "all_mail_uid": mailbox.last_all_mail_uid or 0,
                "folder_cursors": folder_cursors,
            }
        )
    return {
        "mode_label": mode_label,
        "class_name": class_name,
        "mode_note": mode_note,
        "last_run": short_relative_datetime(settings_obj.last_run_at)
        if settings_obj.last_run_at
        else "Never",
        "last_run_full": format_local_datetime(settings_obj.last_run_at),
        "next_target": format_local_datetime(cron_status.get("next_run_at")),
        "cron_status": cron_status,
        "remaining_backlog": remaining,
        "latest_backlog_mailbox": latest_backlog.mailbox.name
        if latest_backlog and latest_backlog.mailbox
        else "",
        "mailboxes": mailbox_rows,
        "cursor_note": (
            "Cursor means the saved last processed UID for each mailbox folder. Normal cron uses it to read only newer emails; "
            "supplier refresh/backfill uses supplier filters and date windows."
        ),
    }
