from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from apps.auditlog.models import record
from apps.organization.models import Holiday

from .models import LeaveBalance, LeaveRequest


class LeaveError(Exception):
    pass


def scheduled_days(employee, start_date, end_date):
    holidays = set(
        Holiday.objects.filter(
            country_code=employee.holiday_country,
            date__range=(start_date, end_date),
        ).values_list("date", flat=True)
    )
    weekdays = set(employee.schedule.days.values_list("weekday", flat=True)) if employee.schedule else set(range(5))
    total = 0
    current = start_date
    while current <= end_date:
        if current.weekday() in weekdays and current not in holidays:
            total += 1
        current += timezone.timedelta(days=1)
    return Decimal(total)


@transaction.atomic
def create_leave_request(employee, leave_type, start_date, end_date, reason=""):
    if end_date < start_date:
        raise LeaveError("End date must not precede start date")
    if employee.leave_requests.filter(
        status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED],
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).exists():
        raise LeaveError("The requested dates overlap existing leave")
    days = scheduled_days(employee, start_date, end_date)
    request = LeaveRequest.objects.create(
        employee=employee,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        requested_days=days,
        reason=reason,
    )
    record("leave.requested", request, employee_actor_id=employee.id)
    return request


@transaction.atomic
def review_leave(request_id, reviewer, approve, note=""):
    request = LeaveRequest.objects.select_for_update().select_related("employee", "leave_type").get(pk=request_id)
    if request.status != LeaveRequest.Status.PENDING:
        raise LeaveError("Leave request has already been reviewed")
    if approve and request.leave_type.requires_balance:
        balance = LeaveBalance.objects.select_for_update().filter(
            employee=request.employee, leave_type=request.leave_type, year=request.start_date.year
        ).first()
        approved = request.employee.leave_requests.filter(
            leave_type=request.leave_type,
            start_date__year=request.start_date.year,
            status=LeaveRequest.Status.APPROVED,
        ).aggregate(total=models.Sum("requested_days"))["total"] or Decimal(0)
        if not balance or balance.allocated_days - approved < request.requested_days:
            raise LeaveError("Insufficient leave balance")
    request.status = LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
    request.reviewed_by = reviewer
    request.review_note = note
    request.reviewed_at = timezone.now()
    request.save(update_fields=["status", "reviewed_by", "review_note", "reviewed_at"])
    record("leave.reviewed", request, actor=reviewer, metadata={"approved": approve})
    return request
