from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.leave_management.models import LeaveType
from apps.organization.models import Company


class Command(BaseCommand):
    help = "Create the first company, admin, and default leave types"

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--company", required=True)
        parser.add_argument("--password", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(email=options["email"]).exists():
            raise CommandError("A user with this email already exists")
        Company.objects.get_or_create(
            pk=1,
            defaults={"name": options["company"], "timezone": "Europe/Chisinau"},
        )
        user = User.objects.create_superuser(options["email"], options["password"])
        LeaveType.objects.get_or_create(
            name_en="Vacation", defaults={"name_ru": "Отпуск", "requires_balance": True}
        )
        LeaveType.objects.get_or_create(
            name_en="Day off", defaults={"name_ru": "Выходной", "requires_balance": True}
        )
        LeaveType.objects.get_or_create(
            name_en="Sick leave",
            defaults={"name_ru": "Больничный", "requires_balance": False},
        )
        self.stdout.write(self.style.SUCCESS(f"Created admin {user.email}"))
