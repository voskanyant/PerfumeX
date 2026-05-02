from __future__ import annotations

from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from prices import models
from prices.services.product_filters import parse_supplier_filter_ids


def delete_orphan_supplier_products(*, product_manager=None):
    manager = product_manager or models.SupplierProduct.objects
    return manager.filter(
        created_import_batch__isnull=True,
        last_import_batch__isnull=True,
    ).delete()


def delete_inactive_supplier_products(
    raw_supplier_filter: str, *, product_manager=None
):
    manager = product_manager or models.SupplierProduct.objects
    queryset = manager.filter(is_active=False)
    supplier_ids = parse_supplier_filter_ids(raw_supplier_filter)
    if supplier_ids:
        queryset = queryset.filter(supplier_id__in=supplier_ids)
    return queryset.delete()


def delete_supplier_products_by_ids(product_ids, *, product_manager=None):
    ids = list(product_ids)
    if not ids:
        return None
    manager = product_manager or models.SupplierProduct.objects
    return manager.filter(id__in=ids).delete()


def run_supplier_product_cleanup_action(
    *,
    delete_func=delete_orphan_supplier_products,
) -> str:
    delete_func()
    return reverse("prices:product_list")


def run_supplier_product_inactive_cleanup_action(
    raw_supplier_filter: str,
    *,
    delete_func=delete_inactive_supplier_products,
) -> str:
    delete_func(raw_supplier_filter)
    return reverse("prices:product_list")


def run_supplier_product_bulk_delete_action(
    product_ids,
    *,
    next_url_raw: str | None = None,
    host: str = "",
    delete_func=delete_supplier_products_by_ids,
) -> str:
    delete_func(product_ids)
    if next_url_raw and url_has_allowed_host_and_scheme(
        next_url_raw,
        allowed_hosts={host} if host else set(),
    ):
        return next_url_raw
    return reverse("prices:product_list")


def save_supplier_product_link_form(form):
    if form.is_valid():
        return form.save()
    return None


def run_supplier_product_link_action(
    product,
    post_data,
    *,
    form_class=None,
    save_func=save_supplier_product_link_form,
):
    if form_class is None:
        from prices.forms import SupplierProductLinkForm

        form_class = SupplierProductLinkForm

    form = form_class(post_data, instance=product)
    return save_func(form)
