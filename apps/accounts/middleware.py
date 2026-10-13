from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect


class PanelAccessMiddleware:
    """Refuse panel access to authenticated accounts without staff privileges."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        public = request.path.startswith(("/accounts/", "/static/", "/health/", "/telegram/"))
        if (
            request.user.is_authenticated
            and request.path.startswith("/django-admin/")
            and not request.user.is_company_admin
        ):
            return HttpResponseForbidden("Settings access is restricted to administrators")
        if request.user.is_authenticated and not request.user.is_staff and not public:
            return HttpResponseForbidden("Panel access is restricted")
        return self.get_response(request)


class MfaRequiredMiddleware:
    """Require staff to configure TOTP outside development/test environments."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        enforce = getattr(settings, "MFA_ENFORCE_STAFF", not settings.DEBUG)
        exempt = request.path.startswith(("/accounts/", "/static/", "/health/", "/telegram/"))
        if enforce and request.user.is_authenticated and request.user.is_staff and not exempt:
            try:
                from allauth.mfa.models import Authenticator

                if not Authenticator.objects.filter(user=request.user, type="totp").exists():
                    return redirect("mfa_activate_totp")
            except (ImportError, LookupError):
                pass
        return self.get_response(request)
