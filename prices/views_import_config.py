from __future__ import annotations

from prices import forms, models
from prices.view_base import BaseCreateView, BaseDeleteView, BaseListView, BaseUpdateView


class MailboxListView(BaseListView):
    model = models.Mailbox
    list_display = ("priority", "name", "protocol", "host", "username", "is_active")
    ordering = ("priority", "id")
    create_url_name = "prices:mailbox_create"
    update_url_name = "prices:mailbox_update"
    delete_url_name = "prices:mailbox_delete"
    show_action_menu = False


class MailboxCreateView(BaseCreateView):
    model = models.Mailbox
    form_class = forms.MailboxForm
    success_url_name = "prices:mailbox_list"


class MailboxUpdateView(BaseUpdateView):
    model = models.Mailbox
    form_class = forms.MailboxForm
    success_url_name = "prices:mailbox_list"


class MailboxDeleteView(BaseDeleteView):
    model = models.Mailbox
    success_url_name = "prices:mailbox_list"


class SupplierMailboxRuleListView(BaseListView):
    model = models.SupplierMailboxRule
    list_display = (
        "supplier",
        "mailbox",
        "from_pattern",
        "subject_pattern",
        "filename_pattern",
        "match_price_files",
        "match_stock_files",
        "is_active",
    )
    create_url_name = "prices:mailbox_rule_create"
    update_url_name = "prices:mailbox_rule_update"
    delete_url_name = "prices:mailbox_rule_delete"


class SupplierMailboxRuleCreateView(BaseCreateView):
    model = models.SupplierMailboxRule
    form_class = forms.SupplierMailboxRuleForm
    success_url_name = "prices:mailbox_rule_list"


class SupplierMailboxRuleUpdateView(BaseUpdateView):
    model = models.SupplierMailboxRule
    form_class = forms.SupplierMailboxRuleForm
    success_url_name = "prices:mailbox_rule_list"


class SupplierMailboxRuleDeleteView(BaseDeleteView):
    model = models.SupplierMailboxRule
    success_url_name = "prices:mailbox_rule_list"


class SupplierFileMappingListView(BaseListView):
    model = models.SupplierFileMapping
    list_display = (
        "supplier",
        "file_kind",
        "mapping_mode",
        "sheet_name",
        "sheet_index",
        "is_active",
    )
    create_url_name = "prices:mapping_create"
    update_url_name = "prices:mapping_update"
    delete_url_name = "prices:mapping_delete"


class SupplierFileMappingCreateView(BaseCreateView):
    model = models.SupplierFileMapping
    form_class = forms.SupplierFileMappingForm
    success_url_name = "prices:mapping_list"

    def get_initial(self):
        initial = super().get_initial()
        supplier_id = self.request.GET.get("supplier")
        if supplier_id:
            initial["supplier"] = supplier_id
        return initial


class SupplierFileMappingUpdateView(BaseUpdateView):
    model = models.SupplierFileMapping
    form_class = forms.SupplierFileMappingForm
    success_url_name = "prices:mapping_list"


class SupplierFileMappingDeleteView(BaseDeleteView):
    model = models.SupplierFileMapping
    success_url_name = "prices:mapping_list"
