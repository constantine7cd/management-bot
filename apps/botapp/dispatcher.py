from aiogram import Bot, Dispatcher
from django.conf import settings

from .handlers import router

dispatcher = Dispatcher()
dispatcher.include_router(router)


def get_bot():
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    return Bot(settings.BOT_TOKEN)
