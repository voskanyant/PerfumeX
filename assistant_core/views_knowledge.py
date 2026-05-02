from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView, View

from assistant_core import forms, models
from assistant_core.services.knowledge import (
    ALIAS_SECTION_BRANDS,
    ALIAS_SECTION_CHOICES,
    ALIAS_SECTION_CONCENTRATIONS,
    ALIAS_SECTION_PRODUCTS,
    SECTION_BRAND_ALIASES,
    SECTION_CHOICES,
    SECTION_CONCENTRATION_ALIASES,
    SECTION_DECISIONS,
    SECTION_GARBAGE_KEYWORDS,
    SECTION_GLOBAL_RULES,
    SECTION_NOTES,
    SECTION_PARSER_TERMS,
    SECTION_PRODUCT_ALIASES,
    SECTION_SUPPLIER_RULES,
    build_aliases_context,
    build_knowledge_context,
)
from assistant_core.services.knowledge_actions import (
    create_garbage_keyword_rules,
    create_parser_term_rules,
    create_teaching_rule_from_decision,
    disable_rule,
)
from assistant_core.view_mixins import StaffAssistantMixin
from assistant_linking import forms as linking_forms


class KnowledgeView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/knowledge/index.html"
    paginate_by = 50

    SECTION_GARBAGE_KEYWORDS = SECTION_GARBAGE_KEYWORDS
    SECTION_PARSER_TERMS = SECTION_PARSER_TERMS
    SECTION_GLOBAL_RULES = SECTION_GLOBAL_RULES
    SECTION_SUPPLIER_RULES = SECTION_SUPPLIER_RULES
    SECTION_NOTES = SECTION_NOTES
    SECTION_BRAND_ALIASES = SECTION_BRAND_ALIASES
    SECTION_PRODUCT_ALIASES = SECTION_PRODUCT_ALIASES
    SECTION_CONCENTRATION_ALIASES = SECTION_CONCENTRATION_ALIASES
    SECTION_DECISIONS = SECTION_DECISIONS
    SECTION_CHOICES = SECTION_CHOICES
    default_section = SECTION_BRAND_ALIASES

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            build_knowledge_context(
                self.request.GET,
                default_section=self.default_section,
                paginate_by=self.paginate_by,
            )
        )
        return context


class RulesView(KnowledgeView):
    default_section = SECTION_GLOBAL_RULES


class AliasesView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/knowledge/aliases.html"
    paginate_by = 50

    SECTION_BRANDS = ALIAS_SECTION_BRANDS
    SECTION_PRODUCTS = ALIAS_SECTION_PRODUCTS
    SECTION_CONCENTRATIONS = ALIAS_SECTION_CONCENTRATIONS
    SECTION_CHOICES = ALIAS_SECTION_CHOICES

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            build_aliases_context(self.request.GET, paginate_by=self.paginate_by)
        )
        return context


