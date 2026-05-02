from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import F, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from prices import models
from prices.services.cbr_rates import upsert_cbr_markup_rates
from prices.services.email_import_runs import parse_import_date_range
from prices.services.import_history import (
    collect_import_dates_from_batches,
    processed_price_import_batches,
)


@dataclass(frozen=True)
class RateSyncSummary:
    synced: int
    failed: int


@dataclass(frozen=True)
class ImportRateRecalculationResult:
    import_dates: set
    summary: RateSyncSummary | None


@dataclass(frozen=True)
class CurrencyActionResult:
    message_level: str
    message: str


def get_latest_rates() -> dict[tuple[str, str], Decimal]:
    today = timezone.localdate()
    rates = {}
    today_rates = models.ExchangeRate.objects.filter(rate_date=today).order_by("-id")
    for rate in today_rates:
        key = (rate.from_currency, rate.to_currency)
        if key not in rates:
            rates[key] = rate.rate
    if rates:
        return rates
    for rate in models.ExchangeRate.objects.order_by("-rate_date", "-id"):
        key = (rate.from_currency, rate.to_currency)
        if key not in rates:
            rates[key] = rate.rate
    return rates


def get_rates_for_date(
    rate_date,
    cache: dict,
) -> dict[tuple[str, str], Decimal]:
    if not rate_date:
        return {}
    if rate_date in cache:
        return cache[rate_date]
    rates: dict[tuple[str, str], Decimal] = {}
    for rate in models.ExchangeRate.objects.filter(rate_date__lte=rate_date).order_by(
        "-rate_date", "-id"
    ):
        key = (rate.from_currency, rate.to_currency)
        if key not in rates:
            rates[key] = rate.rate
    cache[rate_date] = rates
    return rates


def prime_rates_cache_for_dates(required_dates, cache: dict) -> None:
    if not required_dates:
        return
    missing_dates = sorted({d for d in required_dates if d and d not in cache})
    if not missing_dates:
        return

    max_date = missing_dates[-1]
    rate_rows = (
        models.ExchangeRate.objects.filter(rate_date__lte=max_date)
        .order_by("rate_date", "id")
        .values_list("rate_date", "from_currency", "to_currency", "rate")
    )

    current_rates: dict[tuple[str, str], Decimal] = {}
    idx = 0
    rows = list(rate_rows)
    rows_len = len(rows)

    for target_date in missing_dates:
        while idx < rows_len and rows[idx][0] <= target_date:
            _, from_currency, to_currency, rate = rows[idx]
            current_rates[(from_currency, to_currency)] = rate
            idx += 1
        cache[target_date] = dict(current_rates)


def convert_price(
    price: Decimal | None,
    from_currency: str,
    to_currency: str,
    rates: dict[tuple[str, str], Decimal],
) -> Decimal | None:
    if price is None or not from_currency or not to_currency:
        return price
    if from_currency == to_currency:
        return price
    direct = rates.get((from_currency, to_currency))
    if direct:
        return price * direct
    inverse = rates.get((to_currency, from_currency))
    if inverse and inverse != 0:
        return price / inverse
    return price


def format_price(price: Decimal | None, currency: str) -> str:
    if price is None:
        return "-"
    symbol = {"USD": "$", "RUB": "\u20bd"}.get((currency or "").upper(), currency)
    return f"{price:.2f} {symbol}"


def sync_cbr_markup_rates_for_dates(
    rate_dates,
    markup_percent,
    *,
    sync_func=upsert_cbr_markup_rates,
) -> RateSyncSummary:
    synced = 0
    failed = 0
    for rate_date in sorted(rate_dates):
        try:
            sync_func(rate_date, markup_percent)
            synced += 1
        except Exception:
            failed += 1
    return RateSyncSummary(synced=synced, failed=failed)


