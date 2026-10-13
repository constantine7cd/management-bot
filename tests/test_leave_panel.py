from datetime import date

import pytest

from apps.leave_management.models import LeaveBalance, LeaveRequest, LeaveType
from apps.leave_management.services import create_leave_request

pytestmark = pytest.mark.django_db


def test_manager_leave_history_is_company_wide(client, manager, employee):
    leave_type = LeaveType.objects.create(name_en="Vacation", name_ru="Отпуск")
    own = create_leave_request(
        employee, leave_type, date(2026, 10, 12), date(2026, 10, 13), "Trip"
    )
    from apps.organization.models import Employee, Team

    other_team = Team.objects.create(name="Another team")
    other_employee = Employee.objects.create(
        first_name="Ion", last_name="Munteanu", team=other_team
    )
    other = create_leave_request(
        other_employee,
        leave_type,
        date(2026, 11, 2),
        date(2026, 11, 3),
        "Other team trip",
    )
    client.force_login(manager)
    response = client.get("/leave/")
    assert response.status_code == 200
    assert own.reason.encode() in response.content
    assert other.reason.encode() in response.content


def test_admin_can_modify_day_off_and_balance(client, admin_user, employee):
    leave_type = LeaveType.objects.create(name_en="Vacation", name_ru="Отпуск")
    client.force_login(admin_user)
    balance_response = client.post(
        "/leave/balances/add/",
        {
            "employee": employee.id,
            "leave_type": leave_type.id,
            "year": 2026,
            "allocated_days": "20.00",
        },
    )
    assert balance_response.status_code == 302
    assert LeaveBalance.objects.get(employee=employee).allocated_days == 20

    day_off_response = client.post(
        "/leave/days/add/",
        {
            "employee": employee.id,
            "leave_type": leave_type.id,
            "start_date": "2026-11-02",
            "end_date": "2026-11-04",
            "reason": "Admin-entered vacation",
            "status": LeaveRequest.Status.APPROVED,
        },
    )
    assert day_off_response.status_code == 302
    day_off = LeaveRequest.objects.get(reason="Admin-entered vacation")
    assert day_off.requested_days == 3
    assert day_off.reviewed_by == admin_user
