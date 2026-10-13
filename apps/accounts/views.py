from allauth.mfa.models import Authenticator
from allauth.mfa.totp import views as totp_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def activate_mfa_once(request):
    if Authenticator.objects.filter(
        user=request.user, type=Authenticator.Type.TOTP
    ).exists():
        return redirect("mfa_index")
    return totp_views.activate_totp(request)
