import email
import csv
import hashlib
import imaplib
import io
import re
import socket
import ssl
import time
from datetime import datetime, timezone as dt_timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr

from django.core.files.base import ContentFile
from django.utils import timezone

from django.db.models import F

from prices import models
from prices.services.importer import process_import_file


SUPPORTED_PRICE_EXTENSIONS = (".csv", ".xlsx", ".xls")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff")
NON_PRICE_FILENAME_TERMS = (
    "invoice",
    "report",
    "receipt",
    "reconciliation",
    "delivery",
    "statement",
    "сверка",
    "наклад",
    "накладная",
    "акт",
    "отчет",
    "счет",
)


def _mailbox_host_candidates(mailbox):
    host = (mailbox.host or "").strip()
    candidates = []
    if host:
        candidates.append(host)
    lowered = host.lower()
    # mail.ru family domains commonly use imap.mail.ru regardless of sender domain.
    if any(domain in lowered for domain in ("mail.ru", "inbox.ru", "list.ru", "bk.ru")):
        if "imap.mail.ru" not in {c.lower() for c in candidates}:
            candidates.append("imap.mail.ru")
    return candidates


def _decode_header(value):
    if not value:
        return ""
    decoded = decode_header(value)
    parts = []
    for text, encoding in decoded:
        if isinstance(text, bytes):
            parts.append(text.decode(encoding or "utf-8", errors="ignore"))
        else:
            parts.append(text)
    return "".join(parts)

def _get_part_filename(part):
    filename = part.get_filename()
    if filename:
        return _decode_header(filename)
    name = part.get_param("name", header="content-type")
    if name:
        return _decode_header(name)
    return ""


def _infer_extension(content_type):
    if not content_type:
        return ""
    content_type = content_type.lower()
    if content_type in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ):
        return ".xlsx"
    if content_type in ("application/vnd.ms-excel",):
        return ".xls"
    if content_type in ("text/csv", "application/csv"):
        return ".csv"
    if content_type in ("application/octet-stream",):
        return ".xlsx"
    return ""


def _filename_extension(filename: str) -> str:
    if "." not in (filename or ""):
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def _is_non_price_filename(filename: str, content_type: str) -> bool:
    lowered = (filename or "").lower()
    if (content_type or "").lower().startswith("image/"):
        return True
    if _filename_extension(lowered) in IMAGE_EXTENSIONS:
        return True
    return any(term in lowered for term in NON_PRICE_FILENAME_TERMS)


def _validate_spreadsheet_payload(filename: str, payload: bytes) -> tuple[bool, str]:
    extension = _filename_extension(filename)
    try:
        if extension == ".csv":
            sample = payload[:8192].decode("utf-8-sig", errors="ignore")
            list(csv.reader(io.StringIO(sample)))
            return True, ""
        if extension == ".xlsx":
            import openpyxl

            workbook = openpyxl.load_workbook(
                io.BytesIO(payload), data_only=True, read_only=True
            )
            try:
                if not workbook.sheetnames:
                    return False, "Workbook has no sheets."
            finally:
                workbook.close()
            return True, ""
        if extension == ".xls":
            import xlrd

            workbook = xlrd.open_workbook(file_contents=payload)
            if workbook.nsheets < 1:
                return False, "Workbook has no sheets."
            return True, ""
    except Exception as exc:
        return False, str(exc)
    return False, f"Unsupported file type: {extension or '-'}"


def _reason_from_error(error: str) -> str:
    lowered = (error or "").lower()
    if "mapping is missing" in lowered or "mapping must include" in lowered:
        return models.AttachmentReason.MAPPING_MISSING
    if "no data rows parsed" in lowered:
        return models.AttachmentReason.NO_ROWS_PARSED
    if "too few products" in lowered:
        return models.AttachmentReason.TOO_FEW_PRODUCTS
    if "missing exchange rate" in lowered:
        return models.AttachmentReason.MISSING_EXCHANGE_RATE
    return models.AttachmentReason.PROCESSING_ERROR

def _local_day_bounds(dt):
    """Return UTC bounds for the local calendar day of dt."""
    if not dt:
        return None, None
    local_dt = timezone.localtime(dt)
    day_start_local = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_local = day_start_local + timezone.timedelta(days=1)
    return day_start_local.astimezone(dt_timezone.utc), day_end_local.astimezone(dt_timezone.utc)


def _local_day_window_bounds(dt, window_days=0):
    """Return UTC bounds for local day expanded by +/- window_days."""
    if not dt:
        return None, None
    safe_window = max(int(window_days or 0), 0)
    local_dt = timezone.localtime(dt)
    day_start_local = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start_local = day_start_local - timezone.timedelta(days=safe_window)
    window_end_local = day_start_local + timezone.timedelta(days=safe_window + 1)
    return (
        window_start_local.astimezone(dt_timezone.utc),
        window_end_local.astimezone(dt_timezone.utc),
    )


def _match_pattern(value, pattern):
    if not pattern:
        return True
    return pattern.lower() in value.lower()


def _pick_rule(mailbox, from_addr, subject, filename, supplier_id=None):
    rules = models.SupplierMailboxRule.objects.filter(
        mailbox=mailbox, is_active=True
    ).select_related("supplier")
    if supplier_id:
        rules = rules.filter(supplier_id=supplier_id)
    for rule in rules:
        if not _match_pattern(from_addr, rule.from_pattern):
            continue
        if not _match_pattern(subject, rule.subject_pattern):
            continue
        if not _match_pattern(filename, rule.filename_pattern):
            continue
        return rule
    return None


def _match_supplier_fallback(
    supplier, from_addr, subject, filename, has_rules_for_mailbox: bool
):
    if not supplier:
        return False
    if has_rules_for_mailbox:
        return False
    if not supplier.from_address_pattern:
        return False
    if not _match_pattern(from_addr, supplier.from_address_pattern):
        return False
    if not _match_pattern(subject, supplier.price_subject_pattern):
        return False
    if not _match_pattern(filename, supplier.price_filename_pattern):
        return False
    return True


def _find_supplier_fallback(suppliers, from_addr, subject, filename):
    for candidate in suppliers:
        if not candidate.from_address_pattern:
            continue
        if not _match_pattern(from_addr, candidate.from_address_pattern):
            continue
        if not _match_pattern(subject, candidate.price_subject_pattern):
            continue
        if not _match_pattern(filename, candidate.price_filename_pattern):
            continue
        return candidate
    return None


