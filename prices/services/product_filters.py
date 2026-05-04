from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.db.models import Case, DecimalField, ExpressionWrapper, F, Q, Value, When

from prices import models
from prices.services.product_visibility import (
    apply_hidden_product_keywords,
    normalize_hidden_product_keywords,
    parse_hidden_product_keywords,
)


VALID_SUPPLIER_PRODUCT_STATUSES = {"active", "inactive", "all"}
FRONT_FILTER_KEYS = (
    "q",
    "currency",
    "supplier",
    "include_inactive_suppliers",
    "status",
    "price_min",
    "price_max",
    "exclude",
    "smart",
)
SUPPLIER_PRODUCT_SORT_FIELDS = (
    "supplier",
    "supplier_sku",
    "name",
    "current_price",
    "last_imported_at",
)
SUPPLIER_PRODUCT_LIST_FIELDS = (
    "id",
    "supplier_id",
    "supplier__name",
    "supplier_sku",
    "name",
    "currency",
    "current_price",
    "last_imported_at",
    "is_active",
)


@dataclass(frozen=True)
class SupplierProductFilterState:
    query: str
    include_tokens: list[str]
    inline_exclude_tokens: list[str]
    exclude_raw: str
    exclude_terms: list[str]
    currency: str
    supplier_filter_ids: list[int]
    include_inactive_suppliers: bool
    status_filter: str
    smart_search_enabled: bool


@dataclass(frozen=True)
class SupplierProductOrderingPlan:
    ordering: tuple[str, ...]
    display_price_currency: str


@dataclass(frozen=True)
class SupplierProductQueryResult:
    queryset: object
    filter_state: SupplierProductFilterState
    ordering_plan: SupplierProductOrderingPlan
    price_min_raw: str
    price_max_raw: str


def supplier_product_base_queryset(*, product_manager=None):
    manager = product_manager or models.SupplierProduct.objects
    return manager.all().select_related("supplier").only(*SUPPLIER_PRODUCT_LIST_FIELDS)


def parse_search_query(raw: str) -> tuple[list[str], list[str]]:
    include_tokens: list[str] = []
    exclude_tokens: list[str] = []
    for token in re.split(r"\s+", (raw or "").strip()):
        cleaned = token.strip()
        if not cleaned:
            continue
        if cleaned.startswith("-") and len(cleaned) > 1:
            exclude_tokens.append(cleaned[1:])
        else:
            include_tokens.append(cleaned)
    return include_tokens, exclude_tokens


def normalize_exclude_terms(raw: str) -> str:
    return normalize_hidden_product_keywords(raw)


def parse_exclude_terms(raw: str) -> list[str]:
    return parse_hidden_product_keywords(raw)


def resolve_supplier_exclude_terms(request) -> str:
    raw_from_query = request.GET.get("exclude")
    if raw_from_query is None:
        if not request.user.is_authenticated:
            return ""
        return (
            models.UserPreference.get_for_user(request.user).supplier_exclude_terms
            or ""
        )
    normalized = normalize_exclude_terms(raw_from_query)
    if request.user.is_authenticated:
        prefs = models.UserPreference.get_for_user(request.user)
        if (prefs.supplier_exclude_terms or "") != normalized:
            prefs.supplier_exclude_terms = normalized
            prefs.save(update_fields=["supplier_exclude_terms", "updated_at"])
    return normalized


def collect_front_filter_values(request) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in FRONT_FILTER_KEYS:
        if key == "supplier":
            supplier_values = [val for val in request.GET.getlist("supplier") if val]
            raw = ",".join(supplier_values)
        else:
            raw = (request.GET.get(key, "") or "").strip()
        values[key] = raw.strip() if isinstance(raw, str) else str(raw or "").strip()
    return values


def has_front_filter_params(request) -> bool:
    return any(key in request.GET for key in FRONT_FILTER_KEYS)


