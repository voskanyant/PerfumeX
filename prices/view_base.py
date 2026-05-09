from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from prices.services.pagination import paginate_queryset_without_count


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return bool(self.request.user and self.request.user.is_staff)

    def handle_no_permission(self):
        messages.error(self.request, "You do not have access to user management.")
        return redirect("prices:dashboard")


class MutatingPermissionRequiredMixin(PermissionRequiredMixin):
    raise_exception = True


class ModelDeletePermissionMixin(MutatingPermissionRequiredMixin):
    def get_permission_required(self):
        model = getattr(self, "model", None)
        if not model:
            return super().get_permission_required()
        opts = model._meta
        return (f"{opts.app_label}.delete_{opts.model_name}",)


class BaseListView(LoginRequiredMixin, ListView):
    template_name = "prices/list.html"
    paginate_by = 50
    ordering = ("-id",)
    list_display: tuple[str, ...] = ()
    create_url_name = ""
    update_url_name = ""
    delete_url_name = ""
    detail_url_name = ""
    show_create = True
    show_actions = True
    show_action_menu = True
    inactive_divider_label = "Inactive records"
    use_countless_pagination = True
    total_count_singular = "record"
    total_count_plural = "records"

    def get_ordering(self):
        sort_field = self.request.GET.get("sort")
        sort_dir = self.request.GET.get("dir", "asc")
        if sort_field in self.list_display:
            prefix = "-" if sort_dir == "desc" else ""
            return (f"{prefix}{sort_field}",)
        return super().get_ordering()

    def paginate_queryset(self, queryset, page_size):
        if not self.use_countless_pagination:
            return super().paginate_queryset(queryset, page_size)
        page_number = self.kwargs.get(self.page_kwarg) or self.request.GET.get(
            self.page_kwarg
        )
        return paginate_queryset_without_count(
            queryset,
            page_number=page_number,
            page_size=page_size,
        )

    def get_total_count_display(self, context):
        page_obj = context.get("page_obj")
        paginator = context.get("paginator")
        total_count = getattr(paginator, "count", None) if paginator else None
        if total_count is not None:
            noun = (
                self.total_count_singular
                if total_count == 1
                else self.total_count_plural
            )
            return f"Total {total_count} {noun}"
        if not page_obj:
            return f"Showing 0 {self.total_count_plural}"
        end_index = page_obj.end_index() if hasattr(page_obj, "end_index") else 0
        has_next = page_obj.has_next() if hasattr(page_obj, "has_next") else False
        noun = (
            self.total_count_singular
            if end_index == 1 and not has_next
            else self.total_count_plural
        )
        suffix = "+" if has_next else ""
        return f"Showing {end_index}{suffix} {noun}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list_display"] = self.list_display
        context["list_title"] = getattr(
            self, "list_title", self.model._meta.verbose_name_plural.title()
        )
        paginator = context.get("paginator")
        context["total_count"] = getattr(paginator, "count", None)
        context["total_count_display"] = self.get_total_count_display(context)
        context["current_sort"] = self.request.GET.get("sort", "")
        context["current_dir"] = self.request.GET.get("dir", "asc")
        context["current_q"] = self.request.GET.get("q", "")
        context["create_url_name"] = self.create_url_name
        context["update_url_name"] = self.update_url_name
        context["delete_url_name"] = self.delete_url_name
        context["detail_url_name"] = self.detail_url_name
        context["show_create"] = self.show_create
        context["show_actions"] = self.show_actions
        context["show_action_menu"] = self.show_action_menu
        context["show_search"] = getattr(self, "show_search", False)
        context["inactive_divider_label"] = self.inactive_divider_label
        return context


class BaseCreateView(LoginRequiredMixin, CreateView):
    template_name = "prices/form.html"
    success_url_name = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object_name"] = self.model._meta.verbose_name.title()
        return context

    def get_success_url(self):
        return reverse_lazy(self.success_url_name)


class BaseUpdateView(LoginRequiredMixin, UpdateView):
    template_name = "prices/form.html"
    success_url_name = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object_name"] = self.model._meta.verbose_name.title()
        return context

    def get_success_url(self):
        return reverse_lazy(self.success_url_name)


class BaseDeleteView(ModelDeletePermissionMixin, LoginRequiredMixin, DeleteView):
    template_name = "prices/confirm_delete.html"
    success_url_name = ""

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return next_url
        return reverse_lazy(self.success_url_name)
