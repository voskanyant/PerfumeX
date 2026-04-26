import re

from django.utils import timezone
from django.shortcuts import redirect
from django.http import HttpResponseForbidden


class ForceMoscowTimezoneMiddleware:
    """Ensure all request-scoped datetime rendering uses Europe/Moscow."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        timezone.activate("Europe/Moscow")
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()


class AdminPanelStaffOnlyMiddleware:
    """Allow only staff users to open the custom admin panel under /admin/."""

    readonly_user_paths = {
        "/admin/suppliers/overview",
        "/admin/suppliers/import-email/status",
    }
    user_import_post_patterns = (
        re.compile(r"^/admin/suppliers/import-email$"),
        re.compile(r"^/admin/suppliers/\d+/import-email$"),
        re.compile(r"^/admin/suppliers/\d+/quick-upload$"),
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            if not user.is_staff:
                normalized_path = request.path.rstrip("/")
                if request.method == "POST" and any(
                    pattern.match(normalized_path)
                    for pattern in self.user_import_post_patterns
                ):
                    return self.get_response(request)
                if (
                    request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}
                    and normalized_path in self.readonly_user_paths
                ):
                    return self.get_response(request)
                if request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
                    return HttpResponseForbidden("Staff access required.")
                return redirect("/")
        return self.get_response(request)
