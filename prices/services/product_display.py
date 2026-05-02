from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta

from django.core.paginator import Paginator
from django.db.models import F, Window
from django.db.models.functions import RowNumber, TruncDate
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe

from prices import models
from prices.services.currency import (
    attach_display_prices,
    attach_previous_price_deltas,
    convert_price,
    format_price,
    get_latest_rates,
    get_rates_for_date,
    prime_rates_cache_for_dates,
)
from prices.services.product_filters import build_supplier_product_filter_context
from prices.services.product_filters import build_supplier_product_queryset_for_request
from prices.services.supplier_board import imported_age_class, short_relative_datetime


PRICE_CHART_CURRENCIES = {"original", "usd", "rub"}


def supplier_product_detail_back_url(
    *,
    next_url_raw: str | None = None,
    host: str = "",
    fallback_url_name: str,
) -> str:
    next_url = (next_url_raw or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={host} if host else set(),
    ):
        return next_url
    return reverse(fallback_url_name)


def parse_product_history_datetime(value: str | None):
    clean_value = (value or "").strip()
    if not clean_value:
        return None
    try:
        if len(clean_value) == 10:
            date_value = datetime.fromisoformat(clean_value).date()
            return timezone.make_aware(datetime.combine(date_value, time(0, 0)))
        dt_value = datetime.fromisoformat(clean_value)
        if timezone.is_naive(dt_value):
            return timezone.make_aware(dt_value)
        return dt_value
    except ValueError:
        return None


def expand_product_history_end_datetime(value: str | None, end_datetime):
    clean_value = (value or "").strip()
    if end_datetime and len(clean_value) == 10:
        return timezone.make_aware(
            datetime.combine(end_datetime.date(), time(23, 59, 59))
        )
    return end_datetime


def normalize_price_chart_currency(value: str | None) -> str:
    currency = (value or "original").strip().lower()
    if currency not in PRICE_CHART_CURRENCIES:
        return "original"
    return currency


def price_chart_currency_symbol(chart_currency: str) -> str:
    return {
        "original": "",
        "usd": "$",
        "rub": "\u20bd",
    }.get(chart_currency, "")


def latest_price_snapshot_per_day_queryset(snapshots, tz=None):
    timezone_info = tz or timezone.get_current_timezone()
    return (
        snapshots.annotate(local_day=TruncDate("recorded_at", tzinfo=timezone_info))
        .annotate(
            day_rank=Window(
                expression=RowNumber(),
                partition_by=[F("local_day")],
                order_by=[F("recorded_at").desc(), F("id").desc()],
            )
        )
        .filter(day_rank=1)
        .order_by("-recorded_at")
    )


def snapshot_local_dates(snapshots) -> set:
    return {timezone.localtime(snapshot.recorded_at).date() for snapshot in snapshots}


def build_price_history_chart(
    snapshots,
    chart_currency: str,
    rates_by_date_cache: dict,
) -> tuple[list[str], list[float]]:
    labels: list[str] = []
    values: list[float] = []
    normalized_currency = normalize_price_chart_currency(chart_currency)
    for snapshot in reversed(list(snapshots)):
        snapshot_date = timezone.localtime(snapshot.recorded_at).date()
        rates_for_snapshot = get_rates_for_date(snapshot_date, rates_by_date_cache)
        labels.append(timezone.localtime(snapshot.recorded_at).strftime("%d/%m/%Y"))

        chart_price = snapshot.price
        if normalized_currency == "usd":
            chart_price = snapshot.price_usd
            if chart_price is None:
                chart_price = convert_price(
                    snapshot.price,
                    snapshot.currency,
                    models.Currency.USD,
                    rates_for_snapshot,
                )
        elif normalized_currency == "rub":
            chart_price = snapshot.price_rub
            if chart_price is None:
                chart_price = convert_price(
                    snapshot.price,
                    snapshot.currency,
                    models.Currency.RUB,
                    rates_for_snapshot,
                )
        if chart_price is None:
            chart_price = snapshot.price
        values.append(float(chart_price))
    return labels, values


def attach_snapshot_display_prices(snapshots, rates_by_date_cache: dict) -> None:
    for snapshot in snapshots:
        snapshot_date = timezone.localtime(snapshot.recorded_at).date()
        rates_for_snapshot = get_rates_for_date(snapshot_date, rates_by_date_cache)
        usd_rub_rate = rates_for_snapshot.get(
            (models.Currency.USD, models.Currency.RUB)
        )
        display_rub = snapshot.price_rub
        if display_rub is None:
            display_rub = convert_price(
                snapshot.price,
                snapshot.currency,
                models.Currency.RUB,
                rates_for_snapshot,
            )
        display_usd = snapshot.price_usd
        if display_usd is None:
            display_usd = convert_price(
                snapshot.price,
                snapshot.currency,
                models.Currency.USD,
                rates_for_snapshot,
            )
        snapshot.display_price_rub = display_rub
        snapshot.display_price_usd = display_usd
        snapshot.display_exchange_rate = usd_rub_rate