class AliasManageMixin(StaffAssistantMixin):
    template_name = "assistant_core/knowledge/alias_form.html"
    success_section = "brands"

    def get_success_url(self):
        return (
            f"{reverse_lazy('assistant_core:aliases')}?section={self.success_section}"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_url"] = self.get_success_url()
        context["active_section"] = self.success_section
        return context


class BrandAliasCreateView(AliasManageMixin, CreateView):
    from assistant_linking.models import BrandAlias as _BrandAlias

    model = _BrandAlias
    form_class = linking_forms.BrandAliasForm
    template_name = "assistant_core/knowledge/alias_form.html"
    success_section = "brands"


class BrandAliasUpdateView(AliasManageMixin, UpdateView):
    from assistant_linking.models import BrandAlias as _BrandAlias

    model = _BrandAlias
    form_class = linking_forms.BrandAliasForm
    template_name = "assistant_core/knowledge/alias_form.html"
    success_section = "brands"


class BrandAliasDeleteView(StaffAssistantMixin, DeleteView):
    from assistant_linking.models import BrandAlias as _BrandAlias

    model = _BrandAlias
    template_name = "assistant_core/knowledge/alias_confirm_delete.html"

    def get_success_url(self):
        return f"{reverse_lazy('assistant_core:aliases')}?section=brands"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_url"] = self.get_success_url()
        return context


class ProductAliasCreateView(AliasManageMixin, CreateView):
    from assistant_linking.models import ProductAlias as _ProductAlias

    model = _ProductAlias
    form_class = linking_forms.ProductAliasForm
    success_section = "products"


class ProductAliasUpdateView(AliasManageMixin, UpdateView):
    from assistant_linking.models import ProductAlias as _ProductAlias

    model = _ProductAlias
    form_class = linking_forms.ProductAliasForm
    success_section = "products"


class ProductAliasDeleteView(StaffAssistantMixin, DeleteView):
    from assistant_linking.models import ProductAlias as _ProductAlias

    model = _ProductAlias
    template_name = "assistant_core/knowledge/alias_confirm_delete.html"

    def get_success_url(self):
        return f"{reverse_lazy('assistant_core:aliases')}?section=products"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_url"] = self.get_success_url()
        return context


class ConcentrationAliasCreateView(AliasManageMixin, CreateView):
    from assistant_linking.models import ConcentrationAlias as _ConcentrationAlias

    model = _ConcentrationAlias
    form_class = linking_forms.ConcentrationAliasForm
    success_section = "concentrations"


class ConcentrationAliasUpdateView(AliasManageMixin, UpdateView):
    from assistant_linking.models import ConcentrationAlias as _ConcentrationAlias

    model = _ConcentrationAlias
    form_class = linking_forms.ConcentrationAliasForm
    success_section = "concentrations"


class ConcentrationAliasDeleteView(StaffAssistantMixin, DeleteView):
    from assistant_linking.models import ConcentrationAlias as _ConcentrationAlias

    model = _ConcentrationAlias
    template_name = "assistant_core/knowledge/alias_confirm_delete.html"

    def get_success_url(self):
        return f"{reverse_lazy('assistant_core:aliases')}?section=concentrations"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_url"] = self.get_success_url()
        return context


class GlobalRuleCreateView(StaffAssistantMixin, CreateView):
    model = models.GlobalRule
    form_class = forms.GlobalRuleForm
    template_name = "assistant_core/form.html"
    success_url = reverse_lazy("assistant_core:knowledge")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class GlobalRuleUpdateView(StaffAssistantMixin, UpdateView):
    model = models.GlobalRule
    form_class = forms.GlobalRuleForm
    template_name = "assistant_core/form.html"
    success_url = reverse_lazy("assistant_core:knowledge")


class SupplierRuleCreateView(GlobalRuleCreateView):
    model = models.SupplierRule
    form_class = forms.SupplierRuleForm


class KnowledgeNoteCreateView(GlobalRuleCreateView):
    model = models.KnowledgeNote
    form_class = forms.KnowledgeNoteForm


class RuleDisableView(StaffAssistantMixin, View):
    def post(self, request, model_name, pk):
        model = models.GlobalRule if model_name == "global" else models.SupplierRule
        rule = get_object_or_404(model, pk=pk)
        result = disable_rule(rule, is_global=model_name == "global")
        messages.success(request, result.message)
        return redirect("assistant_core:knowledge")


class GarbageKeywordCreateView(StaffAssistantMixin, View):
    def post(self, request):
        result = create_garbage_keyword_rules(request.POST, request.user)
        if result.success:
            messages.success(request, result.message)
        else:
            messages.error(request, result.message)
        return redirect(
            f"{reverse_lazy('assistant_core:knowledge')}?section={result.section}"
        )


class ParserTermCreateView(StaffAssistantMixin, View):
    def post(self, request):
        result = create_parser_term_rules(request.POST, request.user)
        if result.success:
            messages.success(request, result.message)
        else:
            messages.error(request, result.message)
        return redirect(
            f"{reverse_lazy('assistant_core:knowledge')}?section={result.section}"
        )


class TeachFromDecisionView(StaffAssistantMixin, View):
    def post(self, request):
        from assistant_linking.models import ManualLinkDecision

        decision = get_object_or_404(
            ManualLinkDecision, pk=request.POST.get("decision_id")
        )
        create_teaching_rule_from_decision(request.POST, decision, request.user)
        messages.success(request, "Teaching rule draft created.")
        return redirect("assistant_core:knowledge")