def save_front_filters_for_user(request) -> None:
    if not request.user.is_authenticated:
        return
    prefs = models.UserPreference.get_for_user(request.user)
    filters = collect_front_filter_values(request)
    prefs.supplier_front_filters = filters
    if "exclude" in request.GET:
        prefs.supplier_exclude_terms = normalize_exclude_terms(
            filters.get("exclude", "")
        )
    prefs.save(
        update_fields=["supplier_front_filters", "supplier_exclude_terms", "updated_at"]
    )


def resolve_viewer_front_filter_redirect_url(
    request,
    *,
    preferences_getter=None,
    save_func=save_front_filters_for_user,
) -> str:
    if not request.user.is_authenticated:
        return ""
    if has_front_filter_params(request):
        save_func(request)
        return ""

    get_preferences = preferences_getter or models.UserPreference.get_for_user
    saved = get_preferences(request.user).supplier_front_filters or {}
    if not isinstance(saved, dict):
        return ""
    clean = {
        key: (saved.get(key, "") or "").strip()
        for key in FRONT_FILTER_KEYS
        if isinstance(saved.get(key, ""), str) and (saved.get(key, "") or "").strip()
    }
    if not clean:
        return ""
    return f"{request.path}?{urlencode(clean)}"


def apply_supplier_product_token_filter(queryset, include_tokens: list[str]):
    tokens = [token.strip() for token in include_tokens if token.strip()][:6]
    if not tokens:
        return queryset

    for token in tokens:
        queryset = queryset.filter(
            Q(name__icontains=token)
            | Q(supplier_sku__icontains=token)
            | Q(supplier__name__icontains=token)
        )
    return queryset


