from .base import *  # noqa: F403

if SECRET_KEY == "unsafe-development-key-change-me":  # noqa: F405
    raise RuntimeError("DJANGO_SECRET_KEY must be set in production")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
