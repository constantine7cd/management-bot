import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.botapp.dispatcher import get_bot


class Command(BaseCommand):
    help = "Configure the production Telegram webhook"

    def handle(self, *args, **options):
        if not settings.PUBLIC_BASE_URL or not settings.TELEGRAM_WEBHOOK_SECRET:
            raise CommandError("PUBLIC_BASE_URL and TELEGRAM_WEBHOOK_SECRET are required")

        async def configure():
            bot = get_bot()
            try:
                result = await bot.set_webhook(
                    f"{settings.PUBLIC_BASE_URL.rstrip('/')}/telegram/webhook/",
                    secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
                    allowed_updates=["message", "callback_query"],
                )
                self.stdout.write(self.style.SUCCESS(f"Webhook configured: {result}"))
            finally:
                await bot.session.close()

        asyncio.run(configure())
