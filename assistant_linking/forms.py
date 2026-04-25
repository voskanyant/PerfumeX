from django import forms

from catalog.models import Brand, Perfume
from prices.models import Supplier

from . import models
from .utils.text import normalize_alias_value


class BrandAliasForm(forms.ModelForm):
    brand = forms.CharField(help_text="Existing brand name from Our Products.")
    normalized_alias = forms.CharField(required=False, help_text="Leave blank to auto-normalize from supplier text.")
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.order_by("name"), required=False)

    class Meta:
        model = models.BrandAlias
        fields = ("brand", "alias_text", "normalized_alias", "supplier", "priority", "is_regex", "active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.brand_id:
            self.fields["brand"].initial = self.instance.brand.name

    def clean_brand(self):
        brand_name = (self.cleaned_data.get("brand") or "").strip()
        brand = Brand.objects.filter(name__iexact=brand_name).first()
        if not brand:
            raise forms.ValidationError("Choose an existing brand name.")
        return brand

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("normalized_alias") and cleaned.get("alias_text"):
            cleaned["normalized_alias"] = normalize_alias_value(cleaned["alias_text"])
        return cleaned


class ProductAliasForm(forms.ModelForm):
    perfume = forms.CharField(required=False, help_text="Existing perfume name from Our Products. Optional.")
    brand = forms.CharField(required=False, help_text="Existing brand name from Our Products. Optional.")
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.order_by("name"), required=False)

    class Meta:
        model = models.ProductAlias
        fields = ("perfume", "brand", "alias_text", "canonical_text", "supplier", "concentration", "audience", "excluded_terms", "priority", "active")
        widgets = {"excluded_terms": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.brand_id:
            self.fields["brand"].initial = self.instance.brand.name
        if self.instance.pk and self.instance.perfume_id:
            self.fields["perfume"].initial = self.instance.perfume.name

    def clean_brand(self):
        brand_name = (self.cleaned_data.get("brand") or "").strip()
        if not brand_name:
            return None
        brand = Brand.objects.filter(name__iexact=brand_name).first()
        if not brand:
            raise forms.ValidationError("Choose an existing brand name or leave it blank.")
        return brand

    def clean_perfume(self):
        perfume_name = (self.cleaned_data.get("perfume") or "").strip()
        if not perfume_name:
            return None
        queryset = Perfume.objects.select_related("brand").filter(name__iexact=perfume_name)
        brand = self.cleaned_data.get("brand")
        if brand:
            queryset = queryset.filter(brand=brand)
        perfume = queryset.first()
        if not perfume:
            raise forms.ValidationError("Choose an existing perfume name or leave it blank.")
        if queryset.count() > 1:
            raise forms.ValidationError("Multiple catalogue perfumes use this name. Fill brand too to narrow it down.")
        return perfume

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("perfume") and not cleaned.get("brand"):
            cleaned["brand"] = cleaned["perfume"].brand
        return cleaned


class ConcentrationAliasForm(forms.ModelForm):
    normalized_alias = forms.CharField(required=False, help_text="Leave blank to auto-normalize from supplier text.")
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.order_by("name"), required=False)

    class Meta:
        model = models.ConcentrationAlias
        fields = ("concentration", "alias_text", "normalized_alias", "supplier", "priority", "is_regex", "active")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("normalized_alias") and cleaned.get("alias_text"):
            cleaned["normalized_alias"] = normalize_alias_value(cleaned["alias_text"])
        return cleaned


class ManualDecisionForm(forms.ModelForm):
    class Meta:
        model = models.ManualLinkDecision
        fields = ("decision_type", "perfume", "variant", "reason", "apply_to_similar")
        widgets = {"reason": forms.Textarea(attrs={"rows": 3})}


class ParseTeachingForm(forms.Form):
    SCOPE_GLOBAL = "global"
    SCOPE_SUPPLIER = "supplier"
    SCOPE_CHOICES = (
        (SCOPE_GLOBAL, "All suppliers"),
        (SCOPE_SUPPLIER, "Only this supplier"),
    )
    CONCENTRATION_CHOICES = (
        ("", "Unknown"),
        ("Eau de Parfum", "Eau de Parfum"),
        ("Eau de Toilette", "Eau de Toilette"),
        ("Eau de Cologne", "Eau de Cologne"),
        ("Parfum", "Parfum"),
        ("Extrait de Parfum", "Extrait de Parfum"),
    )
    AUDIENCE_CHOICES = (
        ("", "Unknown"),
        ("men", "Men"),
        ("women", "Women"),
        ("unisex", "Unisex"),
    )

    supplier_brand_text = forms.CharField(max_length=255, required=False, label="Supplier brand text")
    brand_name = forms.CharField(
        max_length=200,
        required=True,
        label="Correct brand",
        widget=forms.TextInput(attrs={"list": "catalog-brand-options", "autocomplete": "off"}),
    )
    supplier_product_text = forms.CharField(max_length=255, required=False, label="Supplier scent text")
    product_name = forms.CharField(
        max_length=255,
        required=True,
        label="Correct scent name",
        widget=forms.TextInput(attrs={"list": "catalog-perfume-options", "autocomplete": "off"}),
    )
    product_excluded_terms = forms.CharField(
        required=False,
        label="Do not match when supplier name contains",
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Comma-separated blockers for this brand and scent alias, for example: intense, love in capri, forever",
    )
    supplier_concentration_text = forms.CharField(max_length=80, required=False, label="Supplier concentration text")
    concentration = forms.ChoiceField(choices=CONCENTRATION_CHOICES, required=False, label="Correct concentration")
    supplier_size_text = forms.CharField(max_length=80, required=False, label="Supplier size text")
    size_ml = forms.DecimalField(max_digits=7, decimal_places=2, required=False, label="Correct size ml")
    supplier_audience_text = forms.CharField(max_length=80, required=False, label="Supplier audience text")
    audience = forms.ChoiceField(choices=AUDIENCE_CHOICES, required=False, label="Correct audience")
    supplier_type_text = forms.CharField(max_length=80, required=False, label="Supplier type text")
    variant_type = forms.CharField(
        max_length=80,
        required=False,
        label="Correct product type",
        widget=forms.TextInput(attrs={"list": "catalog-variant-type-options"}),
    )
    supplier_packaging_text = forms.CharField(max_length=80, required=False, label="Supplier packaging text")
    packaging = forms.CharField(
        max_length=80,
        required=False,
        label="Correct packaging",
        widget=forms.TextInput(attrs={"list": "catalog-packaging-options"}),
    )
    alias_scope = forms.ChoiceField(choices=SCOPE_CHOICES, initial=SCOPE_GLOBAL)
    lock_parse = forms.BooleanField(required=False, initial=True)
    reparse_similar = forms.BooleanField(required=False, initial=False)
