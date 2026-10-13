import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Company(models.Model):
    name = models.CharField(max_length=160, default="My company")
    timezone = models.CharField(max_length=64, default="Europe/Chisinau")
    default_currency = models.CharField(
        max_length=3,
        default="MDL",
        validators=[RegexValidator(r"^[A-Z]{3}$", "Use an ISO 4217 currency code")],
    )
    location_retention_days = models.PositiveIntegerField(default=90)
    long_shift_hours = models.PositiveSmallIntegerField(default=14)
    supervisor_document_edit_days = models.PositiveSmallIntegerField(
        default=2,
        validators=[MaxValueValidator(30)],
        help_text="Number of local calendar days after the first upload. Set to 0 to disable supervisor reuploads.",
    )

    def __str__(self):
        return self.name


class Team(models.Model):
    name = models.CharField(max_length=120, unique=True)
    managers = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="managed_teams", blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Workplace(models.Model):
    name = models.CharField(max_length=120)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    radius_meters = models.PositiveIntegerField(
        default=150, validators=[MinValueValidator(20), MaxValueValidator(5000)]
    )
    max_accuracy_meters = models.PositiveIntegerField(default=100)
    timezone = models.CharField(max_length=64, editable=False, default="Europe/Chisinau")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from .timezones import timezone_for_coordinates

        self.timezone = timezone_for_coordinates(self.latitude, self.longitude)
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        from .timezones import timezone_for_coordinates

        self.timezone = timezone_for_coordinates(self.latitude, self.longitude)


class WorkplaceShift(models.Model):
    workplace = models.ForeignKey(Workplace, related_name="daily_shifts", on_delete=models.CASCADE)
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    name = models.CharField(max_length=80, blank=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["workplace__name", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["workplace", "number"], name="unique_workplace_shift_number"
            )
        ]

    def __str__(self):
        label = self.name or f"Shift {self.number}"
        return f"{self.workplace} · {label}"


class ScheduleTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class ScheduleDay(models.Model):
    schedule = models.ForeignKey(ScheduleTemplate, related_name="days", on_delete=models.CASCADE)
    weekday = models.PositiveSmallIntegerField(validators=[MaxValueValidator(6)])
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["schedule", "weekday"], name="unique_schedule_weekday")
        ]


class Employee(models.Model):
    class Category(models.TextChoices):
        OPERATIONS = "operations", _("Operations worker")
        SUPERVISOR = "supervisor", _("Shift supervisor")

    class Language(models.TextChoices):
        EN = "en", "English"
        RU = "ru", "Русский"

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    category = models.CharField(
        max_length=16, choices=Category.choices, default=Category.OPERATIONS
    )
    email = models.EmailField(blank=True)
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL)
    workplaces = models.ManyToManyField(Workplace, related_name="employees", blank=True)
    schedule = models.ForeignKey(ScheduleTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    telegram_user_id = models.BigIntegerField(null=True, blank=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.EN)
    hourly_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_currency = models.CharField(
        max_length=3,
        default="MDL",
        validators=[RegexValidator(r"^[A-Z]{3}$", "Use an ISO 4217 currency code")],
    )
    holiday_country = models.CharField(
        max_length=2,
        default="MD",
        validators=[RegexValidator(r"^[A-Z]{2}$", "Use a two-letter ISO country code")],
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["telegram_user_id"],
                condition=models.Q(is_active=True, telegram_user_id__isnull=False),
                name="unique_active_employee_telegram_user_id",
            )
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.save(update_fields=["is_active", "archived_at"])


class EmployeeInvite(models.Model):
    employee = models.ForeignKey(Employee, related_name="invites", on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, employee, created_by, valid_hours=72):
        return cls.objects.create(
            employee=employee,
            created_by=created_by,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(hours=valid_hours),
        )

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()


class Holiday(models.Model):
    country_code = models.CharField(
        max_length=2,
        default="MD",
        validators=[RegexValidator(r"^[A-Z]{2}$", "Use a two-letter ISO country code")],
    )
    date = models.DateField()
    name = models.CharField(max_length=160)
    source = models.CharField(max_length=32, default="manual")

    class Meta:
        ordering = ["country_code", "date"]
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "date"], name="unique_country_holiday_date"
            )
        ]

    def __str__(self):
        return f"{self.country_code} · {self.date}: {self.name}"