def build_price_history_context(
    latest_by_day,
    *,
    history_page_raw,
    query_params,
    chart_currency: str,
    page_size: int = 100,
) -> dict:
    history_paginator = Paginator(latest_by_day, page_size)
    history_page_obj = history_paginator.get_page(history_page_raw)
    history_query_params = query_params.copy()
    history_query_params.pop("history_page", None)

    rates_by_date_cache: dict = {}
    prime_rates_cache_for_dates(
        snapshot_local_dates(latest_by_day), rates_by_date_cache
    )
    chart_labels, chart_values = build_price_history_chart(
        latest_by_day,
        chart_currency,
        rates_by_date_cache,
    )
    attach_snapshot_display_prices(history_page_obj.object_list, rates_by_date_cache)

    return {
        "snapshots": history_page_obj.object_list,
        "history_page_obj": history_page_obj,
        "history_is_paginated": history_page_obj.has_other_pages(),
        "history_querystring": history_query_params.urlencode(),
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "chart_currency": chart_currency,
        "chart_currency_symbol": price_chart_currency_symbol(chart_currency),
    }


def supplier_product_price_snapshot_queryset(product, *, snapshot_manager=None):
    manager = snapshot_manager or models.PriceSnapshot.objects
    return (
        manager.filter(supplier_product=product)
        .only(
            "id",
            "recorded_at",
            "price",
            "currency",
            "price_rub",
            "price_usd",
            "import_batch_id",
        )
        .order_by("-recorded_at")
    )


def build_supplier_product_detail_history_context(
    product,
    query_params,
    *,
    snapshot_manager=None,
    latest_per_day_func=latest_price_snapshot_per_day_queryset,
    history_context_builder=build_price_history_context,
) -> dict:
    start_value = query_params.get("start", "").strip()
    end_value = query_params.get("end", "").strip()
    chart_currency = normalize_price_chart_currency(
        query_params.get("chart_currency", "original")
    )
    start_dt = parse_product_history_datetime(start_value)
    end_dt = parse_product_history_datetime(end_value)
    snapshots = supplier_product_price_snapshot_queryset(
        product,
        snapshot_manager=snapshot_manager,
    )
    if start_dt:
        snapshots = snapshots.filter(recorded_at__gte=start_dt)
    if end_dt:
        snapshots = snapshots.filter(
            recorded_at__lte=expand_product_history_end_datetime(end_value, end_dt)
        )
    latest_by_day = list(latest_per_day_func(snapshots))
    context = history_context_builder(
        latest_by_day,
        history_page_raw=query_params.get("history_page"),
        query_params=query_params,
        chart_currency=chart_currency,
    )
    context["start_value"] = start_value
    context["end_value"] = end_value
    return context


def build_supplier_product_detail_context(
    product,
    query_params,
    *,
    next_url_raw: str | None,
    host: str,
    fallback_url_name: str,
    link_form_class=None,
    history_context_builder=build_supplier_product_detail_history_context,
) -> dict:
    if link_form_class is None:
        from prices.forms import SupplierProductLinkForm

        link_form_class = SupplierProductLinkForm

    context = {
        "back_url": supplier_product_detail_back_url(
            next_url_raw=next_url_raw,
            host=host,
            fallback_url_name=fallback_url_name,
        ),
        "link_form": link_form_class(instance=product),
        "our_product": product.our_product,
    }
    context.update(history_context_builder(product, query_params))
    return context


def build_supplier_product_list_context(
    context: dict,
    filter_state,
    *,
    price_min_raw: str = "",
    price_max_raw: str = "",
    show_currency_filter: bool = False,
    show_cleanup: bool = True,
    show_search: bool = False,
    link_detail: bool = False,
    show_status: bool = False,
    show_actions: bool = False,
    show_bulk_delete: bool = False,
    search_url="",
    detail_base_url="",
    filter_context_builder=build_supplier_product_filter_context,
    attach_display_func=None,
) -> dict:
    if attach_display_func is None:
        attach_display_func = attach_supplier_product_list_display

    context.update(
        filter_context_builder(
            filter_state,
            price_min_raw=price_min_raw,
            price_max_raw=price_max_raw,
        )
    )
    context["show_currency_filter"] = show_currency_filter
    context["show_cleanup"] = show_cleanup
    context["show_search"] = show_search
    context["link_detail"] = link_detail
    context["show_status"] = show_status
    context["show_actions"] = show_actions
    context["show_bulk_delete"] = show_bulk_delete
    context["search_url"] = search_url
    context["detail_base_url"] = detail_base_url
    attach_display_func(context["object_list"], filter_state.currency)
    return context


