from datetime import date
from decimal import Decimal

import pytest

from apps.leave_management.models import LeaveBalance, LeaveRequest, LeaveType
from apps.leave_management.services import (
    LeaveError,
    create_leave_request,
    review_leave,
)
from apps.organization.models import Holiday

pytestmark = pytest.mark.django_db


def test_schedule_aware_leave_and_balance(employee, manager):
    leave_type = LeaveType.objects.create(name_en="Vacation", name_ru="Отпуск")
    LeaveBalance.objects.create(employee=employee, leave_type=leave_type, year=2026, allocated_days=10)
    request = create_leave_request(employee, leave_type, date(2026, 10, 12), date(2026, 10, 16), "Trip")
    assert request.requested_days == Decimal(5)
    review_leave(request.id, manager, True)
    request.refresh_from_db()
    assert request.status == LeaveRequest.Status.APPROVED


def test_overlapping_leave_is_rejected(employee):
    leave_type = LeaveType.objects.create(name_en="Sick", name_ru="Больничный", requires_balance=False)
    create_leave_request(employee, leave_type, date(2026, 10, 12), date(2026, 10, 14))
    with pytest.raises(LeaveError, match="overlap"):
        create_leave_request(employee, leave_type, date(2026, 10, 14), date(2026, 10, 15))


def test_country_holiday_is_excluded_from_working_leave_days(employee):
    leave_type = LeaveType.objects.create(name_en="Vacation", name_ru="Отпуск")
    Holiday.objects.create(
        country_code="MD", date=date(2026, 10, 14), name="Company holiday"
    )
    request = create_leave_request(
        employee, leave_type, date(2026, 10, 12), date(2026, 10, 16)
    )
    assert request.requested_days == Decimal(4)
