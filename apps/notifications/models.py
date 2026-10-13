from django.db import models

from apps.organization.models import Employee


class Notification(models.Model):
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.CASCADE)
    channel = models.CharField(max_length=16, default="telegram")
    kind = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
