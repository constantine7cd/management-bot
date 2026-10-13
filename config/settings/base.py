from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env(
    DEBUG=(bool, False),
    COMPANY_TIME_ZONE=(str, "Europe/Chisinau"),
    LOCATION_RETENTION_DAYS=(int, 90),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-development-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "apps.accounts",
    "apps.organization",
    "apps.attendance",
    "apps.leave_management",
    "apps.incidents",
    "apps.supervisor_documents",
    "apps.auditlog",
    "apps.reporting",
    "apps.botapp",
    "apps.notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.PanelAccessMiddleware",
    "apps.accounts.middleware.MfaRequiredMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.organization.context_processors.application_settings",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("ru", "Русский")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = env("COMPANY_TIME_ZONE")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "account_login"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_ADAPTER = "apps.accounts.adapter.ClosedAccountAdapter"
ACCOUNT_RATE_LIMITS = {"login_failed": "5/5m"}
ACCOUNT_LOGOUT_ON_GET = False
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_PASSKEY_LOGIN_ENABLED = False
MFA_TOTP_ISSUER = "Management Bot"
MFA_ALLOW_UNVERIFIED_EMAIL = True
MFA_RECOVERY_CODES_SHOW_ONCE = True

CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://localhost:6379/1")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_BEAT_SCHEDULE = {
    "open-shift-reminders": {
        "task": "apps.notifications.tasks.send_open_shift_reminders",
        "schedule": 900,
    },
    "expire-exports": {
        "task": "apps.reporting.tasks.expire_exports",
        "schedule": 3600,
    },
    "redact-location": {
        "task": "apps.attendance.tasks.redact_old_locations",
        "schedule": 86400,
    },
}

BOT_TOKEN = env("BOT_TOKEN", default="")
BOT_USERNAME = env("BOT_USERNAME", default="")
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="")
LOCATION_RETENTION_DAYS = env("LOCATION_RETENTION_DAYS")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@example.com")
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"format": '{{"time":"{asctime}","level":"{levelname}","logger":"{name}","message":"{message}"}}', "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(dsn=SENTRY_DSN, send_default_pii=False, traces_sample_rate=0.05)
