import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def export_path(instance, filename):
    return f"private/exports/{instance.id}/{filename}"


def export_expiry():
    return timezone.now() + timezone.timedelta(hours=24)


class ExportJob(models.Model):
    class Kind(models.TextChoices):
        ATTENDANCE_CSV = "attendance_csv", "Attendance CSV"
        ATTENDANCE_XLSX = "attendance_xlsx", "Attendance XLSX"
        VACATIONS_CSV = "vacations_csv", "Vacations and days off CSV"
        VACATIONS_XLSX = "vacations_xlsx", "Vacations and days off XLSX"
        EMPLOYEES_XLSX = "employees_xlsx", "Employees with rates XLSX"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    filters = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    file = models.FileField(upload_to=export_path, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=export_expiry)

    class Meta:
        ordering = ["-created_at"]
