from __future__ import annotations

from prices import forms, models
from prices.view_base import BaseCreateView, BaseDeleteView, BaseListView, BaseUpdateView


class ImportBatchListView(BaseListView):
    model = models.ImportBatch
    list_display = (
        "supplier",
        "mailbox",
        "message_id",
        "received_at",
        "status",
        "created_at",
    )
    create_url_name = "prices:import_batch_create"
    update_url_name = "prices:import_batch_update"
    delete_url_name = "prices:import_batch_delete"


class ImportBatchCreateView(BaseCreateView):
    model = models.ImportBatch
    form_class = forms.ImportBatchForm
    success_url_name = "prices:import_batch_list"


class ImportBatchUpdateView(BaseUpdateView):
    model = models.ImportBatch
    form_class = forms.ImportBatchForm
    success_url_name = "prices:import_batch_list"


class ImportBatchDeleteView(BaseDeleteView):
    model = models.ImportBatch
    success_url_name = "prices:import_batch_list"


class ImportFileListView(BaseListView):
    model = models.ImportFile
    list_display = (
        "import_batch",
        "mapping",
        "file_kind",
        "filename",
        "status",
        "processed_at",
    )
    create_url_name = "prices:import_file_create"
    update_url_name = "prices:import_file_update"
    delete_url_name = "prices:import_file_delete"


class ImportFileCreateView(BaseCreateView):
    model = models.ImportFile
    form_class = forms.ImportFileForm
    success_url_name = "prices:import_file_list"


class ImportFileUpdateView(BaseUpdateView):
    model = models.ImportFile
    form_class = forms.ImportFileForm
    success_url_name = "prices:import_file_list"


class ImportFileDeleteView(BaseDeleteView):
    model = models.ImportFile
    success_url_name = "prices:import_file_list"


class PriceSnapshotListView(BaseListView):
    model = models.PriceSnapshot
    list_display = ("supplier_product", "price", "currency", "recorded_at")
    create_url_name = "prices:price_snapshot_create"
    update_url_name = "prices:price_snapshot_update"
    delete_url_name = "prices:price_snapshot_delete"


class PriceSnapshotCreateView(BaseCreateView):
    model = models.PriceSnapshot
    form_class = forms.PriceSnapshotForm
    success_url_name = "prices:price_snapshot_list"


class PriceSnapshotUpdateView(BaseUpdateView):
    model = models.PriceSnapshot
    form_class = forms.PriceSnapshotForm
    success_url_name = "prices:price_snapshot_list"


class PriceSnapshotDeleteView(BaseDeleteView):
    model = models.PriceSnapshot
    success_url_name = "prices:price_snapshot_list"


class StockSnapshotListView(BaseListView):
    model = models.StockSnapshot
    list_display = ("supplier_product", "quantity", "recorded_at")
    create_url_name = "prices:stock_snapshot_create"
    update_url_name = "prices:stock_snapshot_update"
    delete_url_name = "prices:stock_snapshot_delete"


class StockSnapshotCreateView(BaseCreateView):
    model = models.StockSnapshot
    form_class = forms.StockSnapshotForm
    success_url_name = "prices:stock_snapshot_list"


class StockSnapshotUpdateView(BaseUpdateView):
    model = models.StockSnapshot
    form_class = forms.StockSnapshotForm
    success_url_name = "prices:stock_snapshot_list"


class StockSnapshotDeleteView(BaseDeleteView):
    model = models.StockSnapshot
    success_url_name = "prices:stock_snapshot_list"


class ExchangeRateListView(BaseListView):
    model = models.ExchangeRate
    list_display = (
        "rate_date",
        "from_currency",
        "to_currency",
        "rate",
        "source",
    )
    create_url_name = "prices:exchange_rate_create"
    update_url_name = "prices:exchange_rate_update"
    delete_url_name = "prices:exchange_rate_delete"


class ExchangeRateCreateView(BaseCreateView):
    model = models.ExchangeRate
    form_class = forms.ExchangeRateForm
    success_url_name = "prices:exchange_rate_list"


class ExchangeRateUpdateView(BaseUpdateView):
    model = models.ExchangeRate
    form_class = forms.ExchangeRateForm
    success_url_name = "prices:exchange_rate_list"


class ExchangeRateDeleteView(BaseDeleteView):
    model = models.ExchangeRate
    success_url_name = "prices:exchange_rate_list"
