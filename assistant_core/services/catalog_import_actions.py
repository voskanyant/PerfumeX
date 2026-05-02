from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from assistant_core import forms
from assistant_core.services.catalog_importer import import_catalog_file


@dataclass(frozen=True)
class CatalogImportActionResult:
    success: bool
    message: str
    form: Any
    result: Any = None


def run_catalog_import_action(
    post_data,
    files,
    *,
    form_class=forms.CatalogImportForm,
    importer: Callable[..., Any] = import_catalog_file,
) -> CatalogImportActionResult:
    form = form_class(post_data, files)
    if not form.is_valid():
        return CatalogImportActionResult(
            success=False,
            message="Catalogue file was not imported.",
            form=form,
        )

    try:
        result = importer(
            form.cleaned_data["file"],
            create_aliases=form.cleaned_data["create_aliases"],
            update_existing=form.cleaned_data["update_existing"],
        )
    except ValueError as exc:
        return CatalogImportActionResult(
            success=False,
            message=str(exc),
            form=form,
        )

    return CatalogImportActionResult(
        success=True,
        message=f"Imported {result.rows_imported} catalogue rows.",
        form=form_class(),
        result=result,
    )
