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
        ("edp", "Eau de Parfum"),
        ("edt", "Eau de Toilette"),
        ("edc", "Eau de Cologne"),
        ("parfum", "Parfum"),
        ("extrait", "Extrait de Parfum"),
    )
    AUDIENCE_CHOICES = (
        ("", "Unknown"),
        ("men", "Men"),
        ("women", "Women"),
        ("unisex", "Unisex"),
    )

    brand_name = forms.CharField(max_length=200, required=True, label="Brand")
    product_name = forms.CharField(max_length=255, required=True, label="Scent name")
    concentration = forms.ChoiceField(choices=CONCENTRATION_CHOICES, required=False)
    size_ml = forms.DecimalField(max_digits=7, decimal_places=2, required=False, label="Size ml")
    audience = forms.ChoiceField(choices=AUDIENCE_CHOICES, required=False)
    alias_scope = forms.ChoiceField(choices=SCOPE_CHOICES, initial=SCOPE_GLOBAL)
    brand_alias_text = forms.CharField(max_length=255, required=False, label="Brand text in supplier row")
    product_alias_text = forms.CharField(max_length=255, required=False, label="Product text in supplier row")
    lock_parse = forms.BooleanField(required=False, initial=True)
    reparse_similar = forms.BooleanField(required=False, initial=True)
