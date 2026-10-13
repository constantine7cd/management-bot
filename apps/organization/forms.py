from typing import Any

import holidays
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.forms import inlineformset_factory

from apps.accounts.models import User
from apps.leave_management.models import LeaveBalance, LeaveRequest
from apps.leave_management.services import scheduled_days

from .models import Employee, Team, Workplace, WorkplaceShift


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "managers", "is_active"]
        widgets = {"managers": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if user and not user.is_company_admin:
            self.fields.pop("managers")
        else:
            self.fields["managers"].queryset = User.objects.filter(
                role=User.Role.MANAGER, is_active=True
            )


class HolidayImportForm(forms.Form):
    country_code = forms.CharField(
        max_length=2,
        initial="MD",
        help_text="Two-letter ISO country code, for example MD, RO or GB.",
    )
    year = forms.IntegerField(min_value=2000, max_value=2100)

    def clean_country_code(self):
        code = self.cleaned_data["country_code"].strip().upper()
        try:
            holidays.country_holidays(code, years=[2024])
        except NotImplementedError as error:
            raise forms.ValidationError("This country is not supported.") from error
        return code


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            "first_name", "last_name", "category", "email", "team", "workplaces", "schedule",
            "language", "hourly_rate", "rate_currency", "is_active",
            "holiday_country",
        ]
        widgets = {"workplaces": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["category"].initial = Employee.Category.OPERATIONS

    def clean_category(self):
        category = self.cleaned_data.get("category") or Employee.Category.OPERATIONS
        if (
            category == Employee.Category.SUPERVISOR
            and self.instance.pk
            and self.instance.shifts.filter(ended_at__isnull=True).exists()
        ):
            raise forms.ValidationError(
                "End the employee's open shift before changing them to a supervisor."
            )
        return category

    def clean(self):
        data = super().clean()
        if (
            data.get("is_active")
            and self.instance.telegram_user_id is not None
            and Employee.objects.filter(
                is_active=True,
                telegram_user_id=self.instance.telegram_user_id,
            )
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError(
                "This Telegram account is already linked to an active employee."
            )
        return data

    def clean_rate_currency(self):
        return self.cleaned_data["rate_currency"].upper()


class ManagerForm(forms.ModelForm):
    teams = forms.ModelMultipleChoiceField(
        queryset=Team.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Set the manager's password. Leave empty only when editing to keep it unchanged.",
    )

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "telegram_chat_id", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["teams"].initial = self.instance.managed_teams.all()

    def save(self, commit=True):
        manager = super().save(commit=False)
        manager.role = User.Role.MANAGER
        manager.is_staff = True
        password = self.cleaned_data.get("password")
        if password:
            manager.set_password(password)
        elif not manager.pk:
            manager.set_unusable_password()
        if commit:
            manager.save()
            manager.managed_teams.set(self.cleaned_data["teams"])
        return manager

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if not self.instance.pk and not password:
            raise forms.ValidationError("An initial password is required for a new manager.")
        if password:
            validate_password(password, self.instance if self.instance.pk else None)
        return password


class WorkplaceForm(forms.ModelForm):
    class Meta:
        model = Workplace
        fields = [
            "name",
            "latitude",
            "longitude",
            "radius_meters",
            "max_accuracy_meters",
            "is_active",
        ]


WorkplaceShiftFormSet: Any = inlineformset_factory(
    Workplace,
    WorkplaceShift,
    fields=["number", "name", "start_time", "end_time", "is_active"],
    extra=1,
    can_delete=True,
    widgets={
        "start_time": forms.TimeInput(attrs={"type": "time"}),
        "end_time": forms.TimeInput(attrs={"type": "time"}),
    },
)


class LeaveBalanceForm(forms.ModelForm):
    class Meta:
        model = LeaveBalance
        fields = ["employee", "leave_type", "year", "allocated_days"]


class AdminLeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ["employee", "leave_type", "start_date", "end_date", "reason", "status"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        data = super().clean()
        start, end, employee = data.get("start_date"), data.get("end_date"), data.get("employee")
        if start and end and end < start:
            raise forms.ValidationError("End date must not precede start date")
        if start and end and employee:
            overlap = LeaveRequest.objects.filter(
                employee=employee,
                status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED],
                start_date__lte=end,
                end_date__gte=start,
            )
            if self.instance.pk:
                overlap = overlap.exclude(pk=self.instance.pk)
            if overlap.exists():
                raise forms.ValidationError("These dates overlap existing pending or approved leave")
        return data

    def save(self, commit=True):
        request = super().save(commit=False)
        request.requested_days = scheduled_days(
            request.employee, request.start_date, request.end_date
        )
        if commit:
            request.save()
        return request
