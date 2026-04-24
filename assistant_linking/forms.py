from django import forms

from . import models


class BrandAliasForm(forms.ModelForm):
    class Meta:
        model = models.BrandAlias
        fields = ("brand", "alias_text", "normalized_alias", "supplier", "priority", "is_regex", "active")


class ProductAliasForm(forms.ModelForm):
    class Meta:
        model = models.ProductAlias
        fields = ("perfume", "brand", "alias_text", "canonical_text", "supplier", "concentration", "audience", "priority", "active")


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
    reparse_similar = forms.BooleanField(required=False, initial=True)
