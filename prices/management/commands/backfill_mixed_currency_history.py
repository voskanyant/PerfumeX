from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from prices import models
from prices.services import importer
from prices.services.cbr_rates import fetch_cbr_usd_rub_rate


@dataclass
class ParsedMixedRow:
    sku: str
    name: str
    price: Decimal
    currency: str


def _parse_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "")
    match = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if match:
        text = match.group(0)
    text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _detect_currency(cell_value) -> str | None:
    if cell_value is None:
        return None
    text = str(cell_value).strip()
    if not text:
        return None
    upper = importer._fix_mojibake(text).upper()
    if "USD" in upper or "$" in upper:
        return models.Currency.USD
    if "RUB" in upper or "RUR" in upper or "РУБ" in upper or "₽" in upper:
        return models.Currency.RUB
    return None


def _iter_file_rows(path: Path, mapping: models.SupplierFileMapping):
    extension = path.suffix.lower()
    start_row = 1
    sheet_names = [
        name.strip() for name in (mapping.sheet_names or "").split(",") if name.strip()
    ]
    sheet_indexes = [
        int(index.strip())
        for index in (mapping.sheet_indexes or "").split(",")
        if index.strip().isdigit()
    ]
    if extension == ".csv":
        yield from importer._iter_rows_csv(path, start_row)
        return
    if extension == ".xlsx":
        if sheet_names or sheet_indexes:
            for name in sheet_names:
                yield from importer._iter_rows_xlsx(path, name, None, start_row)
            for index in sheet_indexes:
                yield from importer._iter_rows_xlsx(path, "", index, start_row)
        else:
            yield from importer._iter_rows_xlsx(
                path, mapping.sheet_name, mapping.sheet_index, start_row
            )
        return
    if extension == ".xls":
        if sheet_names or sheet_indexes:
            for name in sheet_names:
                yield from importer._iter_rows_xls_path(path, name, None, start_row)
            for index in sheet_indexes:
                yield from importer._iter_rows_xls_path(path, "", index, start_row)
        else:
            yield from importer._iter_rows_xls_path(
                path, mapping.sheet_name, mapping.sheet_index, start_row
            )
        return
    raise RuntimeError(f"Unsupported file type: {extension}")


def _parse_rows_with_currency(
    rows,
    sku_col: int,
    name_cols: list[int],
    price_col: int,
    currency_col: int,
    fallback_currency: str,
):
    sku_idx = sku_col - 1 if sku_col else None
    name_indexes = [value - 1 for value in name_cols if value]
    price_idx = price_col - 1
    currency_idx = currency_col - 1 if currency_col else None
    found_data = False
    skip_terms = ("итого", "итог", "доставка")

    for row in rows:
        max_index = max([price_idx, *name_indexes, sku_idx if sku_idx is not None else 0])
        if len(row) <= max_index:
            continue

        sku = ""
        if sku_idx is not None and sku_idx < len(row):
            sku = importer._normalize_sku(row[sku_idx])

        name_parts: list[str] = []
        for idx in name_indexes:
            value = row[idx] if idx < len(row) else None
            text = str(value).strip() if value is not None else ""
            text = importer._fix_mojibake(text).strip()
            if text and not re.match(r"^-?\d+(?:[.,]\d+)?$", text):
                name_parts.append(text)
        name = " ".join(name_parts).strip()
        if name:
            lowered = name.lower()
            if any(term in lowered for term in skip_terms):
                if not found_data:
                    continue
                continue
            if importer._is_invalid_short_name(name):
                if not found_data:
                    continue
                continue

        raw_price_cell = row[price_idx] if price_idx < len(row) else None
        price = _parse_decimal(raw_price_cell)
        if not name or price is None or price == 0:
            if not found_data:
                continue
            continue

        currency = None
        if currency_idx is not None and currency_idx < len(row):
            currency = _detect_currency(row[currency_idx])
        if not currency:
            currency = _detect_currency(raw_price_cell)
        currency = currency or fallback_currency
        found_data = True
        yield ParsedMixedRow(sku=sku, name=name, price=price, currency=currency)


