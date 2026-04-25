from django import forms

from catalog import models as catalog_models

from . import models


class GlobalRuleForm(forms.ModelForm):
    class Meta:
        model = models.GlobalRule
        fields = ("title", "rule_kind", "scope_type", "scope_value", "rule_text", "examples_json", "priority", "confidence", "active", "approved")
        widgets = {"rule_text": forms.Textarea(attrs={"rows": 4}), "examples_json": forms.Textarea(attrs={"rows": 3})}

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if instance.rule_kind in {"garbage_keyword", "exclude_keyword"}:
            from assistant_linking.services.garbage import clear_garbage_keyword_cache

            clear_garbage_keyword_cache()
        return instance


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


class CatalogBrandForm(forms.ModelForm):
    class Meta:
        model = catalog_models.Brand
        fields = ("name", "country_of_origin", "official_url", "description", "is_active")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class CatalogPerfumeForm(forms.ModelForm):
    collection_name = forms.CharField(
        max_length=180,
        required=False,
        label="Collection / subname",
        help_text="Separate product line, for example Secret Garden. Do not include it inside the scent name.",
    )

    class Meta:
        model = catalog_models.Perfume
        fields = (
            "brand",
            "name",
            "concentration",
            "audience",
            "collection_name",
            "release_year",
            "perfumer_name",
            "country_of_manufacture",
            "verification_status",
            "is_published",
            "summary_short",
            "summary_long",
        )
        widgets = {
            "concentration": forms.TextInput(attrs={"list": "catalog-concentration-options"}),
            "audience": forms.TextInput(attrs={"list": "catalog-audience-options"}),
            "summary_short": forms.Textarea(attrs={"rows": 2}),
            "summary_long": forms.Textarea(attrs={"rows": 4}),
        }


class CatalogVariantForm(forms.ModelForm):
    class Meta:
        model = catalog_models.PerfumeVariant
        fields = ("perfume", "size_ml", "size_label", "packaging", "variant_type", "is_tester", "ean", "sku", "is_active")
        widgets = {
            "packaging": forms.TextInput(attrs={"list": "catalog-packaging-options"}),
            "variant_type": forms.TextInput(attrs={"list": "catalog-variant-type-options"}),
        }


class CatalogBrandMergeForm(forms.Form):
    source = forms.ModelChoiceField(queryset=catalog_models.Brand.objects.all(), label="Duplicate brand")
    target = forms.ModelChoiceField(queryset=catalog_models.Brand.objects.all(), label="Keep brand")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("source") and cleaned.get("target") and cleaned["source"] == cleaned["target"]:
            raise forms.ValidationError("Choose two different brands.")
        return cleaned


class CatalogPerfumeMergeForm(forms.Form):
    source = forms.ModelChoiceField(queryset=catalog_models.Perfume.objects.select_related("brand"), label="Duplicate perfume")
    target = forms.ModelChoiceField(queryset=catalog_models.Perfume.objects.select_related("brand"), label="Keep perfume")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("source") and cleaned.get("target") and cleaned["source"] == cleaned["target"]:
            raise forms.ValidationError("Choose two different perfumes.")
        return cleaned


class CatalogImportForm(forms.Form):
    file = forms.FileField(help_text="Upload .xlsx or .csv with at least brand and scent/name columns.")
    create_aliases = forms.BooleanField(required=False, initial=True, label="Create brand and product aliases")
    update_existing = forms.BooleanField(required=False, initial=True, label="Update empty fields on existing catalogue rows")
