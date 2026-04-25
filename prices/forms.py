from django import forms
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission

from . import models
from .services.product_visibility import normalize_hidden_product_keywords


class SupplierForm(forms.ModelForm):
    class Meta:
        model = models.Supplier
        fields = (
            "name",
            "code",
            "is_active",
            "default_currency",
            "from_address_pattern",
            "expected_import_interval_hours",
            "notes",
        )
        labels = {
            "from_address_pattern": "Supplier email",
            "expected_import_interval_hours": "Expected weekday cadence (hours)",
        }
        help_texts = {
            "from_address_pattern": "Email address used to receive this supplier's price lists.",
            "expected_import_interval_hours": "Used on the Import Prices board. Daily suppliers use business-day grace, so Friday can remain healthy through the weekend.",
        }
        widgets = {
            "from_address_pattern": forms.TextInput(
                attrs={"placeholder": "supplier@example.com"}
            ),
            "expected_import_interval_hours": forms.NumberInput(
                attrs={"min": 1, "step": 1}
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
    rate_date = forms.DateField(
        input_formats=["%d/%m/%Y", "%Y-%m-%d"],
        widget=forms.TextInput(attrs={"placeholder": "dd/mm/yyyy"}),
    )

    class Meta:
        model = models.ExchangeRate
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.rate_date:
            self.initial["rate_date"] = self.instance.rate_date.strftime("%d/%m/%Y")
        elif "rate_date" not in self.initial:
            self.initial["rate_date"] = timezone.localdate().strftime("%d/%m/%Y")


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
    currency_column = forms.IntegerField(
        required=False,
        min_value=1,
        help_text="Optional currency column (RUB/USD/?). If empty, currency is detected from price cell or supplier default.",
    )


class ImportSettingsForm(forms.ModelForm):
    class Meta:
        model = models.ImportSettings
        fields = (
            "enabled",
            "interval_minutes",
            "auto_mark_seen",
            "max_messages_per_run",
            "supplier_timeout_minutes",
            "deactivate_products_after_days",
            "cbr_markup_percent",
            "filename_blacklist_terms",
        )
        labels = {
            "enabled": "Enable auto email checks",
            "interval_minutes": "Mailbox check interval (minutes)",
            "auto_mark_seen": "Mark imported emails as seen",
            "max_messages_per_run": "Max messages per run",
            "supplier_timeout_minutes": "Supplier timeout (minutes)",
            "deactivate_products_after_days": "Deactivate products after no price for (days)",
            "cbr_markup_percent": "CBR markup (%)",
            "filename_blacklist_terms": "Filename blacklist terms",
        }
        help_texts = {
            "interval_minutes": "How often to check all mailboxes for new price lists.",
            "auto_mark_seen": "Recommended on. Prevents re-reading the same unseen emails every run.",
            "max_messages_per_run": "Safety limit for one run to avoid long/stuck IMAP sessions.",
            "supplier_timeout_minutes": "Stop a supplier import if it exceeds this time. Set 0 to disable timeout.",
            "deactivate_products_after_days": "Set 0 to disable. Active supplier products older than this threshold are set inactive.",
            "cbr_markup_percent": "Applied to daily USD->RUB CBR rate (e.g. 3.0).",
            "filename_blacklist_terms": "If filename contains any term, the file is skipped. One term per line (or comma-separated).",
        }
        widgets = {
            "interval_minutes": forms.NumberInput(attrs={"min": 5, "step": 5}),
            "max_messages_per_run": forms.NumberInput(attrs={"min": 1, "step": 1}),
            "supplier_timeout_minutes": forms.NumberInput(attrs={"min": 0, "step": 1}),
            "deactivate_products_after_days": forms.NumberInput(attrs={"min": 0, "step": 1}),
            "cbr_markup_percent": forms.NumberInput(attrs={"min": 0, "step": 0.001}),
            "filename_blacklist_terms": forms.Textarea(attrs={"rows": 6}),
        }


class CBRMarkupForm(forms.Form):
    cbr_markup_percent = forms.DecimalField(
        min_value=0,
        max_digits=6,
        decimal_places=3,
        initial=3.0,
        widget=forms.NumberInput(attrs={"min": 0, "step": 0.001}),
        label="CBR markup (%)",
    )


class CBRSyncRangeForm(forms.Form):
    start_date = forms.DateField(
        required=True,
        input_formats=["%d/%m/%Y", "%Y-%m-%d"],
        widget=forms.TextInput(attrs={"placeholder": "dd/mm/yyyy"}),
        label="Start date",
    )
    end_date = forms.DateField(
        required=False,
        input_formats=["%d/%m/%Y", "%Y-%m-%d"],
        widget=forms.TextInput(attrs={"placeholder": "dd/mm/yyyy"}),
        label="End date",
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "End date must be on or after start date.")
        return cleaned_data


class AppUserForm(forms.ModelForm):
    password1 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Password",
        help_text="Required for new users. Leave empty on edit to keep current password.",
    )
    password2 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Confirm password",
    )

    class Meta:
        model = get_user_model()
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "is_staff",
            "groups",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = Group.objects.order_by("name")
        self.fields["groups"].required = False

    def clean(self):
        cleaned_data = super().clean()
        password1 = (cleaned_data.get("password1") or "").strip()
        password2 = (cleaned_data.get("password2") or "").strip()
        creating = not bool(getattr(self.instance, "pk", None))
        if creating and not password1:
            self.add_error("password1", "Password is required for new user.")
        if password1 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password1 = (self.cleaned_data.get("password1") or "").strip()
        if password1:
            user.set_password(password1)
        if commit:
            user.save()
            self.save_m2m()
        return user


class AppGroupForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 18}),
    )

    class Meta:
        model = Group
        fields = ("name", "permissions")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = Permission.objects.filter(
            content_type__app_label__in=("prices", "auth")
        ).order_by("content_type__app_label", "codename")


class UserProfileForm(forms.ModelForm):
    hidden_product_keywords = forms.CharField(
        required=False,
        label="Hidden product keywords",
        help_text="One keyword per line or comma-separated. Matching supplier products will be hidden across supplier lists, linking, and assistant queues.",
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "tester\nsample\nbody mist",
            }
        ),
    )
    current_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Current password",
    )
    new_password1 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="New password",
    )
    new_password2 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Confirm new password",
    )

    class Meta:
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if getattr(self.instance, "pk", None):
            prefs = models.UserPreference.get_for_user(self.instance)
            self.fields["hidden_product_keywords"].initial = (
                prefs.supplier_exclude_terms or ""
            )

    def clean(self):
        cleaned_data = super().clean()
        current_password = (cleaned_data.get("current_password") or "").strip()
        new_password1 = (cleaned_data.get("new_password1") or "").strip()
        new_password2 = (cleaned_data.get("new_password2") or "").strip()
        wants_change = bool(current_password or new_password1 or new_password2)
        if not wants_change:
            return cleaned_data
        if not current_password:
            self.add_error("current_password", "Enter current password to change password.")
        elif not self.instance.check_password(current_password):
            self.add_error("current_password", "Current password is incorrect.")
        if not new_password1:
            self.add_error("new_password1", "Enter new password.")
        if new_password1 and len(new_password1) < 6:
            self.add_error("new_password1", "New password must be at least 6 characters.")
        if new_password1 != new_password2:
            self.add_error("new_password2", "New passwords do not match.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        new_password1 = (self.cleaned_data.get("new_password1") or "").strip()
        self.password_changed = False
        if new_password1:
            user.set_password(new_password1)
            self.password_changed = True
        if commit:
            user.save()
            prefs = models.UserPreference.get_for_user(user)
            prefs.supplier_exclude_terms = normalize_hidden_product_keywords(
                self.cleaned_data.get("hidden_product_keywords", "")
            )
            prefs.save(update_fields=["supplier_exclude_terms", "updated_at"])
        return user
