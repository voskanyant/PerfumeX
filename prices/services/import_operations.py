from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone

from prices import models
from prices.services import link_importer
from prices.services.email_importer import (
    _reason_from_error,
    _validate_spreadsheet_payload,
)
from prices.services.importer import (
    delete_import_batch,
    mark_import_batch_products_seen,
    preview_mapping_file,
    process_import_file,
)
from prices.services.job_queue import enqueue_management_command


@dataclass(frozen=True)
class ImportOperationActionResult:
    message_level: str
    message: str
    redirect_source: str = ""


@dataclass(frozen=True)
class SupplierImportMappingSaveResult:
    mapping: object
    action: ImportOperationActionResult


@dataclass(frozen=True)
class SupplierImportFormActionResult:
    action: ImportOperationActionResult | None = None
    redirect_url: str = ""
    form_error_field: str = ""
    form_error_message: str = ""


@dataclass(frozen=True)
class SupplierMappingPreviewResult:
    payload: dict
    status: int = 200


VALID_SUPPLIER_IMPORT_SOURCES = {"email", "link", "file"}


def normalize_supplier_import_source(
    value: str | None, *, default: str = "email"
) -> str:
    source = (value or default).strip()
    if source not in VALID_SUPPLIER_IMPORT_SOURCES:
        return default
    return source


def build_supplier_mapping_defaults(cleaned_data: dict) -> dict:
    sheet_selector = (cleaned_data.get("sheet_selector") or "").strip()
    sheet_names = []
    sheet_indexes = []
    if sheet_selector:
        for part in [value.strip() for value in sheet_selector.split(",")]:
            if not part:
                continue
            if part.isdigit():
                sheet_indexes.append(part)
            else:
                sheet_names.append(part)
    name_columns = [
        int(value.strip())
        for value in (cleaned_data.get("name_columns") or "").split(",")
        if value.strip().isdigit()
    ]
    sku_column = cleaned_data.get("sku_column")
    price_column = cleaned_data.get("price_column")
    currency_column = cleaned_data.get("currency_column")
    if not name_columns or not price_column:
        raise RuntimeError("Mapping must include name and price columns.")
    return {
        "mapping_mode": models.MappingMode.INDEX,
        "sheet_names": ", ".join(sheet_names),
        "sheet_indexes": ", ".join(sheet_indexes),
        "header_row": cleaned_data.get("header_row") or 1,
        "column_map": {
            "sku": sku_column or 0,
            "name": name_columns,
            "price": price_column,
            "currency": currency_column or 0,
        },
    }


def save_supplier_mapping_from_import_form(form, supplier):
    mapping, _ = models.SupplierFileMapping.objects.update_or_create(
        supplier=supplier,
        file_kind=models.FileKind.PRICE,
        is_active=True,
        defaults=build_supplier_mapping_defaults(form.cleaned_data),
    )
    return mapping


def save_supplier_import_mapping_action(
    supplier,
    form,
    *,
    save_func=save_supplier_mapping_from_import_form,
) -> SupplierImportMappingSaveResult:
    mapping = save_func(form, supplier)
    return SupplierImportMappingSaveResult(
        mapping=mapping,
        action=ImportOperationActionResult(
            "success",
            f"{supplier.name}: mapping saved.",
            redirect_source="file",
        ),
    )


def latest_active_supplier_mapping(
    supplier,
    *,
    file_kind=models.FileKind.PRICE,
    mapping_manager=None,
):
    mapping_manager = mapping_manager or models.SupplierFileMapping.objects
    return (
        mapping_manager.filter(
            supplier=supplier,
            file_kind=file_kind,
            is_active=True,
        )
        .order_by("-id")
        .first()
    )


