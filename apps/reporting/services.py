from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import QuerySet

from apps.attendance.models import Shift
from apps.organization.models import Employee


def accessible_employees(user) -> QuerySet[Employee]:
    queryset = Employee.objects.select_related("team", "schedule")
    if user.can_manage_operations:
        return queryset
    return queryset.none()


def accessible_shifts(user) -> QuerySet[Shift]:
    return Shift.objects.filter(employee__in=accessible_employees(user)).select_related(
        "employee", "start_workplace", "end_workplace", "workplace_shift"
    )


def split_shift_by_local_day(shift, timezone_name=None):
    if not shift.ended_at:
        return []
    tz = ZoneInfo(timezone_name or shift.start_timezone)
    cursor = shift.started_at.astimezone(tz)
    end = shift.ended_at.astimezone(tz)
    parts = []
    while cursor.date() < end.date():
        midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=tz)
        parts.append((cursor.date(), midnight - cursor))
        cursor = midnight
    parts.append((cursor.date(), end - cursor))
    return parts


def attendance_summary(employee, start_date, end_date, timezone_name=None):
    fallback_timezone = timezone_name or (
        employee.workplaces.filter(is_active=True)
        .values_list("timezone", flat=True)
        .first()
        or "Europe/Chisinau"
    )
    tz = ZoneInfo(fallback_timezone)
    start = datetime.combine(start_date, time.min, tzinfo=tz)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
    shifts = employee.shifts.filter(
        started_at__lt=end + timedelta(days=1),
        ended_at__gte=start - timedelta(days=1),
    )
    daily = defaultdict(timedelta)
    for shift in shifts:
        for day, duration in split_shift_by_local_day(shift, timezone_name):
            if start_date <= day <= end_date:
                daily[day] += duration
    worked = sum(daily.values(), timedelta())
    scheduled = timedelta()
    if employee.schedule:
        schedule_days = {day.weekday: day for day in employee.schedule.days.all()}
        cursor = start_date
        while cursor <= end_date:
            day = schedule_days.get(cursor.weekday())
            if day:
                start_dt = datetime.combine(cursor, day.start_time)
                end_dt = datetime.combine(cursor, day.end_time)
                if end_dt <= start_dt:
                    end_dt += timedelta(days=1)
                scheduled += end_dt - start_dt
            cursor += timedelta(days=1)
    return {"worked": worked, "scheduled": scheduled, "variance": worked - scheduled, "daily": dict(daily)}