def recalculate_cbr_rates_for_processed_price_imports(
    *,
    markup_percent,
    supplier_ids=None,
    start_date=None,
    end_date=None,
    batch_manager=None,
    sync_func=upsert_cbr_markup_rates,
) -> ImportRateRecalculationResult:
    batches = processed_price_import_batches(
        supplier_ids=supplier_ids,
        batch_manager=batch_manager,
    )
    date_source = (
        batches.only("received_at", "created_at")
        if hasattr(batches, "only")
        else batches
    )
    import_dates = collect_import_dates_from_batches(
        date_source,
        start_date=start_date,
        end_date=end_date,
    )
    if not import_dates:
        return ImportRateRecalculationResult(import_dates=import_dates, summary=None)
    return ImportRateRecalculationResult(
        import_dates=import_dates,
        summary=sync_cbr_markup_rates_for_dates(
            import_dates,
            markup_percent,
            sync_func=sync_func,
        ),
    )


def run_supplier_rates_recalculation_action(
    supplier_ids,
    *,
    start_raw: str = "",
    end_raw: str = "",
    settings_func=None,
    recalculate_func=recalculate_cbr_rates_for_processed_price_imports,
) -> CurrencyActionResult:
    date_range = parse_import_date_range(
        start_raw=start_raw,
        end_raw=end_raw,
        validate_order=True,
    )
    if not date_range.is_valid:
        return CurrencyActionResult("info", date_range.error_message)

    settings_func = settings_func or models.ImportSettings.get_solo
    settings_obj = settings_func()
    result = recalculate_func(
        supplier_ids=supplier_ids,
        start_date=date_range.start_date,
        end_date=date_range.end_date,
        markup_percent=settings_obj.cbr_markup_percent,
    )
    if not result.import_dates:
        return CurrencyActionResult(
            "info",
            "No import dates found for selected filters.",
        )

    summary = result.summary
    if summary.failed:
        return CurrencyActionResult(
            "warning",
            f"Rate recalculation finished: {summary.synced} day(s) synced, "
            f"{summary.failed} failed.",
        )
    return CurrencyActionResult(
        "success",
        f"Rate recalculation finished: {summary.synced} day(s) synced.",
    )


def attach_display_prices(
    products,
    display_currency: str,
    rates: dict[tuple[str, str], Decimal],
) -> None:
    for product in products:
        product.display_currency = display_currency
        product.display_price = convert_price(
            product.current_price,
            product.currency,
            display_currency,
            rates,
        )


def attach_previous_price_deltas(
    products,
    display_currency: str,
    rates: dict[tuple[str, str], Decimal],
) -> None:
    product_list = list(products)
    if not product_list:
        return
    product_ids = [product.id for product in product_list]
    ranked = (
        models.PriceSnapshot.objects.filter(supplier_product_id__in=product_ids)
        .annotate(
            rn=Window(
                expression=RowNumber(),
                partition_by=[F("supplier_product_id")],
                order_by=[F("recorded_at").desc(), F("id").desc()],
            )
        )
        .filter(rn__lte=2)
        .values(
            "supplier_product_id",
            "rn",
            "price",
            "currency",
        )
    )
    snapshots_by_product: dict[int, dict[int, tuple[Decimal, str]]] = defaultdict(dict)
    for row in ranked:
        snapshots_by_product[row["supplier_product_id"]][row["rn"]] = (
            row["price"],
            row["currency"],
        )

    for product in product_list:
        product.price_delta_direction = ""
        product.price_delta_value = None
        product.price_delta_percent = None

        previous = snapshots_by_product.get(product.id, {}).get(2)
        if not previous:
            continue

        current_display = getattr(product, "display_price", None)
        if current_display is None:
            current_display = convert_price(
                product.current_price, product.currency, display_currency, rates
            )
        previous_price, previous_currency = previous
        previous_display = convert_price(
            previous_price, previous_currency, display_currency, rates
        )
        if current_display is None or previous_display is None:
            continue

        delta = current_display - previous_display
        if delta == 0:
            continue

        product.price_delta_direction = "up" if delta > 0 else "down"
        product.price_delta_value = abs(delta)
        if previous_display != 0:
            product.price_delta_percent = (abs(delta) / previous_display) * Decimal(
                "100"
            )
