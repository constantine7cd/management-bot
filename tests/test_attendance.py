from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

from apps.attendance.services import AttendanceError, end_shift, haversine_meters, start_shift
from apps.botapp.handlers import start_options
from apps.organization.models import WorkplaceShift

pytestmark = pytest.mark.django_db


def test_haversine_same_point_is_zero():
    assert haversine_meters(47.01, 28.86, 47.01, 28.86) == 0


def test_geofenced_shift_lifecycle(employee):
    started = timezone.now() - timedelta(minutes=1)
    shift = start_shift(employee.id, 47.0105, 28.8638, 15, started)
    assert shift.ended_at is None
    assert shift.location_evidence.get(event="start").verified

    ended = end_shift(employee.id, 47.0105, 28.8638, 20, timezone.now())
    assert ended.ended_at is not None
    assert ended.duration.total_seconds() > 0


def test_rejects_duplicate_open_shift(employee):
    start_shift(employee.id, 47.0105, 28.8638, 15)
    with pytest.raises(AttendanceError, match="already open"):
        start_shift(employee.id, 47.0105, 28.8638, 15)


def test_rejects_outside_geofence(employee):
    with pytest.raises(AttendanceError, match="outside"):
        start_shift(employee.id, 48.0, 29.0, 15)


def test_rejects_stale_location(employee):
    with pytest.raises(AttendanceError, match="stale"):
        start_shift(employee.id, 47.0105, 28.8638, 15, timezone.now() - timedelta(minutes=5))


def test_records_selected_workplace_shift(employee, workplace_shift):
    shift = start_shift(
        employee.id,
        47.0105,
        28.8638,
        15,
        workplace_shift_id=workplace_shift.id,
    )
    assert shift.workplace_shift == workplace_shift


def test_rejects_shift_from_different_workplace(employee):
    from apps.organization.models import Workplace

    other = Workplace.objects.create(
        name="Other", latitude="47.010500", longitude="28.863800"
    )
    wrong_shift = WorkplaceShift.objects.create(
        workplace=other, number=1, start_time="08:00", end_time="16:00"
    )
    with pytest.raises(AttendanceError, match="does not belong"):
        start_shift(
            employee.id,
            47.0105,
            28.8638,
            15,
            workplace_shift_id=wrong_shift.id,
        )


def test_bot_start_options_do_not_duplicate_daily_shifts(employee, workplace, workplace_shift):
    second = WorkplaceShift.objects.create(
        workplace=workplace,
        number=2,
        name="Night",
        start_time="20:00",
        end_time="08:00",
    )
    has_open_shift, options = async_to_sync(start_options)(employee)
    assert not has_open_shift
    assert [item.id for item in options] == [workplace_shift.id, second.id]
