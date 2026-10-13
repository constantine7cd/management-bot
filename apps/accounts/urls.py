from allauth.account import views as account_views
from allauth.mfa.base import views as mfa_views
from allauth.mfa.recovery_codes import views as recovery_views
from django.urls import path

from .views import activate_mfa_once

urlpatterns = [
    path("login/", account_views.login, name="account_login"),
    path("logout/", account_views.logout, name="account_logout"),
    path("inactive/", account_views.account_inactive, name="account_inactive"),
    path(
        "reauthenticate/",
        account_views.reauthenticate,
        name="account_reauthenticate",
    ),
    path(
        "password/change/",
        account_views.password_change,
        name="account_change_password",
    ),
    path("2fa/", mfa_views.index, name="mfa_index"),
    path("2fa/authenticate/", mfa_views.authenticate, name="mfa_authenticate"),
    path("2fa/reauthenticate/", mfa_views.reauthenticate, name="mfa_reauthenticate"),
    path("2fa/totp/activate/", activate_mfa_once, name="mfa_activate_totp"),
    path(
        "2fa/recovery-codes/",
        recovery_views.view_recovery_codes,
        name="mfa_view_recovery_codes",
    ),
]