def build_supplier_import_mapping_initial(mapping) -> dict:
    if not mapping:
        return {}
    column_map = mapping.column_map or {}
    name_value = column_map.get("name")
    name_columns = []
    if isinstance(name_value, list):
        name_columns = [str(value) for value in name_value if value]
    elif name_value:
        name_columns = [str(name_value)]

    sheet_selector_parts = []
    if mapping.sheet_names:
        sheet_selector_parts.extend(
            [name.strip() for name in mapping.sheet_names.split(",") if name.strip()]
        )
    if mapping.sheet_indexes:
        sheet_selector_parts.extend(
            [idx.strip() for idx in mapping.sheet_indexes.split(",") if idx.strip()]
        )

    return {
        "sheet_selector": ", ".join(sheet_selector_parts),
        "header_row": mapping.header_row,
        "sku_column": column_map.get("sku"),
        "name_columns": ",".join(name_columns),
        "price_column": column_map.get("price"),
        "currency_column": column_map.get("currency"),
    }


def build_supplier_import_initial(
    supplier,
    *,
    initial=None,
    mapping_func=latest_active_supplier_mapping,
) -> dict:
    values = dict(initial or {})
    mapping = mapping_func(supplier)
    if mapping:
        values.update(build_supplier_import_mapping_initial(mapping))
    return values


def build_supplier_import_context(
    supplier,
    *,
    source_raw: str | None = None,
    source_form_class=None,
) -> dict:
    if source_form_class is None:
        from prices.forms import SupplierPriceSourceForm

        source_form_class = SupplierPriceSourceForm

    return {
        "supplier": supplier,
        "active_import_source": normalize_supplier_import_source(source_raw),
        "source_form": source_form_class(
            initial={"source_type": models.PriceSourceType.FIXED_LINK}
        ),
        "price_sources": supplier.price_sources.order_by(
            "-is_active", "source_type", "id"
        ),
    }


def supplier_import_mapping_redirect_url(supplier_pk, source_raw: str | None) -> str:
    active_source = normalize_supplier_import_source(source_raw, default="file")
    return f"{supplier_import_tab_url(supplier_pk, active_source)}#mapping-preview"


def supplier_import_tab_url(
    supplier_pk,
    source_raw: str | None,
    *,
    default: str = "email",
) -> str:
    active_source = normalize_supplier_import_source(source_raw, default=default)
    return (
        f"{reverse('prices:supplier_import', args=[supplier_pk])}?"
        f"{urlencode({'source': active_source})}"
    )


def import_board_redirect_url(
    *,
    next_url_raw: str = "",
    host: str = "",
    is_staff: bool = False,
) -> str:
    next_url = (next_url_raw or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={host} if host else set(),
    ):
        return next_url
    if is_staff:
        return reverse("prices:supplier_overview")
    return reverse("viewer_import_prices")


def import_settings_or_overview_redirect_name(next_raw: str | None) -> str:
    if next_raw == "import_settings":
        return "prices:import_settings"
    return "prices:supplier_overview"


def build_import_wizard_initial(
    *,
    initial=None,
    supplier_raw: str | None = None,
    file_kind_raw: str | None = None,
) -> dict:
    values = dict(initial or {})
    if supplier_raw:
        values["supplier"] = supplier_raw
    if file_kind_raw:
        values["file_kind"] = file_kind_raw
    return values


def parse_supplier_mapping_preview_sheet_index(value) -> int | None:
    if value and str(value).isdigit():
        return int(value)
    return None


def build_supplier_mapping_preview_result(
    files,
    post_data,
    *,
    preview_func=preview_mapping_file,
) -> SupplierMappingPreviewResult:
    if "file" not in files:
        return SupplierMappingPreviewResult(
            payload={"error": "No file uploaded."},
            status=400,
        )
    sheet_index = parse_supplier_mapping_preview_sheet_index(
        post_data.get("sheet_index")
    )
    return SupplierMappingPreviewResult(
        payload=preview_func(files["file"], sheet_index),
    )


