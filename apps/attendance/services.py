import math
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.auditlog.models import record
from apps.organization.models import Employee, Workplace

from .models import CorrectionRequest, LocationEvidence, Shift


class AttendanceError(Exception):
    pass


@dataclass(frozen=True)
class LocationMatch:
    workplace: Workplace
    distance_meters: float


def haversine_meters(lat1, lon1, lat2, lon2):
    radius = 6_371_000
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def match_workplace(employee, latitude, longitude, accuracy=None):
    matches = []
    for workplace in employee.workplaces.filter(is_active=True):
        distance = haversine_meters(
            latitude, longitude, workplace.latitude, workplace.longitude
        )
        if distance <= workplace.radius_meters and (
            accuracy is None or accuracy <= workplace.max_accuracy_meters
        ):
            matches.append(LocationMatch(workplace, distance))
    return min(matches, key=lambda item: item.distance_meters) if matches else None


def _validate_location_age(captured_at):
    if abs((timezone.now() - captured_at).total_seconds()) > 120:
        raise AttendanceError("Location is stale; share a new current location")


@transaction.atomic
def start_shift(
    employee_id,
    latitude,
    longitude,
    accuracy,
    captured_at=None,
    workplace_shift_id=None,
):
    captured_at = captured_at or timezone.now()
    _validate_location_age(captured_at)
    employee = Employee.objects.select_for_update().get(pk=employee_id, is_active=True)
    if employee.category != Employee.Category.OPERATIONS:
        raise AttendanceError("Shift supervisors do not use attendance shifts")
    if employee.shifts.filter(ended_at__isnull=True).exists():
        raise AttendanceError("A shift is already open")
    match = match_workplace(employee, latitude, longitude, accuracy)
    if not match:
        record(
            "attendance.location_rejected",
            employee,
            employee_actor_id=employee.id,
            metadata={"reason": "outside_geofence_or_inaccurate"},
        )
        raise AttendanceError("Location is outside an assigned workplace or is too inaccurate")
    selected_shift = None
    if workplace_shift_id is not None:
        selected_shift = match.workplace.daily_shifts.filter(
            pk=workplace_shift_id, is_active=True
        ).first()
        if not selected_shift:
            raise AttendanceError("The selected shift does not belong to this workplace")
    try:
        shift = Shift.objects.create(
            employee=employee,
            started_at=captured_at,
            start_workplace=match.workplace,
            workplace_shift=selected_shift,
            start_timezone=match.workplace.timezone,
        )
    except IntegrityError as error:
        raise AttendanceError("A shift is already open") from error
    LocationEvidence.objects.create(
        shift=shift,
        event=LocationEvidence.Event.START,
        workplace=match.workplace,
        latitude=Decimal(str(latitude)),
        longitude=Decimal(str(longitude)),
        accuracy_meters=accuracy,
        distance_meters=match.distance_meters,
        verified=True,
        captured_at=captured_at,
    )
    record("attendance.shift_started", shift, employee_actor_id=employee.id)
    return shift


@transaction.atomic
def end_shift(employee_id, latitude, longitude, accuracy, captured_at=None):
    captured_at = captured_at or timezone.now()
    _validate_location_age(captured_at)
    employee = Employee.objects.select_for_update().get(pk=employee_id, is_active=True)
    if employee.category != Employee.Category.OPERATIONS:
        raise AttendanceError("Shift supervisors do not use attendance shifts")
    shift = Shift.objects.select_for_update().filter(
        employee=employee, ended_at__isnull=True
    ).first()
    if not shift:
        raise AttendanceError("There is no open shift")
    match = match_workplace(employee, latitude, longitude, accuracy)
    if not match:
        raise AttendanceError("Location is outside an assigned workplace or is too inaccurate")
    if captured_at <= shift.started_at:
        raise AttendanceError("Shift end must be after its start")
    shift.ended_at = captured_at
    shift.end_workplace = match.workplace
    shift.end_timezone = match.workplace.timezone
    shift.save(update_fields=["ended_at", "end_workplace", "end_timezone", "updated_at"])
    LocationEvidence.objects.create(
        shift=shift,
        event=LocationEvidence.Event.END,
        workplace=match.workplace,
        latitude=Decimal(str(latitude)),
        longitude=Decimal(str(longitude)),
        accuracy_meters=accuracy,
        distance_meters=match.distance_meters,
        verified=True,
        captured_at=captured_at,
    )
    record("attendance.shift_ended", shift, employee_actor_id=employee.id)
    return shift


@transaction.atomic
def review_correction(correction_id, reviewer, approve, note=""):
    correction = CorrectionRequest.objects.select_for_update().select_related("shift").get(
        pk=correction_id
    )
    if correction.status != CorrectionRequest.Status.PENDING:
        raise AttendanceError("Correction has already been reviewed")
    if approve:
        shift = Shift.objects.select_for_update().get(pk=correction.shift_id)
        new_start = correction.requested_start or shift.started_at
        new_end = correction.requested_end or shift.ended_at
        if new_end and new_end <= new_start:
            raise AttendanceError("Corrected end must be after start")
        shift.started_at, shift.ended_at = new_start, new_end
        shift.save(update_fields=["started_at", "ended_at", "updated_at"])
        correction.status = CorrectionRequest.Status.APPROVED
    else:
        correction.status = CorrectionRequest.Status.REJECTED
    correction.reviewed_by = reviewer
    correction.review_note = note
    correction.reviewed_at = timezone.now()
    correction.save(update_fields=["status", "reviewed_by", "review_note", "reviewed_at"])
    record("attendance.correction_reviewed", correction, actor=reviewer, metadata={"approved": approve})
    return correction
