from django.contrib import admin

from .models import (
    Company,
    Employee,
    EmployeeInvite,
    Holiday,
    ScheduleDay,
    ScheduleTemplate,
    Team,
    Workplace,
    WorkplaceShift,
)


class ScheduleDayInline(admin.TabularInline):
    model = ScheduleDay
    extra = 1


class WorkplaceShiftInline(admin.TabularInline):
    model = WorkplaceShift
    extra = 1


@admin.register(Workplace)
class WorkplaceAdmin(admin.ModelAdmin):
    inlines = [WorkplaceShiftInline]


@admin.register(ScheduleTemplate)
class ScheduleTemplateAdmin(admin.ModelAdmin):
    inlines = [ScheduleDayInline]


admin.site.register([Company, Team, Holiday, EmployeeInvite])


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "last_name",
        "first_name",
        "category",
        "team",
        "is_active",
        "telegram_user_id",
    )
    exclude = ()

    def get_exclude(self, request, obj=None):
        if not request.user.is_company_admin:
            return ("hourly_rate", "rate_currency")
        return ()