def run_supplier_import_form_action(
    supplier,
    form,
    *,
    action_raw: str | None,
    source_raw: str | None,
    save_mapping_func=None,
    upload_action_func=None,
) -> SupplierImportFormActionResult:
    save_mapping_func = save_mapping_func or save_supplier_import_mapping_action
    upload_action_func = upload_action_func or run_supplier_price_upload_action
    mapping_result = save_mapping_func(supplier, form)
    mapping = mapping_result.mapping
    action = action_raw or "upload_import"
    if action == "save_mapping":
        return SupplierImportFormActionResult(
            action=mapping_result.action,
            redirect_url=supplier_import_mapping_redirect_url(supplier.pk, source_raw),
        )

    upload = form.cleaned_data.get("file")
    if not upload:
        return SupplierImportFormActionResult(
            form_error_field="file",
            form_error_message=(
                "Choose a spreadsheet to upload and import, or use Save mapping."
            ),
        )

    return SupplierImportFormActionResult(
        action=upload_action_func(supplier, mapping, upload),
    )


def upload_content_hash(upload) -> str:
    if not upload:
        return ""
    hasher = hashlib.sha256()
    for chunk in upload.chunks():
        hasher.update(chunk)
    return hasher.hexdigest()


def process_supplier_upload(supplier, mapping, upload, file_kind):
    import_batch = models.ImportBatch.objects.create(
        supplier=supplier,
        status=models.ImportStatus.PENDING,
        received_at=timezone.now(),
    )
    content_hash = upload_content_hash(upload)
    import_file = models.ImportFile.objects.create(
        import_batch=import_batch,
        mapping=mapping,
        file_kind=file_kind,
        filename=upload.name if upload else "",
        file=upload,
        content_hash=content_hash,
        status=models.ImportStatus.PENDING,
    )
    try:
        process_import_file(import_file)
        import_batch.status = models.ImportStatus.PROCESSED
        import_batch.save(update_fields=["status"])
    except Exception as exc:
        import_file.status = models.ImportStatus.FAILED
        import_file.error_message = str(exc)
        import_file.save(update_fields=["status", "error_message"])
        import_batch.status = models.ImportStatus.FAILED
        import_batch.error_message = str(exc)
        import_batch.save(update_fields=["status", "error_message"])
        raise
    return import_batch


def process_supplier_price_upload(supplier, mapping, upload):
    return process_supplier_upload(supplier, mapping, upload, models.FileKind.PRICE)


def process_import_wizard_upload(
    supplier,
    file_kind,
    upload,
    *,
    mapping_func=latest_active_supplier_mapping,
    process_upload_func=process_supplier_upload,
) -> bool:
    mapping = mapping_func(supplier, file_kind=file_kind)
    try:
        process_upload_func(supplier, mapping, upload, file_kind)
    except Exception:
        return False
    return True


def delete_single_import_batch(
    import_batch,
    *,
    delete_func=delete_import_batch,
) -> None:
    delete_func(import_batch)


def run_import_delete_action(
    import_batch,
    *,
    next_url_raw: str = "",
    host: str = "",
    delete_func=delete_single_import_batch,
) -> str:
    delete_func(import_batch)
    next_url = (next_url_raw or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={host} if host else set(),
    ):
        return next_url
    return reverse("prices:supplier_overview")


def delete_import_batches_by_ids(
    import_ids,
    *,
    batch_manager=None,
    delete_func=delete_import_batch,
) -> int:
    import_ids = list(import_ids)
    if not import_ids:
        return 0
    batch_manager = batch_manager or models.ImportBatch.objects
    deleted = 0
    for batch in batch_manager.filter(id__in=import_ids):
        delete_func(batch)
        deleted += 1
    return deleted


def run_import_delete_bulk_action(
    import_ids,
    *,
    delete_func=delete_import_batches_by_ids,
) -> str:
    delete_func(import_ids)
    return reverse("prices:supplier_overview")


def run_supplier_price_upload_action(
    supplier,
    mapping,
    upload,
    *,
    process_upload_func=process_supplier_price_upload,
) -> ImportOperationActionResult:
    try:
        process_upload_func(supplier, mapping, upload)
    except Exception as exc:
        return ImportOperationActionResult(
            "error",
            f"{supplier.name}: upload failed. {exc}",
        )
    return ImportOperationActionResult(
        "success",
        f"{supplier.name}: {upload.name} imported.",
    )


