import asyncio
from datetime import timedelta

from aiogram import Bot
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.attendance.models import Shift


async def _send(chat_id, text):
    async with Bot(settings.BOT_TOKEN) as bot:
        await bot.send_message(chat_id, text)


@shared_task
def send_open_shift_reminders():
    if not settings.BOT_TOKEN:
        return 0
    cutoff = timezone.now() - timedelta(hours=14)
    shifts = Shift.objects.select_related("employee").filter(
        ended_at__isnull=True, started_at__lt=cutoff, employee__telegram_chat_id__isnull=False
    )
    count = 0
    for shift in shifts:
        text = "Your shift is still open. Please check out if you have finished."
        if shift.employee.language == "ru":
            text = "Ваша смена всё ещё открыта. Завершите её, если вы закончили работу."
        asyncio.run(_send(shift.employee.telegram_chat_id, text))
        count += 1
    return count
