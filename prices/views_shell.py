from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "prices/dashboard.html"


class DocumentationView(LoginRequiredMixin, TemplateView):
    template_name = "prices/documentation.html"
