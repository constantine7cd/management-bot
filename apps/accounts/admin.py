from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "role", "is_staff", "is_active")
    fieldsets = UserAdmin.fieldsets + (("Company access", {"fields": ("role", "telegram_chat_id")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Company access", {"fields": ("email", "role", "is_staff")}),
    )
