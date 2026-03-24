from django.utils import timezone
from django.shortcuts import redirect


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

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            if not user.is_staff:
                return redirect("/")
        return self.get_response(request)
