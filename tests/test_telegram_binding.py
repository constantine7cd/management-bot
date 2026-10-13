from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.db import IntegrityError, transaction

from apps.botapp.handlers import consume_invite
from apps.organization.models import Employee, EmployeeInvite

pytestmark = pytest.mark.django_db(transaction=True)


def telegram_message(user_id, username="worker"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, username=username),
        chat=SimpleNamespace(id=user_id + 1000),
    )


def test_invitation_rejects_id_owned_by_active_employee(admin_user):
    Employee.objects.create(
        first_name="Active",
        last_name="Owner",
        telegram_user_id=12345,
    )
    target = Employee.objects.create(first_name="New", last_name="Worker")
    invite = EmployeeInvite.issue(target, admin_user)

    result = async_to_sync(consume_invite)(invite.token, telegram_message(12345))

    assert result is None
    target.refresh_from_db()
    invite.refresh_from_db()
    assert target.telegram_user_id is None
    assert invite.used_at is None


def test_archived_employee_releases_telegram_id_for_new_active_employee(admin_user):
    archived = Employee.objects.create(
        first_name="Former",
        last_name="Worker",
        telegram_user_id=12345,
    )
    archived.archive()
    target = Employee.objects.create(first_name="New", last_name="Worker")
    invite = EmployeeInvite.issue(target, admin_user)

    result = async_to_sync(consume_invite)(
        invite.token, telegram_message(12345, "new_worker")
    )

    assert result.pk == target.pk
    target.refresh_from_db()
    invite.refresh_from_db()
    assert target.telegram_user_id == 12345
    assert target.telegram_username == "new_worker"
    assert invite.used_at is not None
    assert archived.telegram_user_id == target.telegram_user_id


def test_database_rejects_duplicate_id_between_active_employees():
    Employee.objects.create(
        first_name="First", last_name="Worker", telegram_user_id=555
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Employee.objects.create(
                first_name="Second", last_name="Worker", telegram_user_id=555
            )


def test_database_allows_duplicate_id_between_archived_employees():
    first = Employee.objects.create(
        first_name="First", last_name="Former", telegram_user_id=777
    )
    first.archive()
    second = Employee.objects.create(
        first_name="Second", last_name="Former", telegram_user_id=777
    )
    second.archive()

    assert Employee.objects.filter(telegram_user_id=777, is_active=False).count() == 2
