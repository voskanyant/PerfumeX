import email
import hashlib
import imaplib
import re
import socket
import ssl
import time
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr

from django.core.files.base import ContentFile
from django.utils import timezone

from django.db.models import F

from prices import models
from prices.services.importer import process_import_file


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


def _connect_imap(mailbox, logger):
    for attempt in range(2):
        try:
            client = imaplib.IMAP4_SSL(mailbox.host, mailbox.port, timeout=45)
            client.login(mailbox.username, mailbox.password)
            client.select("INBOX")
            return client
        except Exception as exc:
            _log(
                logger,
                f"IMAP connection failed for {mailbox.name} (attempt {attempt + 1}/2): {exc}",
            )
            time.sleep(1)
    return None


def _imap_search(client, mailbox, criteria, logger):
    for attempt in range(2):
        try:
            status, data = client.search(None, *criteria)
            return status, data, client
        except (imaplib.IMAP4.abort, socket.timeout, ssl.SSLError) as exc:
            _log(logger, f"IMAP search error ({mailbox.name}): {exc}")
            if attempt == 0:
                client = _connect_imap(mailbox, logger)
                if not client:
                    return "NO", [], None
                continue
            return "NO", [], client
    return "NO", [], client


def _imap_fetch(client, mailbox, msg_id, query, logger):
    for attempt in range(2):
        try:
            status, data = client.fetch(msg_id, query)
            return status, data, client
        except (imaplib.IMAP4.abort, socket.timeout, ssl.SSLError) as exc:
            _log(logger, f"IMAP fetch error ({mailbox.name}): {exc}")
            if attempt == 0:
                client = _connect_imap(mailbox, logger)
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
    min_received_at=None,
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

    def note(msg):
        nonlocal last_message
        last_message = msg
        if logger:
            logger(msg)

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
    }
    if run_id:
        models.EmailImportRun.objects.filter(id=run_id).update(
            status=models.EmailImportStatus.RUNNING
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
            )
        raise TimeoutError(msg)

    client = None
    try:
        for mailbox in mailboxes:
            check_timeout("mailbox loop")
            if run_id:
                run = models.EmailImportRun.objects.filter(id=run_id).first()
                if run and run.status == models.EmailImportStatus.CANCELED:
                    break
            if mailbox.protocol != models.Mailbox.IMAP:
                _log(logger, f"Skipping {mailbox.name}: only IMAP supported.")
                continue

            has_rules_for_mailbox = False
            if supplier:
                has_rules_for_mailbox = models.SupplierMailboxRule.objects.filter(
                    supplier=supplier, mailbox=mailbox, is_active=True
                ).exists()
            client = _connect_imap(mailbox, logger)
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
            status, data, client = _imap_search(client, mailbox, criteria, logger)
            if status != "OK":
                note(f"Failed to search mailbox: {mailbox.name}")
                if client:
                    client.logout()
                continue
            message_ids = data[0].split()
            inbox_ids = set(message_ids)
            note(f"{mailbox.name}: found {len(message_ids)} message(s) in INBOX.")
            # Gmail can have relevant messages outside INBOX (archived/labels).
            # Always merge INBOX + All Mail for reliability.
            if mailbox.host and "gmail.com" in mailbox.host.lower():
                try:
                    selected = False
                    for folder in ('"[Gmail]/All Mail"', '"[Google Mail]/All Mail"'):
                        sel_status, _ = client.select(folder)
                        if sel_status == "OK":
                            selected = True
                            break
                    if selected:
                        status, data, client = _imap_search(client, mailbox, criteria, logger)
                        if status == "OK":
                            all_mail_ids = set(data[0].split())
                            note(
                                f"{mailbox.name}: found {len(all_mail_ids)} message(s) in Gmail All Mail."
                            )
                            merged_ids = inbox_ids | all_mail_ids
                            if merged_ids:
                                message_ids = list(merged_ids)
                                _log(logger, f"Using Gmail All Mail for {mailbox.name}.")
                            else:
                                message_ids = list(inbox_ids)
                    else:
                        _log(logger, f"Gmail All Mail folder not accessible for {mailbox.name}.")
                except Exception as exc:
                    _log(logger, f"Failed Gmail All Mail fallback ({mailbox.name}): {exc}")
            # Process oldest first so history is built chronologically.
            try:
                message_ids = sorted(message_ids, key=lambda x: int(x))
            except Exception:
                pass
            if limit:
                # Always take newest messages in incremental runs to avoid
                # starvation when mailbox volume is high.
                message_ids = message_ids[-limit:]
            note(f"{mailbox.name}: processing {len(message_ids)} message(s) after limit.")
            if run_id:
                models.EmailImportRun.objects.filter(id=run_id).update(
                    total_messages=len(message_ids),
                    processed_messages=0,
                    last_message=f"Found {len(message_ids)} message(s) in {mailbox.name}.",
                )
            if not message_ids and run_id:
                models.EmailImportRun.objects.filter(id=run_id).update(
                    last_message=f"No messages found in {mailbox.name}.",
                )

            for msg_id in message_ids:
                check_timeout("processing messages")
                if run_id:
                    run = models.EmailImportRun.objects.filter(id=run_id).first()
                    if run and run.status == models.EmailImportStatus.CANCELED:
                        break
                if run_id:
                    models.EmailImportRun.objects.filter(id=run_id).update(
                        processed_messages=F("processed_messages") + 1
                    )
                status, meta, client = _imap_fetch(
                    client,
                    mailbox,
                    msg_id,
                    "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)] RFC822.SIZE INTERNALDATE)",
                    logger,
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
                    note(f"Skipping message {msg_id.decode()}: size {size} bytes.")
                    if run_id:
                        models.EmailImportRun.objects.filter(id=run_id).update(
                            last_message=f"Skipped large message {msg_id.decode()}"
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
                    client, mailbox, msg_id, "(BODY.PEEK[])", logger
                )
                if not client:
                    continue
                if status != "OK" or not msg_data:
                    continue
                raw_email = msg_data[0][1]
                message = email.message_from_bytes(raw_email)
                subject = _decode_header(message.get("Subject", ""))
                from_addr = parseaddr(message.get("From", ""))[1]
                message_id = (message.get("Message-ID") or "").strip()
                if not received_at and message.get("Date"):
                    try:
                        received_at = parsedate_to_datetime(message.get("Date"))
                        if received_at and timezone.is_naive(received_at):
                            received_at = timezone.make_aware(received_at)
                    except Exception:
                        received_at = None

                batch_by_supplier = {}
                processed_any = False
                for part in message.walk():
                    check_timeout("processing attachments")
                    if run_id:
                        run = models.EmailImportRun.objects.filter(id=run_id).first()
                        if run and run.status == models.EmailImportStatus.CANCELED:
                            break
                    if part.get_content_maintype() == "multipart":
                        continue
                    filename = _get_part_filename(part)
                    content_type = (part.get_content_type() or "").lower()
                    if not filename:
                        ext = _infer_extension(content_type)
                        if ext and part.get_content_maintype() != "text":
                            filename = f"attachment_{timezone.now():%Y%m%d_%H%M%S}{ext}"
                        else:
                            summary["skipped_no_filename"] += 1
                            continue
                    summary["attachments_seen"] += 1
                    lowered_filename = filename.lower()
                    if blacklist_terms and any(term in lowered_filename for term in blacklist_terms):
                        summary["skipped_blacklist"] += 1
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                last_message=f"Skipped by filename blacklist: {filename}"
                            )
                        last_message = f"Skipped by filename blacklist: {filename}"
                        continue
                    payload = part.get_payload(decode=True)
                    if not payload:
                        summary["skipped_no_payload"] += 1
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
                            continue
                    summary["matched_files"] += 1
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
                        },
                    )
                    stats["matched"] = stats.get("matched", 0) + 1

                    if file_kind == models.FileKind.PRICE:
                        lower_name = filename.lower()
                        if not lower_name.endswith((".csv", ".xlsx", ".xls")):
                            if run_id:
                                models.EmailImportRun.objects.filter(id=run_id).update(
                                    last_message=f"Skipped unsupported file: {filename}"
                                )
                            summary["skipped_unsupported_extension"] += 1
                            last_message = f"Skipped unsupported file: {filename}"
                            continue

                    content_hash = hashlib.sha256(payload).hexdigest()
                    if dedupe_same_day_only and received_at:
                        message_day = timezone.localtime(received_at).date()
                        exists = models.ImportFile.objects.filter(
                            content_hash=content_hash,
                            status=models.ImportStatus.PROCESSED,
                            import_batch__supplier=matched_supplier,
                            import_batch__received_at__date=message_day,
                        ).exists()
                    else:
                        exists = models.ImportFile.objects.filter(
                            content_hash=content_hash,
                            status=models.ImportStatus.PROCESSED,
                            import_batch__supplier=matched_supplier,
                        ).exists()
                    if exists:
                        summary["skipped_duplicates"] += 1
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                skipped_duplicates=F("skipped_duplicates") + 1
                            )
                        last_message = f"Skipped duplicate file: {filename}"
                        continue

                    batch = batch_by_supplier.get(matched_supplier.id)
                    if not batch:
                        if message_id:
                            batch, _ = models.ImportBatch.objects.get_or_create(
                                supplier=matched_supplier,
                                message_id=message_id,
                                defaults={
                                    "mailbox": mailbox,
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
                            if update_fields:
                                batch.save(update_fields=update_fields)
                        else:
                            # Some providers omit Message-ID; never collapse such
                            # emails into old batches with blank IDs.
                            batch = models.ImportBatch.objects.create(
                                supplier=matched_supplier,
                                mailbox=mailbox,
                                message_id="",
                                received_at=received_at,
                                status=models.ImportStatus.PENDING,
                            )
                        batch_by_supplier[matched_supplier.id] = batch

                    import_file = models.ImportFile.objects.create(
                        import_batch=batch,
                        mapping=models.SupplierFileMapping.objects.filter(
                            supplier=matched_supplier,
                            file_kind=file_kind,
                            is_active=True,
                        ).order_by("-id").first(),
                        file_kind=file_kind,
                        filename=filename,
                        content_hash=content_hash,
                        status=models.ImportStatus.PENDING,
                    )
                    import_file.file.save(filename, ContentFile(payload), save=True)
                    processed_any = True
                    try:
                        process_import_file(import_file)
                        import_file.status = models.ImportStatus.PROCESSED
                        import_file.save(update_fields=["status"])
                        summary["processed_files"] += 1
                        stats["processed"] = stats.get("processed", 0) + 1
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                processed_files=F("processed_files") + 1
                            )
                    except Exception as exc:
                        import_file.status = models.ImportStatus.FAILED
                        import_file.error_message = str(exc)
                        import_file.save(update_fields=["status", "error_message"])
                        batch.status = models.ImportStatus.FAILED
                        batch.error_message = str(exc)
                        batch.save(update_fields=["status", "error_message"])
                        summary["errors"] += 1
                        stats["errors"] = stats.get("errors", 0) + 1
                        if run_id:
                            models.EmailImportRun.objects.filter(id=run_id).update(
                                errors=F("errors") + 1,
                                last_message=str(exc),
                            )
                        last_message = str(exc)
                        stats["last_message"] = str(exc)

                for batch in batch_by_supplier.values():
                    if batch.status != models.ImportStatus.FAILED:
                        batch.status = models.ImportStatus.PROCESSED
                        batch.save(update_fields=["status"])

                if processed_any and mark_seen:
                    try:
                        client.store(msg_id, "+FLAGS", "\\Seen")
                    except (imaplib.IMAP4.abort, socket.timeout, ssl.SSLError):
                        _log(logger, "Failed to mark message as seen.")

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
        for fallback_supplier in fallback_suppliers:
            stats = supplier_stats.get(
                fallback_supplier.id,
                {
                    "matched": 0,
                    "processed": 0,
                    "errors": 0,
                    "last_message": "No matching emails.",
                },
            )
            if not stats.get("last_message"):
                stats["last_message"] = "No matching emails."
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
                finished_at=timezone.now()
            )
        elif not timed_out:
            models.EmailImportRun.objects.filter(id=run_id).update(
                status=models.EmailImportStatus.FINISHED,
                finished_at=timezone.now(),
                matched_files=summary["matched_files"],
                processed_files=summary["processed_files"],
                skipped_duplicates=summary["skipped_duplicates"],
                errors=summary["errors"],
            )
    if not summary["matched_files"] and unmatched_samples and logger:
        _log(logger, "No supplier matches. Top unmatched sender/subject/file:")
        top_items = sorted(unmatched_samples.items(), key=lambda item: item[1], reverse=True)[:10]
        for (from_addr, subj, fname), count in top_items:
            _log(
                logger,
                f"- x{count} from='{from_addr}' subject='{subj}' file='{fname}'",
            )
    if logger:
        _log(
            logger,
            "Attachment diagnostics: "
            f"seen={summary['attachments_seen']} "
            f"no_filename={summary['skipped_no_filename']} "
            f"no_payload={summary['skipped_no_payload']} "
            f"blacklist={summary['skipped_blacklist']} "
            f"unsupported_ext={summary['skipped_unsupported_extension']}",
        )
    summary["last_message"] = last_message
    return summary


def _log(logger, message):
    if logger:
        logger(message)