def run_supplier_quick_upload_action(
    supplier,
    upload,
    *,
    mapping_func=latest_active_supplier_mapping,
    process_upload_func=process_supplier_price_upload,
) -> ImportOperationActionResult:
    mapping = mapping_func(supplier)
    if not mapping:
        return ImportOperationActionResult(
            "info",
            "Create or confirm the supplier price mapping first.",
            redirect_source="file",
        )

    if not upload:
        return ImportOperationActionResult("info", "Select a file to upload.")

    try:
        process_upload_func(supplier, mapping, upload)
    except Exception as exc:
        return ImportOperationActionResult(
            "error",
            f"{supplier.name}: upload failed. {exc}",
        )
    return ImportOperationActionResult(
        "success",
        f"{supplier.name}: {upload.name} imported.",
    )


def enqueue_bulk_price_reimport(
    *,
    description: str = "Bulk price reimport",
    enqueue_func=enqueue_management_command,
):
    return enqueue_func(
        "repair_supplier_price_imports",
        all_suppliers=True,
        description=description,
    )


def run_bulk_price_reimport_action(
    *,
    enqueue_func=enqueue_bulk_price_reimport,
) -> ImportOperationActionResult:
    try:
        enqueue_func(description="Bulk price reimport")
    except Exception as exc:
        return ImportOperationActionResult(
            "error",
            f"Failed to start price reimport: {exc}",
        )
    return ImportOperationActionResult(
        "success",
        "Reimport of all processed price files started in background.",
    )


def process_supplier_price_payload(
    *,
    supplier,
    mapping,
    filename,
    payload,
    content_type="",
    source_label="link",
    source_url="",
    received_at=None,
):
    filename = filename or "downloaded_price.xlsx"
    imported_at = timezone.now()
    content_hash = hashlib.sha256(payload or b"").hexdigest()
    readable, readable_error = _validate_spreadsheet_payload(filename, payload or b"")
    if not readable:
        raise RuntimeError(f"Spreadsheet could not be opened: {readable_error}")

    existing = models.ImportFile.objects.filter(
        content_hash=content_hash,
        status=models.ImportStatus.PROCESSED,
        import_batch__supplier=supplier,
    ).first()
    if existing:
        seen_count = mark_import_batch_products_seen(
            existing.import_batch, seen_at=imported_at
        )
        models.EmailAttachmentDiagnostic.objects.create(
            supplier=supplier,
            import_file=existing,
            import_batch=existing.import_batch,
            message_id=source_url[:255],
            message_date=received_at or timezone.now(),
            sender=source_label[:300],
            subject="Price source link",
            filename=filename,
            content_type=content_type[:200],
            size_bytes=len(payload or b""),
            content_hash=content_hash,
            decision=models.AttachmentDecision.DUPLICATE,
            reason_code=models.AttachmentReason.DUPLICATE_HASH,
            message=f"Duplicate price source link file. Refreshed {seen_count} product(s).",
        )
        return {
            "status": "duplicate",
            "message": "Duplicate price file hash.",
            "filename": filename,
            "batch": existing.import_batch,
        }

    import_batch = models.ImportBatch.objects.create(
        supplier=supplier,
        status=models.ImportStatus.PENDING,
        received_at=imported_at,
        message_id=source_url[:255],
    )
    import_file = models.ImportFile.objects.create(
        import_batch=import_batch,
        mapping=mapping,
        file_kind=models.FileKind.PRICE,
        filename=filename,
        content_hash=content_hash,
        status=models.ImportStatus.PENDING,
    )
    import_file.file.save(filename, ContentFile(payload), save=True)
    try:
        process_import_file(import_file)
        import_file.status = models.ImportStatus.PROCESSED
        import_file.save(update_fields=["status"])
        import_batch.status = models.ImportStatus.PROCESSED
        import_batch.save(update_fields=["status"])
        models.EmailAttachmentDiagnostic.objects.create(
            supplier=supplier,
            import_batch=import_batch,
            import_file=import_file,
            message_id=source_url[:255],
            message_date=received_at or imported_at,
            sender=source_label[:300],
            subject="Price source link",
            filename=filename,
            content_type=content_type[:200],
            size_bytes=len(payload or b""),
            content_hash=content_hash,
            decision=models.AttachmentDecision.IMPORTED,
            message="Price source link imported successfully.",
        )
    except Exception as exc:
        reason_code = _reason_from_error(str(exc))
        try:
            if import_file.file:
                import_file.file.delete(save=False)
        except Exception:
            pass
        settings_obj = models.ImportSettings.get_solo()
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
        import_batch.status = models.ImportStatus.FAILED
        import_batch.error_message = str(exc)
        import_batch.save(update_fields=["status", "error_message"])
        models.EmailAttachmentDiagnostic.objects.create(
            supplier=supplier,
            import_batch=import_batch,
            import_file=import_file,
            message_id=source_url[:255],
            message_date=received_at or imported_at,
            sender=source_label[:300],
            subject="Price source link",
            filename=filename,
            content_type=content_type[:200],
            size_bytes=len(payload or b""),
            content_hash=content_hash,
            decision=models.AttachmentDecision.QUARANTINED,
            reason_code=reason_code,
            message=str(exc),
        )
        raise
    return {
        "status": "imported",
        "message": "Imported successfully.",
        "filename": filename,
        "batch": import_batch,
    }


