from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.organization.models import Employee, Workplace, WorkplaceShift


class Shift(models.Model):
    employee = models.ForeignKey(Employee, related_name="shifts", on_delete=models.PROTECT)
    started_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    start_workplace = models.ForeignKey(
        Workplace, related_name="started_shifts", on_delete=models.PROTECT
    )
    end_workplace = models.ForeignKey(
        Workplace, related_name="ended_shifts", null=True, blank=True, on_delete=models.PROTECT
    )
    workplace_shift = models.ForeignKey(
        WorkplaceShift,
        related_name="attendance_records",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    start_timezone = models.CharField(max_length=64, default="Europe/Chisinau")
    end_timezone = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=Q(ended_at__isnull=True),
                name="one_open_shift_per_employee",
            ),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gt=models.F("started_at")),
                name="shift_end_after_start",
            ),
        ]

    @property
    def duration(self):
        if not self.ended_at:
            return None
        return self.ended_at - self.started_at

    @property
    def local_started_at(self):
        return self.started_at.astimezone(ZoneInfo(self.start_timezone))

    @property
    def local_ended_at(self):
        if not self.ended_at:
            return None
        return self.ended_at.astimezone(ZoneInfo(self.end_timezone or self.start_timezone))


class LocationEvidence(models.Model):
    class Event(models.TextChoices):
        START = "start", "Shift start"
        END = "end", "Shift end"

    shift = models.ForeignKey(Shift, related_name="location_evidence", on_delete=models.CASCADE)
    event = models.CharField(max_length=8, choices=Event.choices)
    workplace = models.ForeignKey(Workplace, on_delete=models.PROTECT)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    accuracy_meters = models.FloatField(null=True, blank=True)
    distance_meters = models.FloatField()
    verified = models.BooleanField(default=False)
    captured_at = models.DateTimeField()
    redacted_at = models.DateTimeField(null=True, blank=True)


class CorrectionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    shift = models.ForeignKey(Shift, related_name="corrections", on_delete=models.PROTECT)
    requested_start = models.DateTimeField(null=True, blank=True)
    requested_end = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        start = self.requested_start or self.shift.started_at
        end = self.requested_end or self.shift.ended_at
        if end and end <= start:
            raise ValidationError("Requested end must be after requested start")


class TelegramUpdate(models.Model):
    update_id = models.BigIntegerField(unique=True)
    received_at = models.DateTimeField(auto_now_add=True)
