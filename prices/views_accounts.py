from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import UpdateView

from prices import forms
from prices.view_base import BaseCreateView, BaseDeleteView, BaseListView, BaseUpdateView, StaffRequiredMixin


class UserProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = get_user_model()
    form_class = forms.UserProfileForm
    template_name = "prices/form.html"

    def get_object(self, queryset=None):
        return self.request.user

    def get_success_url(self):
        next_url = self.request.GET.get("next", "").strip()
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return next_url
        if self.request.user.is_staff:
            return reverse_lazy("prices:dashboard")
        return reverse_lazy("viewer_home")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object_name"] = "Profile"
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(form, "password_changed", False):
            update_session_auth_hash(self.request, self.object)
        messages.success(self.request, "Profile updated.")
        return response


class UserListView(StaffRequiredMixin, BaseListView):
    model = get_user_model()
    list_display = ("username", "email", "is_staff", "is_active", "date_joined")
    list_title = "Users"
    create_url_name = "prices:user_create"
    update_url_name = "prices:user_update"
    delete_url_name = "prices:user_delete"
    detail_url_name = ""
    show_search = True
    ordering = ("username",)

    def get_queryset(self):
        queryset = super().get_queryset().order_by("username")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
            )
        return queryset


class UserCreateView(StaffRequiredMixin, BaseCreateView):
    model = get_user_model()
    form_class = forms.AppUserForm
    success_url_name = "prices:user_list"


class UserUpdateView(StaffRequiredMixin, BaseUpdateView):
    model = get_user_model()
    form_class = forms.AppUserForm
    success_url_name = "prices:user_list"


class UserDeleteView(StaffRequiredMixin, BaseDeleteView):
    model = get_user_model()
    success_url_name = "prices:user_list"

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.id == request.user.id:
            messages.error(request, "You cannot delete your own account.")
            return redirect("prices:user_list")
        return super().post(request, *args, **kwargs)


class UserGroupListView(StaffRequiredMixin, BaseListView):
    model = Group
    list_display = ("name",)
    list_title = "User Groups"
    create_url_name = "prices:user_group_create"
    update_url_name = "prices:user_group_update"
    delete_url_name = "prices:user_group_delete"
    detail_url_name = ""
    show_search = True
    ordering = ("name",)

    def get_queryset(self):
        queryset = super().get_queryset().order_by("name")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(name__icontains=query)
        return queryset


class UserGroupCreateView(StaffRequiredMixin, BaseCreateView):
    model = Group
    form_class = forms.AppGroupForm
    success_url_name = "prices:user_group_list"


class UserGroupUpdateView(StaffRequiredMixin, BaseUpdateView):
    model = Group
    form_class = forms.AppGroupForm
    success_url_name = "prices:user_group_list"


class UserGroupDeleteView(StaffRequiredMixin, BaseDeleteView):
    model = Group
    success_url_name = "prices:user_group_list"