def import_supplier_price_source(
    *,
    supplier,
    source,
    mapping,
    download_func=None,
    process_payload_func=process_supplier_price_payload,
    now_func=timezone.now,
):
    download_func = download_func or link_importer.download_price_source
    try:
        downloaded = download_func(source)
        result = process_payload_func(
            supplier=supplier,
            mapping=mapping,
            filename=downloaded.filename,
            payload=downloaded.payload,
            content_type=downloaded.content_type,
            source_label=f"{source.get_source_type_display()} / {downloaded.provider}",
            source_url=downloaded.source_url,
        )
    except Exception as exc:
        source.last_checked_at = now_func()
        source.last_status = "failed"
        source.last_message = str(exc)
        source.save(update_fields=["last_checked_at", "last_status", "last_message"])
        raise

    source.last_checked_at = now_func()
    source.last_status = result["status"]
    source.last_message = result["message"]
    source.last_filename = result["filename"]
    source.save(
        update_fields=[
            "last_checked_at",
            "last_status",
            "last_message",
            "last_filename",
        ]
    )
    return result


def run_supplier_price_source_import_action(
    supplier,
    source,
    *,
    mapping_func=latest_active_supplier_mapping,
    import_func=import_supplier_price_source,
) -> ImportOperationActionResult:
    mapping = mapping_func(supplier)
    if not mapping:
        return ImportOperationActionResult(
            "info",
            "Create or confirm the supplier price mapping first.",
            redirect_source="file",
        )

    try:
        result = import_func(
            supplier=supplier,
            source=source,
            mapping=mapping,
        )
    except Exception as exc:
        return ImportOperationActionResult(
            "error",
            f"{supplier.name}: link import failed. {exc}",
            redirect_source="link",
        )

    if result["status"] == "duplicate":
        return ImportOperationActionResult(
            "info",
            f"{supplier.name}: no change, duplicate file {result['filename']}.",
            redirect_source="link",
        )
    return ImportOperationActionResult(
        "success",
        f"{supplier.name}: imported {result['filename']} from link.",
        redirect_source="link",
    )


def run_supplier_price_source_create_action(
    supplier,
    form,
) -> ImportOperationActionResult:
    if not form.is_valid():
        return ImportOperationActionResult(
            "error",
            "Link source was not saved. Check the highlighted fields.",
            redirect_source="link",
        )

    source = form.save(commit=False)
    source.supplier = supplier
    source.save()
    return ImportOperationActionResult(
        "success",
        "Price link source saved.",
        redirect_source="link",
    )


def run_supplier_price_source_delete_action(source) -> ImportOperationActionResult:
    source.delete()
    return ImportOperationActionResult(
        "success",
        "Price link source deleted.",
        redirect_source="link",
    )