class Command(BaseCommand):
    help = (
        "Backfill supplier history for mixed USD/RUB price files. "
        "USD rows are converted to target currency using CBR previous-day rate "
        "plus configurable markup."
    )

    def add_arguments(self, parser):
        parser.add_argument("--supplier-id", type=int, required=True)
        parser.add_argument("--start-date", type=str, required=True, help="YYYY-MM-DD")
        parser.add_argument("--end-date", type=str, help="YYYY-MM-DD")
        parser.add_argument(
            "--target-currency",
            type=str,
            default=models.Currency.RUB,
            choices=[models.Currency.RUB, models.Currency.USD],
        )
        parser.add_argument(
            "--fallback-currency",
            type=str,
            default=models.Currency.RUB,
            choices=[models.Currency.RUB, models.Currency.USD],
        )
        parser.add_argument("--usd-markup-percent", type=Decimal, default=Decimal("2.7"))
        parser.add_argument(
            "--replace-range",
            action="store_true",
            help="Delete existing snapshots in range for this supplier before backfill.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def _parse_date(self, value: str, field_name: str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError as exc:
            raise CommandError(f"Invalid {field_name}: {value}") from exc

    def _local_date(self, dt):
        if dt is None:
            return timezone.localdate()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return timezone.localtime(dt).date()

    def _get_or_fetch_cbr_usd_rub(self, rate_date):
        if not hasattr(self, "_cbr_cache"):
            self._cbr_cache = {}
        if rate_date not in self._cbr_cache:
            existing = (
                models.ExchangeRate.objects.filter(
                    from_currency=models.Currency.USD,
                    to_currency=models.Currency.RUB,
                    rate_date__lte=rate_date,
                )
                .order_by("-rate_date", "-id")
                .first()
            )
            if existing:
                self._cbr_cache[rate_date] = existing.rate
            else:
                try:
                    self._cbr_cache[rate_date] = fetch_cbr_usd_rub_rate(rate_date)
                except Exception:
                    latest_any = (
                        models.ExchangeRate.objects.filter(
                            from_currency=models.Currency.USD,
                            to_currency=models.Currency.RUB,
                        )
                        .order_by("-rate_date", "-id")
                        .first()
                    )
                    if not latest_any:
                        raise
                    self._cbr_cache[rate_date] = latest_any.rate
        return self._cbr_cache[rate_date]

    def _convert_price(
        self, price: Decimal, from_currency: str, to_currency: str, received_at, usd_markup_percent: Decimal
    ) -> Decimal:
        if from_currency == to_currency:
            return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if from_currency == models.Currency.USD and to_currency == models.Currency.RUB:
            base_date = self._local_date(received_at) - timedelta(days=1)
            cbr_rate = self._get_or_fetch_cbr_usd_rub(base_date)
            multiplier = Decimal("1") + (usd_markup_percent / Decimal("100"))
            return (price * cbr_rate * multiplier).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        if from_currency == models.Currency.RUB and to_currency == models.Currency.USD:
            base_date = self._local_date(received_at) - timedelta(days=1)
            cbr_rate = self._get_or_fetch_cbr_usd_rub(base_date)
            if cbr_rate == 0:
                raise CommandError(f"Invalid CBR rate 0 for {base_date.isoformat()}")
            return (price / cbr_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        raise CommandError(f"Unsupported conversion: {from_currency}->{to_currency}")

    def _dual_prices(
        self,
        price: Decimal,
        from_currency: str,
        received_at,
        usd_markup_percent: Decimal,
    ):
        rub_value = self._convert_price(
            price,
            from_currency,
            models.Currency.RUB,
            received_at,
            usd_markup_percent,
        )
        usd_value = self._convert_price(
            price,
            from_currency,
            models.Currency.USD,
            received_at,
            usd_markup_percent,
        )
        return rub_value, usd_value

    def handle(self, *args, **options):
        supplier = models.Supplier.objects.filter(id=options["supplier_id"]).first()
        if not supplier:
            raise CommandError("Supplier not found.")

        start_date = self._parse_date(options["start_date"], "start-date")
        end_date = (
            self._parse_date(options["end_date"], "end-date")
            if options.get("end_date")
            else timezone.localdate()
        )
        if start_date > end_date:
            raise CommandError("start-date cannot be greater than end-date.")

        target_currency = options["target_currency"]
        fallback_currency = options["fallback_currency"]
        try:
            usd_markup_percent = Decimal(str(options["usd_markup_percent"]))
        except Exception as exc:
            raise CommandError("Invalid usd-markup-percent value.") from exc
        dry_run = options["dry_run"]

        files_qs = (
            models.ImportFile.objects.select_related("import_batch", "mapping")
            .filter(
                import_batch__supplier=supplier,
                file_kind=models.FileKind.PRICE,
                status=models.ImportStatus.PROCESSED,
            )
            .filter(
                Q(import_batch__received_at__date__gte=start_date)
                | (
                    Q(import_batch__received_at__isnull=True)
                    & Q(import_batch__created_at__date__gte=start_date)
                )
            )
            .filter(
                Q(import_batch__received_at__date__lte=end_date)
                | (
                    Q(import_batch__received_at__isnull=True)
                    & Q(import_batch__created_at__date__lte=end_date)
                )
            )
            .order_by("import_batch__received_at", "import_batch__created_at", "id")
        )
        files = list(files_qs)
        if not files:
            self.stdout.write(self.style.WARNING("No processed import files in range."))
            return

        if options["replace_range"] and not dry_run:
            models.PriceSnapshot.objects.filter(
                supplier_product__supplier=supplier,
                recorded_at__date__gte=start_date,
                recorded_at__date__lte=end_date,
            ).delete()

        stats = Counter()
        stats["files_total"] = len(files)
        stats["products_created"] = 0
        stats["products_updated"] = 0
        stats["snapshots_created"] = 0
        stats["snapshots_updated"] = 0
        conversion_error_examples = []

        for import_file in files:
            mapping = import_file.mapping or (
                models.SupplierFileMapping.objects.filter(
                    supplier=supplier, file_kind=models.FileKind.PRICE, is_active=True
                )
                .order_by("-id")
                .first()
            )
            if not mapping or not import_file.file:
                stats["files_skipped_no_mapping_or_file"] += 1
                continue

            column_map = mapping.column_map or {}
            sku_col = int(column_map.get("sku", 0))
            name_value = column_map.get("name", 0)
            if isinstance(name_value, list):
                name_cols = [int(value) for value in name_value if value]
            else:
                name_cols = [int(name_value)] if name_value else []
            price_col = int(column_map.get("price", 0))
            currency_col = int(column_map.get("currency", 0) or 0)
            if not name_cols or not price_col:
                stats["files_skipped_bad_mapping"] += 1
                continue

            path = Path(import_file.file.path)
            try:
                rows = _iter_file_rows(path, mapping)
                parsed_rows = list(
                    _parse_rows_with_currency(
                        rows=rows,
                        sku_col=sku_col,
                        name_cols=name_cols,
                        price_col=price_col,
                        currency_col=currency_col,
                        fallback_currency=fallback_currency,
                    )
                )
            except Exception:
                stats["files_failed_parse"] += 1
                continue

            unique_rows: dict[str, ParsedMixedRow] = {}
            for parsed in parsed_rows:
                key = importer._identity_key(parsed.sku, parsed.name)
                if key:
                    unique_rows[key] = parsed
            if not unique_rows:
                stats["files_skipped_empty"] += 1
                continue

            batch = import_file.import_batch
            batch_time = batch.received_at or batch.created_at or timezone.now()
            if timezone.is_naive(batch_time):
                batch_time = timezone.make_aware(batch_time)

            identity_keys = list(unique_rows.keys())
            existing_products = models.SupplierProduct.objects.filter(
                supplier=supplier, identity_key__in=identity_keys
            )
            existing_map = {p.identity_key: p for p in existing_products if p.identity_key}

            to_create = []
            to_update = []
            for key, row in unique_rows.items():
                try:
                    converted = self._convert_price(
                        row.price,
                        row.currency,
                        target_currency,
                        batch_time,
                        usd_markup_percent,
                    )
                    price_rub, price_usd = self._dual_prices(
                        row.price,
                        row.currency,
                        batch_time,
                        usd_markup_percent,
                    )
                except Exception:
                    stats["rows_failed_conversion"] += 1
                    if len(conversion_error_examples) < 5:
                        conversion_error_examples.append(
                            f"{row.sku or '-'} / {row.name[:80]} / {row.currency}->{target_currency}"
                        )
                    continue

                product = existing_map.get(key)
                if not product:
                    product = models.SupplierProduct(
                        supplier=supplier,
                        supplier_sku=row.sku,
                        identity_key=key,
                        name=row.name,
                        current_price=converted,
                        currency=target_currency,
                        last_imported_at=batch_time,
                        last_import_batch=batch,
                        created_import_batch=batch,
                        is_active=False,
                    )
                    to_create.append(product)
                    continue

                if row.sku and product.supplier_sku != row.sku:
                    product.supplier_sku = row.sku
                if product.name != row.name:
                    product.name = row.name
                if not product.last_imported_at or batch_time >= product.last_imported_at:
                    product.current_price = converted
                    product.currency = target_currency
                    product.last_imported_at = batch_time
                    product.last_import_batch = batch
                to_update.append(product)

            if dry_run:
                stats["products_created"] += len(to_create)
                stats["products_updated"] += len(to_update)
            else:
                if to_create:
                    models.SupplierProduct.objects.bulk_create(to_create, batch_size=500)
                    stats["products_created"] += len(to_create)
                if to_update:
                    models.SupplierProduct.objects.bulk_update(
                        to_update,
                        ["supplier_sku", "name", "current_price", "currency", "last_imported_at", "last_import_batch"],
                        batch_size=500,
                    )
                    stats["products_updated"] += len(to_update)

            refreshed_products = models.SupplierProduct.objects.filter(
                supplier=supplier, identity_key__in=identity_keys
            ).only("id", "identity_key")
            product_map = {p.identity_key: p for p in refreshed_products}

            existing_snapshots_by_product_id = {}
            if not dry_run:
                existing_snapshots = models.PriceSnapshot.objects.filter(import_batch=batch)
                existing_snapshots_by_product_id = {
                    snap.supplier_product_id: snap for snap in existing_snapshots
                }

            snapshots = []
            snapshots_to_update = []
            for key, row in unique_rows.items():
                product = product_map.get(key)
                if not product:
                    continue
                try:
                    converted = self._convert_price(
                        row.price,
                        row.currency,
                        target_currency,
                        batch_time,
                        usd_markup_percent,
                    )
                except Exception:
                    stats["rows_failed_conversion"] += 1
                    if len(conversion_error_examples) < 5:
                        conversion_error_examples.append(
                            f"{row.sku or '-'} / {row.name[:80]} / {row.currency}->{target_currency}"
                        )
                    continue
                price_rub, price_usd = self._dual_prices(
                    row.price,
                    row.currency,
                    batch_time,
                    usd_markup_percent,
                )
                existing_snapshot = existing_snapshots_by_product_id.get(product.id)
                if existing_snapshot and not dry_run:
                    existing_snapshot.price = converted
                    existing_snapshot.currency = target_currency
                    existing_snapshot.price_rub = price_rub
                    existing_snapshot.price_usd = price_usd
                    existing_snapshot.recorded_at = batch_time
                    snapshots_to_update.append(existing_snapshot)
                else:
                    snapshots.append(
                        models.PriceSnapshot(
                            supplier_product=product,
                            import_batch=batch,
                            price=converted,
                            currency=target_currency,
                            price_rub=price_rub,
                            price_usd=price_usd,
                            recorded_at=batch_time,
                        )
                    )
                stats[f"currency_{row.currency}"] += 1

            if dry_run:
                stats["snapshots_created"] += len(snapshots)
                stats["snapshots_updated"] += len(snapshots_to_update)
            elif snapshots:
                models.PriceSnapshot.objects.bulk_create(snapshots, batch_size=500)
                stats["snapshots_created"] += len(snapshots)
            if not dry_run and snapshots_to_update:
                models.PriceSnapshot.objects.bulk_update(
                    snapshots_to_update,
                    ["price", "currency", "price_rub", "price_usd", "recorded_at"],
                    batch_size=500,
                )
                stats["snapshots_updated"] += len(snapshots_to_update)

        mode = "DRY-RUN" if dry_run else "DONE"
        self.stdout.write(self.style.SUCCESS(f"{mode}: mixed-currency history backfill finished"))
        for key in sorted(stats.keys()):
            self.stdout.write(f"- {key}: {stats[key]}")
        for sample in conversion_error_examples:
            self.stdout.write(f"- conversion_error_sample: {sample}")
        summary_parts = [
            f"files_total={stats['files_total']}",
            f"snapshots_created={stats['snapshots_created']}",
            f"snapshots_updated={stats['snapshots_updated']}",
            f"rows_failed_conversion={stats['rows_failed_conversion']}",
            f"files_failed_parse={stats['files_failed_parse']}",
        ]
        self.stdout.write("SUMMARY " + " ".join(summary_parts))


