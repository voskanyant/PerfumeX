from __future__ import annotations

from collections.abc import Iterable, Sequence

from django.db.models import Max
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone

from prices import models


def latest_timestamp_from_rows(rows: Iterable[Sequence[object]]):
    latest = None
    for row in rows:
        candidate = next((value for value in row if value), None)
        if candidate and (latest is None or candidate > latest):
            latest = candidate
    return latest


def local_date_from_import_timestamps(received_at, created_at):
    timestamp = received_at or created_at
    if not timestamp:
        return None
    if timezone.is_aware(timestamp):
        return timezone.localtime(timestamp).date()
    return timestamp.date()


def collect_import_dates_from_batches(
    batches: Iterable,
    *,
    start_date=None,
    end_date=None,
) -> set:
    import_dates = set()
    for batch in batches:
        local_date = local_date_from_import_timestamps(
            getattr(batch, "received_at", None),
            getattr(batch, "created_at", None),
        )
        if not local_date:
            continue
        if start_date and local_date < start_date:
            continue
        if end_date and local_date > end_date:
            continue
        import_dates.add(local_date)
    return import_dates


def processed_price_import_batches(*, supplier_ids=None, batch_manager=None):
    batch_manager = batch_manager or models.ImportBatch.objects
    batches = batch_manager.filter(
        importfile__file_kind=models.FileKind.PRICE,
        importfile__status=models.ImportStatus.PROCESSED,
    ).distinct()
    if supplier_ids:
        batches = batches.filter(supplier_id__in=supplier_ids)
    return batches


def get_supplier_latest_price_batch_time(supplier: models.Supplier):
    rows = models.ImportBatch.objects.filter(
        supplier=supplier,
        importfile__status=models.ImportStatus.PROCESSED,
        importfile__file_kind=models.FileKind.PRICE,
    ).values_list("received_at", "created_at")
    return latest_timestamp_from_rows(rows)


def get_supplier_latest_processed_price_import_time(supplier: models.Supplier):
    rows = models.ImportBatch.objects.filter(
        supplier=supplier,
        importfile__status=models.ImportStatus.PROCESSED,
        importfile__file_kind=models.FileKind.PRICE,
    ).values_list("importfile__processed_at", "created_at", "received_at")
    return latest_timestamp_from_rows(rows)


def build_import_detail_context(import_batch, request) -> dict:
    import_files = import_batch.importfile_set.all().order_by("id")
    updated_at = import_files.aggregate(updated_at=Max("processed_at")).get(
        "updated_at"
    )
    back_url = request.GET.get("next", "").strip()
    if not (
        back_url
        and url_has_allowed_host_and_scheme(
            back_url,
            allowed_hosts={request.get_host()},
        )
    ):
        back_url = reverse_lazy("prices:supplier_overview")
    return {
        "import_files": import_files,
        "received_at_display": import_batch.received_at or import_batch.created_at,
        "updated_at_display": updated_at or import_batch.created_at,
        "back_url": back_url,
    }
