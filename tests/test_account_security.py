import pytest
from allauth.account.models import EmailAddress
from allauth.mfa import app_settings as mfa_settings
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.urls import NoReverseMatch, reverse

from apps.auditlog.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "path",
    [
        "/accounts/signup/",
        "/accounts/email/",
        "/accounts/confirm-email/",
        "/accounts/password/reset/",
        "/accounts/password/reset/done/",
        "/accounts/password/reset/key/example-key/",
    ],
)
def test_self_service_account_lifecycle_routes_are_disabled(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "url_name",
    ["account_signup", "account_email", "account_reset_password"],
)
def test_disabled_account_routes_cannot_be_reversed(url_name):
    with pytest.raises(NoReverseMatch):
        reverse(url_name)


def test_login_has_no_registration_or_recovery_links(client):
    response = client.get("/accounts/login/")
    assert response.status_code == 200
    assert b"Forgot password" not in response.content
    assert b"Sign up" not in response.content
    assert b"managed by your company administrator" in response.content


def test_sidebar_has_separate_security_link_and_post_logout(client, manager):
    client.force_login(manager)
    response = client.get("/")
    assert response.status_code == 200
    assert b'href="/accounts/2fa/"' in response.content
    assert b'action="/accounts/logout/"' in response.content
    assert b'method="post"' in response.content
    assert b'href="/accounts/logout/"' not in response.content


def test_security_and_password_pages_use_panel_layout(client, manager):
    client.force_login(manager)
    security = client.get("/accounts/2fa/")
    assert security.status_code == 200
    assert b"Manage your password" in security.content
    assert b"Change password" in security.content
    assert b"Set up MFA" in security.content

    password = client.get("/accounts/password/change/")
    assert password.status_code == 200
    assert b"ACCOUNT SECURITY" in password.content
    assert b"Back" in password.content


def test_sidebar_logout_posts_immediately(client, manager):
    client.force_login(manager)
    response = client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response.url == "/accounts/login/"
    assert "_auth_user_id" not in client.session


def test_new_manager_requires_admin_assigned_password(client, admin_user):
    client.force_login(admin_user)
    response = client.post(
        "/managers/add/",
        {
            "email": "no-password@example.com",
            "first_name": "No",
            "last_name": "Password",
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert b"An initial password is required" in response.content


def test_mfa_activation_allows_unverified_email(client, manager):
    EmailAddress.objects.create(
        user=manager, email=manager.email, primary=True, verified=False
    )
    client.force_login(manager)
    client.post(
        "/accounts/reauthenticate/",
        {"password": "test-pass", "next": "/accounts/2fa/totp/activate/"},
    )
    response = client.get("/accounts/2fa/totp/activate/")
    assert response.status_code == 200
    assert b"Set up authenticator app" in response.content
    assert b"cannot activate" not in response.content
    assert mfa_settings.ALLOW_UNVERIFIED_EMAIL is True


def test_recovery_codes_are_revealed_only_once(client, manager):
    recovery = RecoveryCodes.activate(manager)
    first_code = recovery.get_unused_codes()[0]
    client.force_login(manager)
    client.post(
        "/accounts/reauthenticate/",
        {"password": "test-pass", "next": "/accounts/2fa/recovery-codes/"},
    )

    first_view = client.get("/accounts/2fa/recovery-codes/")
    assert first_view.status_code == 200
    assert first_code.encode() in first_view.content
    assert b"Save these codes now" in first_view.content

    second_view = client.get("/accounts/2fa/recovery-codes/")
    assert second_view.status_code == 200
    assert first_code.encode() not in second_view.content
    assert b"cannot be viewed again" in second_view.content
    assert mfa_settings.RECOVERY_CODES_SHOW_ONCE is True


@pytest.mark.parametrize(
    ("url_name", "path"),
    [
        ("mfa_deactivate_totp", "/accounts/2fa/totp/deactivate/"),
        (
            "mfa_generate_recovery_codes",
            "/accounts/2fa/recovery-codes/generate/",
        ),
    ],
)
def test_mfa_replacement_routes_are_disabled(client, manager, url_name, path):
    client.force_login(manager)
    with pytest.raises(NoReverseMatch):
        reverse(url_name)
    assert client.get(path).status_code == 404
    assert client.post(path).status_code == 404


def test_existing_mfa_cannot_be_reenrolled(client, manager):
    totp = TOTP.activate(manager, generate_totp_secret())
    original_data = totp.instance.data.copy()
    client.force_login(manager)

    response = client.get("/accounts/2fa/totp/activate/")

    assert response.status_code == 302
    assert response.url == "/accounts/2fa/"
    totp.instance.refresh_from_db()
    assert totp.instance.data == original_data
    assert Authenticator.objects.filter(
        user=manager, type=Authenticator.Type.TOTP
    ).count() == 1


def test_manager_cannot_reset_mfa(client, manager):
    client.force_login(manager)
    response = client.post(reverse("manager_mfa_reset", args=[manager.pk]))
    assert response.status_code == 403


def test_admin_can_reset_manager_mfa(client, admin_user, manager):
    TOTP.activate(manager, generate_totp_secret())
    RecoveryCodes.activate(manager)
    client.force_login(admin_user)

    response = client.post(reverse("manager_mfa_reset", args=[manager.pk]))

    assert response.status_code == 302
    assert response.url == reverse("managers")
    assert not Authenticator.objects.filter(user=manager).exists()
    event = AuditEvent.objects.get(action="manager.mfa_reset")
    assert event.actor == admin_user
    assert event.target_id == str(manager.pk)
    assert event.metadata == {"authenticators_removed": 2}


def test_admin_sees_manager_mfa_status_and_reset_action(
    client, admin_user, manager
):
    TOTP.activate(manager, generate_totp_secret())
    client.force_login(admin_user)

    response = client.get(reverse("managers"))

    assert response.status_code == 200
    assert b"Configured" in response.content
    assert reverse("manager_mfa_reset", args=[manager.pk]).encode() in response.content
    assert b"Reset MFA" in response.content
