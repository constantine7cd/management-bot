import json

from aiogram.types import Update
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt

from apps.attendance.models import TelegramUpdate

from .dispatcher import dispatcher, get_bot


@csrf_exempt
async def telegram_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)
    if not settings.TELEGRAM_WEBHOOK_SECRET or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != settings.TELEGRAM_WEBHOOK_SECRET:
        return HttpResponseForbidden()
    payload = json.loads(request.body)
    update_id = payload.get("update_id")
    _, created = await TelegramUpdate.objects.aget_or_create(update_id=update_id)
    if created:
        bot = get_bot()
        try:
            await dispatcher.feed_update(bot, Update.model_validate(payload, context={"bot": bot}))
        finally:
            await bot.session.close()
    return HttpResponse("ok")
