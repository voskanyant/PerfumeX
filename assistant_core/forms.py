from django import forms

from . import models


class GlobalRuleForm(forms.ModelForm):
    class Meta:
        model = models.GlobalRule
        fields = ("title", "rule_kind", "scope_type", "scope_value", "rule_text", "examples_json", "priority", "confidence", "active", "approved")
        widgets = {"rule_text": forms.Textarea(attrs={"rows": 4}), "examples_json": forms.Textarea(attrs={"rows": 3})}


class SupplierRuleForm(forms.ModelForm):
    class Meta:
        model = models.SupplierRule
        fields = ("supplier", "brand", "title", "rule_kind", "applies_to_text", "rule_text", "examples_json", "priority", "confidence", "active", "approved")
        widgets = {"rule_text": forms.Textarea(attrs={"rows": 4}), "examples_json": forms.Textarea(attrs={"rows": 3})}


class KnowledgeNoteForm(forms.ModelForm):
    class Meta:
        model = models.KnowledgeNote
        fields = ("category", "title", "content", "supplier", "brand", "perfume", "active")
        widgets = {"content": forms.Textarea(attrs={"rows": 5})}


class BrandWatchProfileForm(forms.ModelForm):
    class Meta:
        model = models.BrandWatchProfile
        fields = ("brand", "official_url", "trusted_sources_json", "watch_frequency", "active", "instructions")
        widgets = {"trusted_sources_json": forms.Textarea(attrs={"rows": 4}), "instructions": forms.Textarea(attrs={"rows": 4})}