def _connect_imap(mailbox, logger, select_folder="INBOX"):
    host_candidates = _mailbox_host_candidates(mailbox)
    for attempt in range(2):
        for host in host_candidates:
            try:
                client = imaplib.IMAP4_SSL(host, mailbox.port, timeout=45)
                client.login(mailbox.username, mailbox.password)
                if not _select_mailbox(client, select_folder):
                    raise RuntimeError(f"Could not select mailbox: {select_folder}")
                if (mailbox.host or "").strip().lower() != host.lower():
                    _log(logger, f"{mailbox.name}: connected via fallback host {host}.")
                return client
            except Exception as exc:
                _log(
                    logger,
                    f"IMAP connection failed for {mailbox.name} via {host} (attempt {attempt + 1}/2): {exc}",
                )
        time.sleep(1)
    return None


def _imap_search(client, mailbox, criteria, logger, selected_folder="INBOX"):
    if not client:
        return "NO", [], None
    for attempt in range(2):
        try:
            status, data = client.search(None, *criteria)
            return status, data, client
        except (
            imaplib.IMAP4.abort,
            imaplib.IMAP4.error,
            socket.timeout,
            ssl.SSLError,
            OSError,
            AttributeError,
        ) as exc:
            _log(logger, f"IMAP search error ({mailbox.name}): {exc}")
            if attempt == 0:
                client = _connect_imap(mailbox, logger, select_folder=selected_folder)
                if not client:
                    return "NO", [], None
                continue
            return "NO", [], client
    return "NO", [], client


def _imap_fetch(client, mailbox, msg_id, query, logger, selected_folder="INBOX"):
    if not client:
        return "NO", [], None
    for attempt in range(2):
        try:
            status, data = client.fetch(msg_id, query)
            return status, data, client
        except (
            imaplib.IMAP4.abort,
            imaplib.IMAP4.error,
            socket.timeout,
            ssl.SSLError,
            OSError,
            AttributeError,
        ) as exc:
            _log(logger, f"IMAP fetch error ({mailbox.name}): {exc}")
            if attempt == 0:
                client = _connect_imap(mailbox, logger, select_folder=selected_folder)
                if not client:
                    return "NO", [], None
                continue
            return "NO", [], client
    return "NO", [], client


def _extract_internaldate(meta):
    if not meta:
        return None
    pattern = re.compile(r'INTERNALDATE "([^"]+)"')
    for item in meta:
        if isinstance(item, tuple) and item and item[0]:
            text = item[0].decode(errors="ignore")
            match = pattern.search(text)
            if not match:
                continue
            raw = match.group(1)
            try:
                return datetime.strptime(raw, "%d-%b-%Y %H:%M:%S %z")
            except ValueError:
                return None
    return None


def _extract_header_date(meta):
    if not meta:
        return None
    for item in meta:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        raw_headers = item[1]
        if not raw_headers:
            continue
        try:
            headers = email.message_from_bytes(raw_headers)
            raw_date = headers.get("Date")
            if not raw_date:
                continue
            parsed = parsedate_to_datetime(raw_date)
            if parsed and timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            return parsed
        except Exception:
            continue
    return None


def _extract_raw_email_from_fetch(msg_data):
    if not msg_data:
        return None
    for item in msg_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _find_all_mail_folder(client):
    try:
        status, data = client.list()
    except Exception:
        return None
    if status != "OK" or not data:
        return None
    for row in data:
        if not row:
            continue
        text = row.decode(errors="ignore")
        if "\\All" not in text:
            continue
        # Common LIST row: (\HasNoChildren \All) "/" "[Gmail]/All Mail"
        quoted = re.findall(r'"([^"]*)"', text)
        if quoted:
            return quoted[-1]
        parts = text.rsplit(" ", 1)
        if len(parts) == 2:
            return parts[-1].strip('"')
    return None


def _find_archive_like_folder(client):
    """
    Find archive-like folder for non-Gmail providers (e.g. mail.ru).
    Prefer folders flagged as \\Archive, then common archive name patterns.
    """
    try:
        status, data = client.list()
    except Exception:
        return None
    if status != "OK" or not data:
        return None

    archive_candidates = []
    name_candidates = []
    for row in data:
        if not row:
            continue
        text = row.decode(errors="ignore")
        lowered = text.lower()
        quoted = re.findall(r'"([^"]*)"', text)
        folder_name = quoted[-1] if quoted else None
        if not folder_name:
            parts = text.rsplit(" ", 1)
            if len(parts) == 2:
                folder_name = parts[-1].strip('"')
        if not folder_name:
            continue
        if "\\Archive" in text:
            archive_candidates.append(folder_name)
            continue
        if any(token in lowered for token in ("archive", "архив", "all mail")):
            name_candidates.append(folder_name)
    if archive_candidates:
        return archive_candidates[0]
    if name_candidates:
        return name_candidates[0]
    return None


def _select_mailbox(client, folder_name):
    if not folder_name:
        return False
    candidates = [folder_name]
    trimmed = folder_name.strip()
    if trimmed.startswith('"') and trimmed.endswith('"'):
        candidates.append(trimmed[1:-1])
    else:
        candidates.append(f'"{trimmed}"')
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            status, _ = client.select(candidate)
        except Exception:
            continue
        if status == "OK":
            return True
    return False


