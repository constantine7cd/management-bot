from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    employee_actor_id = models.PositiveBigIntegerField(null=True, blank=True)
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=80)
    target_id = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Audit events are immutable")
        return super().save(*args, **kwargs)


def record(action, target, *, actor=None, employee_actor_id=None, metadata=None, ip=None):
    safe_metadata = metadata or {}
    for sensitive in ("hourly_rate", "rate", "password", "token", "coordinates"):
        safe_metadata.pop(sensitive, None)
    return AuditEvent.objects.create(
        actor=actor,
        employee_actor_id=employee_actor_id,
        action=action,
        target_type=target._meta.label_lower,
        target_id=str(target.pk),
        metadata=safe_metadata,
        ip_address=ip,
    )
