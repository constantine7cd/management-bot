from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import LocationEvidence


@shared_task
def redact_old_locations():
    cutoff = timezone.now() - timedelta(days=settings.LOCATION_RETENTION_DAYS)
    return LocationEvidence.objects.filter(
        captured_at__lt=cutoff, redacted_at__isnull=True
    ).update(latitude=None, longitude=None, redacted_at=timezone.now())
