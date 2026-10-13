import asyncio

from django.core.management.base import BaseCommand

from apps.botapp.dispatcher import dispatcher, get_bot


class Command(BaseCommand):
    help = "Run the Telegram bot using long polling (development only)"

    def handle(self, *args, **options):
        async def run():
            bot = get_bot()
            try:
                await bot.delete_webhook(drop_pending_updates=False)
                await dispatcher.start_polling(bot)
            finally:
                await bot.session.close()

        asyncio.run(run())
