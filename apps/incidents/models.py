from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from apps.organization.models import Employee


class Incident(models.Model):
    class Severity(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        EMERGENCY = "emergency", "Emergency"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"

    employee = models.ForeignKey(Employee, related_name="incidents", on_delete=models.PROTECT)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.NORMAL)
    category = models.CharField(max_length=80)
    description = models.TextField()
    attachment = models.FileField(
        upload_to="incidents/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "pdf"])],
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    resolution = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