def run_import(
    mailboxes,
    supplier_id=None,
    mark_seen=False,
    limit=0,
    max_bytes=20_000_000,
    max_seconds=None,
    logger=None,
    run_id=None,
    search_criteria="UNSEEN",
    since_date=None,
    before_date=None,
    from_filter=None,
    subject_filter=None,
    dedupe_same_day_only=False,
    dedupe_day_window=0,
    min_received_at=None,
    use_uid_cursor=False,
):
    socket.setdefaulttimeout(20)
    supplier = None
    if supplier_id:
        supplier = models.Supplier.objects.filter(id=supplier_id).first()
    fallback_suppliers = None
    if not supplier:
        fallback_suppliers = list(
            models.Supplier.objects.filter(is_active=True, from_address_pattern__gt="")
        )
    settings_obj = models.ImportSettings.get_solo()
    blacklist_terms = settings_obj.get_filename_blacklist()
    last_message = None
    timed_out = False
    start_time = time.monotonic()
    run_started = timezone.now()
    mailbox_names = ", ".join([mb.name for mb in mailboxes])
    supplier_stats: dict[int, dict[str, object]] = {}
    unmatched_samples: dict[tuple[str, str, str], int] = {}
    log_lines: list[str] = []
    max_log_lines = 20000
    unmatched_log_count = 0
    unmatched_log_limit = 25
    last_cancel_check = 0.0
    cancel_check_interval = 2.0
    is_canceled = False

    def _log_line(msg):
        stamp = timezone.localtime(timezone.now()).strftime("%H:%M:%S")
        log_lines.append(f"[{stamp}] {msg}")
        if len(log_lines) > max_log_lines:
            del log_lines[: len(log_lines) - max_log_lines]
        if run_id and (len(log_lines) <= 3 or len(log_lines) % 10 == 0):
            models.EmailImportRun.objects.filter(id=run_id).update(
                detailed_log="\n".join(log_lines)
            )
        if logger:
            logger(msg)

    def _short(value, limit=180):
        if value is None:
            return ""
        text = str(value).replace("\n", " ").replace("\r", " ").strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1]}…"

    def note(msg):
        nonlocal last_message
        last_message = msg
        _log_line(msg)

    def run_was_canceled() -> bool:
        nonlocal last_cancel_check, is_canceled
        if is_canceled or not run_id:
            return is_canceled
        now = time.monotonic()
        if now - last_cancel_check < cancel_check_interval:
            return False
        last_cancel_check = now
        status = (
            models.EmailImportRun.objects.filter(id=run_id)
            .values_list("status", flat=True)
            .first()
        )
        is_canceled = status == models.EmailImportStatus.CANCELED
        return is_canceled

    summary = {
        "processed_files": 0,
        "skipped_duplicates": 0,
        "matched_files": 0,
        "errors": 0,
        "timed_out": False,
        "attachments_seen": 0,
        "skipped_no_filename": 0,
        "skipped_no_payload": 0,
        "skipped_blacklist": 0,
        "skipped_unsupported_extension": 0,
        "messages_found": 0,
        "messages_scanned": 0,
        "remaining_backlog": 0,
        "price_candidates": 0,
        "skipped_files": 0,
        "failed_files": 0,
        "quarantined_files": 0,
    }
    if run_id:
        models.EmailImportRun.objects.filter(id=run_id).update(
            status=models.EmailImportStatus.RUNNING
        )
    timeout_label = "disabled" if not max_seconds else f"{int(max_seconds // 60)}m"
    _log_line(
        "Run started: "
        f"mailboxes={len(mailboxes)} supplier_id={supplier_id or '-'} "
        f"criteria={search_criteria} since={since_date or '-'} limit={limit or 'none'} "
        f"timeout={timeout_label}"
    )
    def check_timeout(context=""):
        nonlocal timed_out
        if not max_seconds:
            return
        if time.monotonic() - start_time <= max_seconds:
            return
        timed_out = True
        msg = f"Timed out after {int(max_seconds // 60)} min"
        if context:
            msg = f"{msg} ({context})"
        note(msg)
        if run_id:
            models.EmailImportRun.objects.filter(id=run_id).update(
                status=models.EmailImportStatus.FAILED,
                finished_at=timezone.now(),
                errors=F("errors") + 1,
                last_message=msg,
                detailed_log="\n".join(log_lines),
            )
        raise TimeoutError(msg)

    def record_diagnostic(
        *,
        decision,
        reason_code="",
        message="",
        mailbox=None,
        folder="",
        msg_id=None,
        email_message_id="",
        message_date=None,
        sender="",
        subject="",
        filename="",
        content_type="",
        payload=None,
        content_hash="",
        supplier_obj=None,
        batch=None,
        import_file=None,
    ):
        size_bytes = len(payload or b"")
        try:
            return models.EmailAttachmentDiagnostic.objects.create(
                run_id=run_id,
                supplier=supplier_obj,
                mailbox=mailbox,
                import_batch=batch,
                import_file=import_file,
                message_folder=folder or "",
                message_uid=_uid_display(msg_id) if msg_id is not None else "",
                message_id=email_message_id or "",
                message_date=message_date,
                sender=sender or "",
                subject=(subject or "")[:500],
                filename=filename or "",
                content_type=(content_type or "")[:200],
                size_bytes=size_bytes,
                content_hash=content_hash or "",
                decision=decision,
                reason_code=reason_code or "",
                message=message or "",
            )
        except Exception as exc:
            _log_line(f"Diagnostic write failed: {_short(exc, 160)}")
            return None

    client = None
    try:
        for mailbox in mailboxes:
            check_timeout("mailbox loop")
            if run_was_canceled():
                break
            if mailbox.protocol != models.Mailbox.IMAP:
                _log(_log_line, f"Skipping {mailbox.name}: only IMAP supported.")
                continue

            has_rules_for_mailbox = False
            if supplier:
                has_rules_for_mailbox = models.SupplierMailboxRule.objects.filter(
                    supplier=supplier, mailbox=mailbox, is_active=True
                ).exists()
            selected_folder = "INBOX"
            client = _connect_imap(mailbox, _log_line, select_folder=selected_folder)
            if not client:
                summary["errors"] += 1
                if run_id:
                    models.EmailImportRun.objects.filter(id=run_id).update(
                        errors=F("errors") + 1,
                        last_message=f"IMAP connection failed: {mailbox.name}",
                    )
                note(f"Skipping mailbox due connection failure: {mailbox.name}")
                continue
            criteria = [search_criteria]
            if from_filter:
                criteria.extend(["FROM", from_filter])
            if subject_filter:
                criteria.extend(["SUBJECT", subject_filter])
            if since_date:
                criteria.extend(["SINCE", since_date.strftime("%d-%b-%Y")])
            if before_date:
                criteria.extend(["BEFORE", before_date.strftime("%d-%b-%Y")])

            def search_folder(folder_name: str):
                nonlocal client, selected_folder
                if selected_folder != folder_name:
                    if not _select_mailbox(client, folder_name):
                        client = _connect_imap(mailbox, _log_line, select_folder=folder_name)
                        if not client:
                            summary["errors"] += 1
                            note(
                                f"Skipping folder due select failure: {mailbox.name}/{folder_name}"
                            )
                            return []
                    selected_folder = folder_name
                status, data, client = _imap_search(
                    client, mailbox, criteria, _log_line, selected_folder=folder_name
                )
                if status != "OK":
                    note(f"Failed to search mailbox folder: {mailbox.name}/{folder_name}")
                    return []
                return _sort_message_ids(data[0].split())

            work_items = []
            inbox_ids = search_folder("INBOX")
            note(f"{mailbox.name}: found {len(inbox_ids)} message(s) in INBOX.")
            work_items.extend(("INBOX", msg_id) for msg_id in inbox_ids)

            host_lower = (mailbox.host or "").lower()
            if "gmail.com" in host_lower:
                try:
                    detected_folder = _find_all_mail_folder(client)
                    folder_candidates = []
                    if detected_folder:
                        folder_candidates.append(detected_folder)
                    folder_candidates.extend(["[Gmail]/All Mail", "[Google Mail]/All Mail"])
                    seen = set()
                    all_mail_folder = None
                    for folder in folder_candidates:
                        if not folder or folder in seen:
                            continue
                        seen.add(folder)
                        if _select_mailbox(client, folder):
                            selected_folder = folder
                            all_mail_folder = folder
                            break
                    if all_mail_folder:
                        all_mail_ids = search_folder(all_mail_folder)
                        note(
                            f"{mailbox.name}: found {len(all_mail_ids)} message(s) in Gmail All Mail."
                        )
                        if all_mail_ids:
                            # All Mail includes INBOX for Gmail, so use it as the authoritative folder.
                            work_items = [(all_mail_folder, msg_id) for msg_id in all_mail_ids]
                            _log(_log_line, f"Using Gmail All Mail for {mailbox.name}.")
                    else:
                        _log(_log_line, f"Gmail All Mail folder not accessible for {mailbox.name}.")
                except Exception as exc:
                    _log(_log_line, f"Failed Gmail All Mail fallback ({mailbox.name}): {exc}")
            elif any(domain in host_lower for domain in ("mail.ru", "inbox.ru", "list.ru", "bk.ru")):
                try:
                    archive_folder = _find_archive_like_folder(client)
                    if archive_folder:
                        archive_ids = search_folder(archive_folder)
                        if archive_ids:
                            existing_keys = {
                                (folder, _uid_display(msg_id)) for folder, msg_id in work_items
                            }
                            for msg_id in archive_ids:
                                key = (archive_folder, _uid_display(msg_id))
                                if key not in existing_keys:
                                    work_items.append((archive_folder, msg_id))
                            note(
                                f"{mailbox.name}: found {len(archive_ids)} message(s) in archive folder '{archive_folder}'."
                            )
                except Exception as exc:
                    _log(_log_line, f"mail.ru archive fallback failed ({mailbox.name}): {exc}")

            work_items = sorted(
                work_items,
                key=lambda item: (
                    0 if item[0] == "INBOX" else 1,
                    _uid_to_int(item[1]) or 0,
                    _uid_display(item[1]),
                ),
            )
            summary["messages_found"] += len(work_items)
            if use_uid_cursor and not supplier and not from_filter and not subject_filter:
                filtered_items = []
                counts_by_folder = {}
                for folder_name, msg_id in work_items:
                    cursor_field = _folder_cursor_field(folder_name)
                    last_uid = getattr(mailbox, cursor_field, 0) or 0
                    uid_int = _uid_to_int(msg_id)
                    if uid_int is None or uid_int <= last_uid:
                        continue
                    filtered_items.append((folder_name, msg_id))
                    counts_by_folder[folder_name] = counts_by_folder.get(folder_name, 0) + 1
                details = ", ".join(
                    f"{folder}={count}" for folder, count in sorted(counts_by_folder.items())
                ) or "none"
                note(f"{mailbox.name}: new by UID cursor {details}.")
                work_items = filtered_items
            if limit:
                if use_uid_cursor and not supplier and not from_filter and not subject_filter:
                    backlog = max(0, len(work_items) - limit)
                    if backlog:
                        summary["remaining_backlog"] += backlog
                        _log_line(
                            f"{mailbox.name}: backlog remaining after this run={backlog}; processing oldest {limit}."
                        )
                        record_diagnostic(
                            decision=models.AttachmentDecision.SKIPPED,
                            reason_code=models.AttachmentReason.BACKLOG_REMAINING,
                            message=f"{backlog} message(s) remain after this run.",
                            mailbox=mailbox,
                        )
                    work_items = work_items[:limit]
                else:
                    work_items = work_items[-limit:]
            note(f"{mailbox.name}: processing {len(work_items)} message(s) after limit.")
            if run_id:
                models.EmailImportRun.objects.filter(id=run_id).update(
                    total_messages=len(work_items),
                    processed_messages=0,
                    last_message=f"Found {len(work_items)} message(s) in {mailbox.name}.",
                )
            if not work_items and run_id:
                models.EmailImportRun.objects.filter(id=run_id).update(
                    last_message=f"No messages found in {mailbox.name}.",
                )

            max_processed_uid_by_field = {}
            for item_folder, msg_id in work_items:
                check_timeout("processing messages")
                if run_was_canceled():
                    break
                if selected_folder != item_folder:
                    if not _select_mailbox(client, item_folder):
                        client = _connect_imap(mailbox, _log_line, select_folder=item_folder)
                        if not client:
                            summary["errors"] += 1
                            note(f"Skipping message due folder select failure: {mailbox.name}/{item_folder}")
                            continue
                    selected_folder = item_folder
                summary["messages_scanned"] += 1
                if run_id:
                    models.EmailImportRun.objects.filter(id=run_id).update(
                        processed_messages=F("processed_messages") + 1
                    )
                status, meta, client = _imap_fetch(
                    client,
                    mailbox,
                    msg_id,
                    "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)] RFC822.SIZE INTERNALDATE)",
                    _log_line,
                    selected_folder=item_folder,
                )
                if not client:
                    continue
                if status != "OK" or not meta:
                    continue
                size = None
                for item in meta:
                    if isinstance(item, tuple):
                        text = item[0].decode(errors="ignore")
                        if "RFC822.SIZE" in text:
                            try:
                                size = int(text.split("RFC822.SIZE")[1].split()[0])
                            except (IndexError, ValueError):
                                size = None
                if size and size > max_bytes:
                    note(f"Skipping message {item_folder}/{_uid_display(msg_id)}: size {size} bytes.")
                    if run_id:
                        models.EmailImportRun.objects.filter(id=run_id).update(
                            last_message=f"Skipped large message {item_folder}/{_uid_display(msg_id)}"
                        )
                    continue
                received_at = _extract_internaldate(meta)
                if received_at and timezone.is_naive(received_at):
                    received_at = timezone.make_aware(received_at)
                if received_at and timezone.is_aware(received_at):
                    received_at = received_at.astimezone(timezone.get_current_timezone())
                if not received_at:
                    received_at = _extract_header_date(meta)
                if (
                    min_received_at
                    and received_at
                    and received_at <= min_received_at
                ):
                    if run_id:
                        models.EmailImportRun.objects.filter(id=run_id).update(
                            last_message=(
                                "Skipped old email at "
                                f"{timezone.localtime(received_at).strftime('%d/%m/%Y %H:%M')}"
                            )
                        )
                    last_message = (
                        "Skipped old email at "
                        f"{timezone.localtime(received_at).strftime('%d/%m/%Y %H:%M')}"
                    )
                    continue
                status, msg_data, client = _imap_fetch(
                    client,
                    mailbox,
                    msg_id,
                    "(BODY.PEEK[])",
                    _log_line,
                    selected_folder=item_folder,
                )
                if not client:
                    continue
                if status != "OK" or not msg_data:
                    _log_line(
                        f"{mailbox.name}/{item_folder}: skip message {_uid_display(msg_id)} - fetch body failed."
                    )
                    continue
                raw_email = _extract_raw_email_from_fetch(msg_data)
                if not raw_email:
                    _log_line(
                        f"{mailbox.name}/{item_folder}: skip message {_uid_display(msg_id)} - empty RFC822 payload."
                    )
                    continue
                uid_int = _uid_to_int(msg_id)
                cursor_field = _folder_cursor_field(item_folder)
                if uid_int:
                    previous_uid = max_processed_uid_by_field.get(cursor_field, 0)
                    if uid_int > previous_uid:
                        max_processed_uid_by_field[cursor_field] = uid_int
                message = email.message_from_bytes(raw_email)
                subject = _decode_header(message.get("Subject", ""))
                from_addr = parseaddr(message.get("From", ""))[1]
                message_id = (message.get("Message-ID") or "").strip()
                _log_line(
                    "Message "
                    f"{mailbox.name}/{item_folder}/{_uid_display(msg_id)} "
                    f"from={_short(from_addr, 80)} "
                    f"subject='{_short(subject, 120)}' "
                    f"msgid={_short(message_id, 80) or '-'}"
                )
                if not received_at and message.get("Date"):
                    try:
                        received_at = parsedate_to_datetime(message.get("Date"))
                        if received_at and timezone.is_naive(received_at):
                            received_at = timezone.make_aware(received_at)
                    except Exception:
                        received_at = None

                batch_by_supplier = {}
                processed_any = False
                valid_attachment_processed = False
                for part in message.walk():
                    check_timeout("processing attachments")
                    if valid_attachment_processed:
                        _log_line(
                            f"{mailbox.name}/{item_folder}: processed first valid attachment in message {_uid_display(msg_id)}; skipping remaining attachments."
                        )
                        break
                    if run_was_canceled():
                        break
                    if part.get_content_maintype() == "multipart":
                        continue
                    filename = _get_part_filename(part)
                    content_type = (part.get_content_type() or "").lower()
                    if not filename:
                        ext = _infer_extension(content_type)
                        if ext and part.get_content_maintype() != "text":
                            filename = f"attachment_{timezone.now():%Y%m%d_%H%M%S}{ext}"
                            _log_line(
                                f"{mailbox.name}: inferred attachment filename '{filename}' from content-type={content_type or '-'}."
                            )
                        else:
                            summary["skipped_no_filename"] += 1
                            summary["skipped_files"] += 1
                            record_diagnostic(
                                decision=models.AttachmentDecision.SKIPPED,
                                reason_code=models.AttachmentReason.UNSUPPORTED_EXTENSION,
                                message="Attachment has no filename.",
                                mailbox=mailbox,
                                folder=item_folder,
                                msg_id=msg_id,
                                email_message_id=message_id,
                                message_date=received_at,
                                sender=from_addr,
                                subject=subject,
                                content_type=content_type,
                            )
                            _log_line(
                                f"{mailbox.name}/{item_folder}: SKIP attachment in message {_uid_display(msg_id)} - no filename."
                            )
                            continue
                    summary["attachments_seen"] += 1
                    lowered_filename = filename.lower()
                    if blacklist_terms and any(term in lowered_filename for term in blacklist_terms):
                        summary["skipped_blacklist"] += 1
                        summary["skipped_files"] += 1
                        record_diagnostic(
                            decision=models.AttachmentDecision.SKIPPED,
                            reason_code=models.AttachmentReason.FILENAME_BLACKLISTED,
                            message="Filename matched blacklist.",
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                        )
                        _log_line(
                            f"{mailbox.name}: SKIP '{filename}' - blacklist match."
                        )
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                last_message=f"Skipped by filename blacklist: {filename}"
                            )
                        last_message = f"Skipped by filename blacklist: {filename}"
                        continue
                    payload = part.get_payload(decode=True)
                    if not payload:
                        summary["skipped_no_payload"] += 1
                        summary["skipped_files"] += 1
                        record_diagnostic(
                            decision=models.AttachmentDecision.SKIPPED,
                            reason_code=models.AttachmentReason.EMPTY_PAYLOAD,
                            message="Attachment payload is empty.",
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                        )
                        _log_line(
                            f"{mailbox.name}: SKIP '{filename}' - empty payload."
                        )
                        continue

                    lower_name = filename.lower()
                    extension = _filename_extension(lower_name)
                    content_hash = hashlib.sha256(payload).hexdigest()
                    if _is_non_price_filename(filename, content_type):
                        summary["skipped_files"] += 1
                        record_diagnostic(
                            decision=models.AttachmentDecision.SKIPPED,
                            reason_code=models.AttachmentReason.INVOICE_OR_REPORT,
                            message="Attachment looks like an invoice, report, image, or non-price document.",
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                            payload=payload,
                            content_hash=content_hash,
                        )
                        _log_line(
                            f"{mailbox.name}: SKIP '{filename}' - invoice/report/image classifier."
                        )
                        continue
                    if extension not in SUPPORTED_PRICE_EXTENSIONS:
                        summary["skipped_unsupported_extension"] += 1
                        summary["skipped_files"] += 1
                        record_diagnostic(
                            decision=models.AttachmentDecision.SKIPPED,
                            reason_code=models.AttachmentReason.UNSUPPORTED_EXTENSION,
                            message=f"Unsupported file type: {extension or '-'}",
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                            payload=payload,
                            content_hash=content_hash,
                        )
                        _log_line(
                            f"{mailbox.name}: SKIP '{filename}' - unsupported extension for price import."
                        )
                        continue
                    readable, readable_error = _validate_spreadsheet_payload(filename, payload)
                    if not readable:
                        summary["skipped_files"] += 1
                        record_diagnostic(
                            decision=models.AttachmentDecision.SKIPPED,
                            reason_code=models.AttachmentReason.WORKBOOK_UNREADABLE,
                            message=f"Spreadsheet could not be opened: {_short(readable_error, 220)}",
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                            payload=payload,
                            content_hash=content_hash,
                        )
                        _log_line(
                            f"{mailbox.name}: SKIP '{filename}' - unreadable workbook: {_short(readable_error, 180)}"
                        )
                        continue

                    rule = _pick_rule(mailbox, from_addr, subject, filename, supplier_id)
                    matched_supplier = None
                    if rule:
                        matched_supplier = rule.supplier
                    elif supplier:
                        if not _match_supplier_fallback(
                            supplier,
                            from_addr,
                            subject,
                            filename,
                            has_rules_for_mailbox,
                        ):
                            summary["skipped_files"] += 1
                            record_diagnostic(
                                decision=models.AttachmentDecision.SKIPPED,
                                reason_code=models.AttachmentReason.SENDER_NOT_MATCHED,
                                message="Spreadsheet did not match this supplier sender/subject/filename pattern.",
                                mailbox=mailbox,
                                folder=item_folder,
                                msg_id=msg_id,
                                email_message_id=message_id,
                                message_date=received_at,
                                sender=from_addr,
                                subject=subject,
                                filename=filename,
                                content_type=content_type,
                                payload=payload,
                                content_hash=content_hash,
                                supplier_obj=supplier,
                            )
                            continue
                        matched_supplier = supplier
                    else:
                        matched_supplier = _find_supplier_fallback(
                            fallback_suppliers or [],
                            from_addr,
                            subject,
                            filename,
                        )
                        if not matched_supplier:
                            key = (
                                (from_addr or "").strip().lower(),
                                (subject or "").strip()[:120],
                                (filename or "").strip()[:120],
                            )
                            unmatched_samples[key] = unmatched_samples.get(key, 0) + 1
                            if unmatched_log_count < unmatched_log_limit:
                                unmatched_log_count += 1
                                _log_line(
                                    f"{mailbox.name}: UNMATCHED '{filename}' from={_short(from_addr, 80)} subject='{_short(subject, 100)}'."
                                )
                            record_diagnostic(
                                decision=models.AttachmentDecision.SKIPPED,
                                reason_code=models.AttachmentReason.SENDER_NOT_MATCHED,
                                message="Spreadsheet did not match any active supplier sender/subject/filename pattern.",
                                mailbox=mailbox,
                                folder=item_folder,
                                msg_id=msg_id,
                                email_message_id=message_id,
                                message_date=received_at,
                                sender=from_addr,
                                subject=subject,
                                filename=filename,
                                content_type=content_type,
                                payload=payload,
                                content_hash=content_hash,
                            )
                            continue
                    summary["matched_files"] += 1
                    _log_line(
                        f"{mailbox.name}: MATCH supplier='{matched_supplier.name}' file='{filename}'."
                    )
                    if run_id:
                        models.EmailImportRun.objects.filter(id=run_id).update(
                            matched_files=F("matched_files") + 1
                        )

                    if rule:
                        file_kind = (
                            models.FileKind.STOCK
                            if rule.match_stock_files and not rule.match_price_files
                            else models.FileKind.PRICE
                        )
                    else:
                        file_kind = models.FileKind.PRICE
                    supplier_id_for_stats = matched_supplier.id
                    stats = supplier_stats.setdefault(
                        supplier_id_for_stats,
                        {
                            "matched": 0,
                            "processed": 0,
                            "errors": 0,
                            "last_message": "",
                            "duplicates": 0,
                            "skipped": 0,
                        },
                    )
                    stats["matched"] = stats.get("matched", 0) + 1

                    valid_attachment_processed = True

                    summary["price_candidates"] += 1
                    if dedupe_same_day_only and received_at:
                        day_start_utc, day_end_utc = _local_day_window_bounds(
                            received_at, dedupe_day_window
                        )
                        # Deduplicate within supplier + local day window across all
                        # mailboxes so the same attachment hash is imported once.
                        # Include pending files too so repeated attachments in the same run
                        # are skipped immediately.
                        exists = models.ImportFile.objects.filter(
                            content_hash=content_hash,
                            file_kind=file_kind,
                            import_batch__supplier=matched_supplier,
                            import_batch__received_at__gte=day_start_utc,
                            import_batch__received_at__lt=day_end_utc,
                            status__in=[
                                models.ImportStatus.PENDING,
                                models.ImportStatus.PROCESSED,
                            ],
                        ).exists()
                    else:
                        exists = models.ImportFile.objects.filter(
                            content_hash=content_hash,
                            status=models.ImportStatus.PROCESSED,
                            import_batch__supplier=matched_supplier,
                        ).exists()
                    if exists:
                        summary["skipped_duplicates"] += 1
                        summary["skipped_files"] += 1
                        _log_line(
                            f"{mailbox.name}: SKIP duplicate '{filename}' (supplier={matched_supplier.name}, hash={content_hash[:10]}...)."
                        )
                        record_diagnostic(
                            decision=models.AttachmentDecision.DUPLICATE,
                            reason_code=models.AttachmentReason.DUPLICATE_HASH,
                            message="Duplicate price attachment hash.",
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                            payload=payload,
                            content_hash=content_hash,
                            supplier_obj=matched_supplier,
                        )
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                skipped_duplicates=F("skipped_duplicates") + 1
                            )
                        last_message = f"Skipped duplicate file: {filename}"
                        stats["duplicates"] = stats.get("duplicates", 0) + 1
                        continue

                    batch = batch_by_supplier.get(matched_supplier.id)
                    if not batch:
                        if message_id:
                            batch, _ = models.ImportBatch.objects.get_or_create(
                                supplier=matched_supplier,
                                message_id=message_id,
                                defaults={
                                    "mailbox": mailbox,
                                    "message_folder": item_folder,
                                    "received_at": received_at,
                                    "status": models.ImportStatus.PENDING,
                                },
                            )
                            # Backfill critical metadata on previously created batches.
                            update_fields = []
                            if batch.mailbox_id is None:
                                batch.mailbox = mailbox
                                update_fields.append("mailbox")
                            if batch.received_at is None and received_at is not None:
                                batch.received_at = received_at
                                update_fields.append("received_at")
                            if not batch.message_folder:
                                batch.message_folder = item_folder
                                update_fields.append("message_folder")
                            if update_fields:
                                batch.save(update_fields=update_fields)
                        else:
                            # Some providers omit Message-ID; never collapse such
                            # emails into old batches with blank IDs.
                            batch = models.ImportBatch.objects.create(
                                supplier=matched_supplier,
                                mailbox=mailbox,
                                message_folder=item_folder,
                                message_id="",
                                received_at=received_at,
                                status=models.ImportStatus.PENDING,
                            )
                        batch_by_supplier[matched_supplier.id] = batch

                    mapping = models.SupplierFileMapping.objects.filter(
                        supplier=matched_supplier,
                        file_kind=file_kind,
                        is_active=True,
                    ).order_by("-id").first()
                    import_file = models.ImportFile.objects.create(
                        import_batch=batch,
                        mapping=mapping,
                        file_kind=file_kind,
                        filename=filename,
                        content_hash=content_hash,
                        status=models.ImportStatus.PENDING,
                    )
                    if not mapping and file_kind == models.FileKind.PRICE:
                        import_file.storage_type = models.ImportFileStorage.QUARANTINE
                        import_file.status = models.ImportStatus.FAILED
                        import_file.reason_code = models.AttachmentReason.MAPPING_MISSING
                        import_file.quarantine_until = timezone.now() + timezone.timedelta(
                            days=int(settings_obj.quarantine_retention_days or 30)
                        )
                        import_file.file.save(filename, ContentFile(payload), save=True)
                        import_file.save(
                            update_fields=[
                                "storage_type",
                                "status",
                                "reason_code",
                                "quarantine_until",
                            ]
                        )
                        batch.status = models.ImportStatus.FAILED
                        batch.error_message = "Mapping is missing."
                        batch.save(update_fields=["status", "error_message"])
                        summary["errors"] += 1
                        summary["failed_files"] += 1
                        summary["quarantined_files"] += 1
                        stats["errors"] = stats.get("errors", 0) + 1
                        stats["last_message"] = "Mapping is missing."
                        record_diagnostic(
                            decision=models.AttachmentDecision.QUARANTINED,
                            reason_code=models.AttachmentReason.MAPPING_MISSING,
                            message="Mapping is missing.",
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                            payload=payload,
                            content_hash=content_hash,
                            supplier_obj=matched_supplier,
                            batch=batch,
                            import_file=import_file,
                        )
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                errors=F("errors") + 1,
                                last_message="Mapping is missing.",
                            )
                        continue
                    import_file.file.save(filename, ContentFile(payload), save=True)
                    _log_line(
                        f"{mailbox.name}: PROCESS file='{filename}' supplier='{matched_supplier.name}' kind={file_kind} import_file_id={import_file.id}."
                    )
                    processed_any = True
                    try:
                        process_import_file(import_file)
                        import_file.status = models.ImportStatus.PROCESSED
                        import_file.save(update_fields=["status"])
                        summary["processed_files"] += 1
                        _log_line(
                            f"{mailbox.name}: OK file='{filename}' supplier='{matched_supplier.name}' import_file_id={import_file.id}."
                        )
                        record_diagnostic(
                            decision=models.AttachmentDecision.IMPORTED,
                            message="Price file imported successfully.",
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                            payload=payload,
                            content_hash=content_hash,
                            supplier_obj=matched_supplier,
                            batch=batch,
                            import_file=import_file,
                        )
                        stats["processed"] = stats.get("processed", 0) + 1
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                processed_files=F("processed_files") + 1
                            )
                    except Exception as exc:
                        reason_code = _reason_from_error(str(exc))
                        try:
                            if import_file.file:
                                import_file.file.delete(save=False)
                        except Exception as delete_exc:
                            _log_line(
                                f"{mailbox.name}: failed to remove permanent file before quarantine: {_short(delete_exc, 180)}"
                            )
                        import_file.storage_type = models.ImportFileStorage.QUARANTINE
                        import_file.status = models.ImportStatus.FAILED
                        import_file.reason_code = reason_code
                        import_file.quarantine_until = timezone.now() + timezone.timedelta(
                            days=int(settings_obj.quarantine_retention_days or 30)
                        )
                        import_file.error_message = str(exc)
                        import_file.file.save(filename, ContentFile(payload), save=True)
                        import_file.save(
                            update_fields=[
                                "storage_type",
                                "status",
                                "reason_code",
                                "quarantine_until",
                                "error_message",
                            ]
                        )
                        batch.status = models.ImportStatus.FAILED
                        batch.error_message = str(exc)
                        batch.save(update_fields=["status", "error_message"])
                        summary["errors"] += 1
                        summary["failed_files"] += 1
                        summary["quarantined_files"] += 1
                        _log_line(
                            f"{mailbox.name}: FAIL file='{filename}' supplier='{matched_supplier.name}' import_file_id={import_file.id} error={_short(exc, 260)}"
                        )
                        record_diagnostic(
                            decision=models.AttachmentDecision.QUARANTINED,
                            reason_code=reason_code,
                            message=str(exc),
                            mailbox=mailbox,
                            folder=item_folder,
                            msg_id=msg_id,
                            email_message_id=message_id,
                            message_date=received_at,
                            sender=from_addr,
                            subject=subject,
                            filename=filename,
                            content_type=content_type,
                            payload=payload,
                            content_hash=content_hash,
                            supplier_obj=matched_supplier,
                            batch=batch,
                            import_file=import_file,
                        )
                        stats["errors"] = stats.get("errors", 0) + 1
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                errors=F("errors") + 1,
                                last_message=str(exc),
                            )
                        last_message = f"Import failed: {filename} (see detailed log)."
                        stats["last_message"] = str(exc)

                for batch in batch_by_supplier.values():
                    if batch.status != models.ImportStatus.FAILED:
                        batch.status = models.ImportStatus.PROCESSED
                        batch.save(update_fields=["status"])

                if processed_any and mark_seen:
                    try:
                        client.store(msg_id, "+FLAGS", "\\Seen")
                    except (imaplib.IMAP4.abort, socket.timeout, ssl.SSLError):
                        _log(_log_line, "Failed to mark message as seen.")

            if use_uid_cursor and not supplier and not from_filter and not subject_filter:
                update_fields = []
                for cursor_field, max_processed_uid in max_processed_uid_by_field.items():
                    current_uid = getattr(mailbox, cursor_field, 0) or 0
                    if max_processed_uid > current_uid:
                        setattr(mailbox, cursor_field, max_processed_uid)
                        update_fields.append(cursor_field)
                if update_fields:
                    mailbox.last_checked_at = timezone.now()
                    update_fields.append("last_checked_at")
                    mailbox.save(update_fields=update_fields)

            if client:
                client.logout()
                client = None
    except TimeoutError:
        summary["errors"] += 1
        summary["timed_out"] = True
    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass
    if not supplier and fallback_suppliers is not None:
        fallback_by_id = {
            fallback_supplier.id: fallback_supplier for fallback_supplier in fallback_suppliers
        }
        for supplier_id_for_stats, stats in supplier_stats.items():
            fallback_supplier = fallback_by_id.get(supplier_id_for_stats)
            if not fallback_supplier:
                continue
            if not stats.get("last_message"):
                parts = []
                if stats.get("duplicates"):
                    parts.append(f"{stats.get('duplicates')} duplicate(s)")
                if stats.get("skipped"):
                    parts.append(f"{stats.get('skipped')} skipped")
                stats["last_message"] = ", ".join(parts) if parts else "Matching email handled."
            fallback_supplier.last_email_check_at = run_started
            fallback_supplier.last_email_matched = stats.get("matched", 0)
            fallback_supplier.last_email_processed = stats.get("processed", 0)
            fallback_supplier.last_email_errors = stats.get("errors", 0)
            fallback_supplier.last_email_last_message = stats.get("last_message") or ""
            fallback_supplier.last_email_mailboxes = mailbox_names
            fallback_supplier.save(
                update_fields=[
                    "last_email_check_at",
                    "last_email_matched",
                    "last_email_processed",
                    "last_email_errors",
                    "last_email_last_message",
                    "last_email_mailboxes",
                ]
            )
    if run_id:
        run = models.EmailImportRun.objects.filter(id=run_id).first()
        if run and run.status == models.EmailImportStatus.CANCELED:
            models.EmailImportRun.objects.filter(id=run_id).update(
                finished_at=timezone.now(),
                detailed_log="\n".join(log_lines),
            )
        elif not timed_out:
            final_message = (
                f"Imported {summary['processed_files']} of {summary['matched_files']} matched file(s); "
                f"duplicates {summary['skipped_duplicates']}, skipped {summary['skipped_files']}, "
                f"failed {summary['failed_files']}, quarantined {summary['quarantined_files']}, "
                f"errors {summary['errors']}, "
                f"remaining backlog {summary['remaining_backlog']}."
            )
            models.EmailImportRun.objects.filter(id=run_id).update(
                status=models.EmailImportStatus.FINISHED,
                finished_at=timezone.now(),
                matched_files=summary["matched_files"],
                processed_files=summary["processed_files"],
                skipped_duplicates=summary["skipped_duplicates"],
                errors=summary["errors"],
                last_message=final_message,
                detailed_log="\n".join(log_lines),
            )
    if not summary["matched_files"] and unmatched_samples and logger:
        _log(_log_line, "No supplier matches. Top unmatched sender/subject/file:")
        top_items = sorted(unmatched_samples.items(), key=lambda item: item[1], reverse=True)[:10]
        for (from_addr, subj, fname), count in top_items:
            _log(
                _log_line,
                f"- x{count} from='{from_addr}' subject='{subj}' file='{fname}'",
            )
    if logger:
        _log(
            _log_line,
            "Attachment diagnostics: "
            f"messages_found={summary['messages_found']} "
            f"messages_scanned={summary['messages_scanned']} "
            f"seen={summary['attachments_seen']} "
            f"no_filename={summary['skipped_no_filename']} "
            f"no_payload={summary['skipped_no_payload']} "
            f"blacklist={summary['skipped_blacklist']} "
            f"unsupported_ext={summary['skipped_unsupported_extension']} "
            f"price_candidates={summary['price_candidates']} "
            f"skipped={summary['skipped_files']} "
            f"failed={summary['failed_files']} "
            f"quarantined={summary['quarantined_files']} "
            f"remaining_backlog={summary['remaining_backlog']}",
        )
    if run_id:
        models.EmailImportRun.objects.filter(id=run_id).update(
            detailed_log="\n".join(log_lines)
        )
    summary["last_message"] = last_message
    return summary


def _log(logger, message):
    if logger:
        logger(message)


def _uid_to_int(uid):
    if isinstance(uid, bytes):
        uid = uid.decode(errors="ignore")
    try:
        return int(str(uid).strip())
    except Exception:
        return None


def _uid_display(uid) -> str:
    if isinstance(uid, bytes):
        return uid.decode(errors="ignore")
    return str(uid)


def _folder_cursor_field(folder_name: str) -> str:
    return "last_inbox_uid" if folder_name == "INBOX" else "last_all_mail_uid"


def _sort_message_ids(message_ids):
    try:
        return sorted(message_ids, key=lambda value: int(value))
    except Exception:
        return list(message_ids)
