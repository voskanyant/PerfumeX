from __future__ import annotations

from django.views.generic import TemplateView

from assistant_core.services.dashboard import build_dashboard_context
from assistant_core.view_mixins import StaffAssistantMixin


class DashboardView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/dashboard.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), **build_dashboard_context()}