def build_supplier_product_sparklines(products) -> dict[int, list[float]]:
    product_ids = [product.id for product in products if getattr(product, "id", None)]
    if not product_ids:
        return {}

    six_months_ago = timezone.now() - timedelta(days=180)
    raw_snaps = list(
        models.PriceSnapshot.objects.filter(
            supplier_product_id__in=product_ids, recorded_at__gte=six_months_ago
        )
        .values("supplier_product_id", "price", "recorded_at")
        .order_by("supplier_product_id", "recorded_at")
    )
    product_day_prices: dict = defaultdict(dict)
    for snap in raw_snaps:
        pid = snap["supplier_product_id"]
        day = snap["recorded_at"].date()
        product_day_prices[pid][day] = float(snap["price"])
    return {
        pid: [value for _, value in sorted(days.items())]
        for pid, days in product_day_prices.items()
    }


def attach_supplier_product_list_display(products, currency: str) -> None:
    if currency:
        rates = get_latest_rates()
        attach_display_prices(products, currency, rates)
        attach_previous_price_deltas(products, currency, rates)

    sparklines = build_supplier_product_sparklines(products)
    for product in products:
        product.sparkline_values = sparklines.get(product.id, [])
        product.original_price_display = (
            format_price(product.current_price, product.currency)
            if product.current_price is not None
            else ""
        )
        product.sparkline_svg = render_product_sparkline_svg(
            product.sparkline_values,
            getattr(product, "price_delta_direction", ""),
        )


def parse_positive_page_number(raw) -> int:
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return 1


def build_supplier_product_search_response(
    queryset,
    *,
    page_raw,
    currency: str,
    page_size: int = 100,
) -> dict:
    page_number = parse_positive_page_number(page_raw)
    offset = (page_number - 1) * page_size
    rows = list(queryset[offset : offset + page_size + 1])
    has_next = len(rows) > page_size
    visible_products = rows[:page_size]
    has_previous = page_number > 1
    attach_supplier_product_list_display(visible_products, currency)

    items = [
        serialize_supplier_product_search_row(
            product,
            getattr(product, "sparkline_values", []),
        )
        for product in visible_products
    ]

    return {
        "count": None,
        "count_display": (
            f"{offset + len(items)}+" if has_next else str(offset + len(items))
        ),
        "shown": len(items),
        "page": page_number,
        "num_pages": None,
        "has_next": has_next,
        "has_previous": has_previous,
        "next_page": page_number + 1 if has_next else None,
        "previous_page": page_number - 1 if has_previous else None,
        "items": items,
    }


def build_supplier_product_search_payload(
    request,
    *,
    rates_getter=get_latest_rates,
    queryset_builder=build_supplier_product_queryset_for_request,
    response_builder=build_supplier_product_search_response,
) -> dict:
    query_result = queryset_builder(request, rates=rates_getter())
    return response_builder(
        query_result.queryset,
        page_raw=request.GET.get("page", "1"),
        currency=query_result.filter_state.currency,
    )


def render_product_sparkline_svg(values, delta_dir: str | None = None) -> str:
    width = 200
    height = 32
    pad = 3
    color = "#c8c8c8"
    if delta_dir == "down":
        color = "#22c55e"
    elif delta_dir == "up":
        color = "#ef4444"
    svg_open = (
        f'<svg class="product-sparkline" width="100%" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="none" fill="none" aria-hidden="true">'
    )
    if not values or len(values) < 2:
        mid = f"{height / 2:.1f}"
        return mark_safe(
            svg_open
            + f'<line x1="0" y1="{mid}" x2="{width}" y2="{mid}" stroke="#e2e2e2" stroke-width="1.5"/></svg>'
        )

    min_value = min(values)
    max_value = max(values)
    value_range = max_value - min_value or 1
    points = []
    for index, value in enumerate(values):
        x = pad + (index / (len(values) - 1)) * (width - pad * 2)
        y = (height - pad) - ((value - min_value) / value_range) * (height - pad * 2)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)
    return mark_safe(
        svg_open + f'<polyline points="{polyline}" stroke="{color}" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def serialize_supplier_product_search_row(product, sparkline_values=None) -> dict:
    imported_at = short_relative_datetime(product.last_imported_at)
    imported_at_full = (
        timezone.localtime(product.last_imported_at).strftime("%d.%m.%Y %H:%M")
        if product.last_imported_at
        else ""
    )
    original_price = (
        format_price(product.current_price, product.currency)
        if product.current_price is not None
        else ""
    )
    display_currency = getattr(product, "display_currency", product.currency)
    return {
        "id": product.id,
        "supplier": product.supplier.name,
        "supplier_id": product.supplier_id,
        "supplier_sku": product.supplier_sku,
        "name": product.name,
        "current_price": format_price(product.display_price, display_currency),
        "original_price": original_price,
        "last_imported_at": imported_at,
        "last_imported_at_full": imported_at_full,
        "last_imported_age_class": imported_age_class(product.last_imported_at),
        "is_active": product.is_active,
        "price_delta_direction": getattr(product, "price_delta_direction", ""),
        "price_delta_value": (
            format_price(product.price_delta_value, display_currency)
            if getattr(product, "price_delta_value", None) is not None
            else ""
        ),
        "price_delta_percent": (
            f"{product.price_delta_percent:.2f}%"
            if getattr(product, "price_delta_percent", None) is not None
            else ""
        ),
        "sparkline": sparkline_values or [],
    }
