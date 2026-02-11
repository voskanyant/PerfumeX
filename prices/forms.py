from django import forms
from django.utils import timezone

from . import models


class SupplierForm(forms.ModelForm):
    class Meta:
        model = models.Supplier
        fields = (
            "name",
            "code",
            "is_active",
            "default_currency",
            "from_address_pattern",
            "notes",
        )
        labels = {
            "from_address_pattern": "Supplier email",
        }
        help_texts = {
            "from_address_pattern": "Email address used to receive this supplier's price lists.",
        }
        widgets = {
            "from_address_pattern": forms.TextInput(
                attrs={"placeholder": "supplier@example.com"}
            ),
        }


class MailboxForm(forms.ModelForm):
    class Meta:
        model = models.Mailbox
        fields = "__all__"
        widgets = {"password": forms.PasswordInput(render_value=True)}


class SupplierMailboxRuleForm(forms.ModelForm):
    class Meta:
        model = models.SupplierMailboxRule
        fields = "__all__"


class SupplierFileMappingForm(forms.ModelForm):
    class Meta:
        model = models.SupplierFileMapping
        fields = "__all__"
        widgets = {"column_map": forms.Textarea(attrs={"rows": 4})}


class SupplierProductForm(forms.ModelForm):
    class Meta:
        model = models.SupplierProduct
        fields = "__all__"


class SupplierProductLinkForm(forms.ModelForm):
    class Meta:
        model = models.SupplierProduct
        fields = ("our_product",)


class OurProductForm(forms.ModelForm):
    class Meta:
        model = models.OurProduct
        fields = "__all__"


class ImportBatchForm(forms.ModelForm):
    class Meta:
        model = models.ImportBatch
        fields = "__all__"


class ImportFileForm(forms.ModelForm):
    class Meta:
        model = models.ImportFile
        fields = "__all__"


class PriceSnapshotForm(forms.ModelForm):
    class Meta:
        model = models.PriceSnapshot
        fields = "__all__"


class StockSnapshotForm(forms.ModelForm):
    class Meta:
        model = models.StockSnapshot
        fields = "__all__"


class ExchangeRateForm(forms.ModelForm):
    class Meta:
        model = models.ExchangeRate
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and "rate_date" not in self.initial:
            self.initial["rate_date"] = timezone.localdate()


class ImportWizardForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=models.Supplier.objects.all())
    file_kind = forms.ChoiceField(choices=models.FileKind.choices)
    file = forms.FileField()

    def clean(self):
        cleaned_data = super().clean()
        supplier = cleaned_data.get("supplier")
        file_kind = cleaned_data.get("file_kind")
        if supplier and file_kind == models.FileKind.PRICE:
            mapping = models.SupplierFileMapping.objects.filter(
                supplier=supplier, file_kind=models.FileKind.PRICE, is_active=True
            ).order_by("-id").first()
            if not mapping:
                self.add_error(
                    None,
                    "Create price mapping on the supplier page before importing.",
                )
        return cleaned_data


class SupplierImportForm(forms.Form):
    file = forms.FileField()
    sheet_selector = forms.CharField(
        required=False,
        help_text="Sheet names or indexes (0-based), comma-separated. Example: Sheet1, 2, Sheet3.",
    )
    header_row = forms.IntegerField(required=False, min_value=1, initial=1)
    sku_column = forms.IntegerField(required=False, min_value=1)
    name_columns = forms.CharField(
        help_text="Comma-separated columns to concatenate, e.g. 3 or 3,4."
    )
    price_column = forms.IntegerField(min_value=1)
