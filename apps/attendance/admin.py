from django.contrib import admin

from .models import CorrectionRequest, LocationEvidence, Shift, TelegramUpdate

admin.site.register([Shift, LocationEvidence, CorrectionRequest, TelegramUpdate])
