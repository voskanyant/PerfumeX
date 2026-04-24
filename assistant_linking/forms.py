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
