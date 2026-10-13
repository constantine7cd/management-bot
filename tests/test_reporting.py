from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.attendance.models import Shift
from apps.organization.timezones import timezone_for_coordinates
from apps.reporting.services import split_shift_by_local_day

pytestmark = pytest.mark.django_db


def test_overnight_shift_splits_at_moldova_midnight(employee, workplace):
    tz = ZoneInfo("Europe/Chisinau")
    start = datetime(2026, 10, 8, 22, 0, tzinfo=tz)
    end = datetime(2026, 10, 9, 2, 0, tzinfo=tz)
    shift = Shift.objects.create(employee=employee, started_at=start, ended_at=end, start_workplace=workplace, end_workplace=workplace)
    parts = split_shift_by_local_day(shift)
    assert parts == [(start.date(), timedelta(hours=2)), (end.date(), timedelta(hours=2))]


def test_workplace_timezone_is_detected_and_dst_is_applied(employee, workplace):
    assert timezone_for_coordinates("47.0105", "28.8638") == "Europe/Chisinau"
    assert workplace.timezone == "Europe/Chisinau"
    winter = Shift.objects.create(
        employee=employee,
        started_at=datetime(2026, 1, 15, 10, tzinfo=ZoneInfo("UTC")),
        ended_at=datetime(2026, 1, 15, 11, tzinfo=ZoneInfo("UTC")),
        start_workplace=workplace,
        end_workplace=workplace,
        start_timezone=workplace.timezone,
        end_timezone=workplace.timezone,
    )
    summer = Shift.objects.create(
        employee=employee,
        started_at=datetime(2026, 7, 15, 10, tzinfo=ZoneInfo("UTC")),
        ended_at=datetime(2026, 7, 15, 11, tzinfo=ZoneInfo("UTC")),
        start_workplace=workplace,
        end_workplace=workplace,
        start_timezone=workplace.timezone,
        end_timezone=workplace.timezone,
    )
    assert winter.local_started_at.utcoffset() == timedelta(hours=2)
    assert summer.local_started_at.utcoffset() == timedelta(hours=3)
