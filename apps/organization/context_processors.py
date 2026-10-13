from django.conf import settings


def application_settings(request):
    return {"BOT_USERNAME": settings.BOT_USERNAME}
