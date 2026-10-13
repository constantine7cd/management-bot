from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.organization.models import Employee


class LeaveType(models.Model):
    name_en = models.CharField(max_length=80)
    name_ru = models.CharField(max_length=80)
    requires_balance = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name_en


class LeaveBalance(models.Model):
    employee = models.ForeignKey(Employee, related_name="leave_balances", on_delete=models.PROTECT)
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    year = models.PositiveSmallIntegerField()
    allocated_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "leave_type", "year"], name="unique_leave_balance"
            )
        ]


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, related_name="leave_requests", on_delete=models.PROTECT)
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date = models.DateField()
    requested_days = models.DecimalField(max_digits=6, decimal_places=2)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")), name="leave_dates_valid"
            )
        ]

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError("End date must not precede start date")