def smart_search_enabled_from_request(request) -> bool:
    raw = (request.GET.get("smart") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def include_inactive_suppliers_from_request(request) -> bool:
    raw = (request.GET.get("include_inactive_suppliers") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def supplier_product_filter_state_from_request(request) -> SupplierProductFilterState:
    query = request.GET.get("q", "").strip()
    include_tokens, inline_exclude_tokens = parse_search_query(query)
    exclude_raw = resolve_supplier_exclude_terms(request)
    return SupplierProductFilterState(
        query=query,
        include_tokens=include_tokens,
        inline_exclude_tokens=inline_exclude_tokens,
        exclude_raw=exclude_raw,
        exclude_terms=parse_exclude_terms(exclude_raw),
        currency=request.GET.get("currency", "").strip() or models.Currency.USD,
        supplier_filter_ids=supplier_filter_ids_from_request(request),
        include_inactive_suppliers=include_inactive_suppliers_from_request(request),
        status_filter=normalize_supplier_product_status(request.GET.get("status")),
        smart_search_enabled=smart_search_enabled_from_request(request),
    )


def apply_supplier_product_search_filter(
    queryset, query: str, include_tokens: list[str], request
):
    if query and smart_search_enabled_from_request(request):
        from assistant_linking.services.smart_search import apply_smart_supplier_search

        return apply_smart_supplier_search(queryset, query)
    return apply_supplier_product_token_filter(queryset, include_tokens)


def apply_supplier_product_filter_state(
    queryset, filter_state: SupplierProductFilterState, request
):
    queryset = apply_supplier_product_search_filter(
        queryset,
        filter_state.query,
        filter_state.include_tokens,
        request,
    )
    for term in filter_state.inline_exclude_tokens:
        queryset = queryset.exclude(name__icontains=term)
    if filter_state.supplier_filter_ids:
        queryset = queryset.filter(supplier_id__in=filter_state.supplier_filter_ids)
    if not filter_state.include_inactive_suppliers:
        queryset = queryset.filter(supplier__is_active=True)
    if filter_state.status_filter == "active":
        queryset = queryset.filter(is_active=True)
    elif filter_state.status_filter == "inactive":
        queryset = queryset.filter(is_active=False)
    return apply_hidden_product_keywords(queryset, filter_state.exclude_terms)


def parse_supplier_filter_ids(raw: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for token in re.split(r"[\s,]+", (raw or "").strip()):
        if not token:
            continue
        try:
            value = int(token)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    return ids


def supplier_filter_ids_from_request(request) -> list[int]:
    raw_values = request.GET.getlist("supplier")
    merged_raw = ",".join([val for val in raw_values if val])
    return parse_supplier_filter_ids(merged_raw)


def serialize_supplier_filter_ids(ids: list[int]) -> str:
    return ",".join(str(x) for x in ids)


def build_supplier_filter_names(supplier_filter_ids: list[int]) -> list[dict]:
    if not supplier_filter_ids:
        return []
    name_map = {
        supplier.id: supplier.name
        for supplier in models.Supplier.objects.filter(id__in=supplier_filter_ids)
    }
    return [
        {
            "id": supplier_id,
            "name": name_map.get(supplier_id, f"Supplier #{supplier_id}"),
        }
        for supplier_id in supplier_filter_ids
    ]


def build_supplier_product_filter_context(
    filter_state: SupplierProductFilterState,
    *,
    price_min_raw: str = "",
    price_max_raw: str = "",
) -> dict:
    supplier_options = models.Supplier.objects
    if not filter_state.include_inactive_suppliers:
        supplier_options = supplier_options.filter(is_active=True)
    return {
        "currency_filter": filter_state.currency,
        "currency_options": [choice[0] for choice in models.Currency.choices],
        "supplier_filter": serialize_supplier_filter_ids(
            filter_state.supplier_filter_ids
        ),
        "supplier_options": supplier_options.order_by("name"),
        "supplier_filter_names": build_supplier_filter_names(
            filter_state.supplier_filter_ids
        ),
        "include_inactive_suppliers": filter_state.include_inactive_suppliers,
        "status_filter": filter_state.status_filter,
        "status_options": [
            ("all", "All"),
            ("active", "Active"),
            ("inactive", "Inactive"),
        ],
        "smart_search_enabled": filter_state.smart_search_enabled,
        "exclude_terms": filter_state.exclude_raw,
        "price_min": price_min_raw,
        "price_max": price_max_raw,
    }


def parse_decimal_query_param(raw: str) -> Decimal | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def display_price_expression_for_currency(
    currency: str, rates: dict[tuple[str, str], Decimal]
):
    output_field = DecimalField(max_digits=14, decimal_places=6)
    display_price_expr = F("current_price")
    if currency not in {models.Currency.USD, models.Currency.RUB}:
        return display_price_expr

    usd_rub_rate = rates.get((models.Currency.USD, models.Currency.RUB))
    if not usd_rub_rate or usd_rub_rate <= 0:
        return display_price_expr

    rate_value = Value(usd_rub_rate)
    if currency == models.Currency.USD:
        return Case(
            When(currency=models.Currency.USD, then=F("current_price")),
            When(
                currency=models.Currency.RUB,
                then=ExpressionWrapper(
                    F("current_price") / rate_value,
                    output_field=output_field,
                ),
            ),
            default=F("current_price"),
            output_field=output_field,
        )

    return Case(
        When(currency=models.Currency.RUB, then=F("current_price")),
        When(
            currency=models.Currency.USD,
            then=ExpressionWrapper(
                F("current_price") * rate_value,
                output_field=output_field,
            ),
        ),
        default=F("current_price"),
        output_field=output_field,
    )


def apply_supplier_price_filter(
    queryset,
    *,
    price_min_raw: str,
    price_max_raw: str,
    currency: str,
    rates: dict[tuple[str, str], Decimal],
):
    price_min = parse_decimal_query_param(price_min_raw)
    price_max = parse_decimal_query_param(price_max_raw)
    if price_min is None and price_max is None:
        return queryset, price_min_raw, price_max_raw

    display_price_expr = display_price_expression_for_currency(currency, rates)
    queryset = queryset.annotate(display_price_filter=display_price_expr)
    if price_min is not None:
        queryset = queryset.filter(display_price_filter__gte=price_min)
    if price_max is not None:
        queryset = queryset.filter(display_price_filter__lte=price_max)
    return queryset, price_min_raw, price_max_raw


def normalize_supplier_product_status(value: str | None) -> str:
    status = (value or "").strip().lower() or "all"
    if status not in VALID_SUPPLIER_PRODUCT_STATUSES:
        return "all"
    return status


def supplier_product_ordering(
    *,
    sort_field: str | None,
    sort_dir: str | None,
    currency: str | None,
    status_filter: str | None,
    allowed_fields: tuple[str, ...],
) -> tuple[str, ...]:
    currency_code = (currency or "").strip() or models.Currency.USD
    status = normalize_supplier_product_status(status_filter)
    sort_map = {
        "supplier": "supplier__name",
        "supplier_sku": "supplier_sku",
        "name": "name",
        "current_price": (
            "display_price_sort"
            if currency_code in {models.Currency.USD, models.Currency.RUB}
            else "current_price"
        ),
        "last_imported_at": "last_imported_at",
    }
    selected_field = sort_field if sort_field in allowed_fields else "current_price"
    selected_dir = sort_dir if sort_field in allowed_fields else "asc"
    prefix = "-" if selected_dir == "desc" else ""
    sort_expr = f"{prefix}{sort_map.get(selected_field, 'current_price')}"
    if status == "all":
        return ("-is_active", sort_expr, "id")
    return (sort_expr, "id")


def supplier_product_ordering_plan(
    *,
    sort_field: str | None,
    sort_dir: str | None,
    currency: str | None,
    status_filter: str | None,
    allowed_fields: tuple[str, ...] = SUPPLIER_PRODUCT_SORT_FIELDS,
) -> SupplierProductOrderingPlan:
    selected_field = sort_field if sort_field in allowed_fields else "current_price"
    currency_code = (currency or "").strip() or models.Currency.USD
    display_price_currency = (
        currency_code
        if selected_field == "current_price"
        and currency_code in {models.Currency.USD, models.Currency.RUB}
        else ""
    )
    return SupplierProductOrderingPlan(
        ordering=supplier_product_ordering(
            sort_field=sort_field,
            sort_dir=sort_dir,
            currency=currency,
            status_filter=status_filter,
            allowed_fields=allowed_fields,
        ),
        display_price_currency=display_price_currency,
    )


def build_supplier_product_queryset_for_request(
    request,
    *,
    base_queryset=None,
    rates=None,
    allowed_fields: tuple[str, ...] = SUPPLIER_PRODUCT_SORT_FIELDS,
    fast_search_default_order: bool = False,
) -> SupplierProductQueryResult:
    filter_state = supplier_product_filter_state_from_request(request)
    if fast_search_default_order and not request.GET.get("sort"):
        ordering = ("supplier__name", "name", "id")
        if normalize_supplier_product_status(filter_state.status_filter) == "all":
            ordering = ("-is_active", *ordering)
        ordering_plan = SupplierProductOrderingPlan(
            ordering=ordering,
            display_price_currency="",
        )
    else:
        ordering_plan = supplier_product_ordering_plan(
            sort_field=request.GET.get("sort"),
            sort_dir=request.GET.get("dir", "asc"),
            currency=filter_state.currency,
            status_filter=filter_state.status_filter,
            allowed_fields=allowed_fields,
        )
    queryset = (
        base_queryset if base_queryset is not None else supplier_product_base_queryset()
    )
    if ordering_plan.display_price_currency:
        queryset = queryset.annotate(
            display_price_sort=display_price_expression_for_currency(
                ordering_plan.display_price_currency,
                rates or {},
            )
        )
    queryset = apply_supplier_product_filter_state(queryset, filter_state, request)
    queryset, price_min_raw, price_max_raw = apply_supplier_price_filter(
        queryset,
        price_min_raw=request.GET.get("price_min", ""),
        price_max_raw=request.GET.get("price_max", ""),
        currency=filter_state.currency,
        rates=rates or {},
    )
    if ordering_plan.ordering:
        queryset = queryset.order_by(*ordering_plan.ordering)
    return SupplierProductQueryResult(
        queryset=queryset,
        filter_state=filter_state,
        ordering_plan=ordering_plan,
        price_min_raw=price_min_raw,
        price_max_raw=price_max_raw,
    )
