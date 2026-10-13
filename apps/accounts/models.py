from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MANAGER)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    @property
    def is_company_admin(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def can_manage_operations(self):
        return self.is_company_admin or (self.is_staff and self.role == self.Role.MANAGER)
