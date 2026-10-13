from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.auditlog.models import record
from apps.organization.models import Company, Employee, Workplace

from .models import SupervisorDocument, SupervisorDocumentVersion

MAX_DOCUMENT_SIZE = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    SupervisorDocument.Kind.DAILY_TIMESHEET: {".xlsx", ".xls"},
    SupervisorDocument.Kind.MONTHLY_INVENTORY: {".xlsx", ".xls"},
    SupervisorDocument.Kind.DELIVERY_NOTE: {".pdf", ".jpg", ".jpeg", ".png"},
}


class SupervisorDocumentError(Exception):
    pass


def _validate_file(kind, filename, content):
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS.get(kind, set()):
        raise SupervisorDocumentError("This file type is not allowed for the selected document.")
    if not content:
        raise SupervisorDocumentError("The uploaded file is empty.")
    if len(content) > MAX_DOCUMENT_SIZE:
        raise SupervisorDocumentError("The file is larger than 5 MB.")
    signatures = {
        ".xlsx": (b"PK\x03\x04",),
        ".xls": (bytes.fromhex("D0CF11E0A1B11AE1"),),
        ".pdf": (b"%PDF-",),
        ".jpg": (b"\xff\xd8\xff",),
        ".jpeg": (b"\xff\xd8\xff",),
        ".png": (b"\x89PNG\r\n\x1a\n",),
    }
    if not any(content.startswith(signature) for signature in signatures[extension]):
        raise SupervisorDocumentError("The file content does not match its extension.")
    return extension


def _company_edit_days():
    value = Company.objects.values_list(
        "supervisor_document_edit_days", flat=True
    ).first()
    return 2 if value is None else value


def can_supervisor_reupload(document, *, now=None, edit_days=None):
    edit_days = _company_edit_days() if edit_days is None else edit_days
    if edit_days <= 0:
        return False
    now = now or timezone.now()
    local_timezone = ZoneInfo(document.workplace.timezone)
    created_date = document.created_at.astimezone(local_timezone).date()
    current_date = now.astimezone(local_timezone).date()
    age = (current_date - created_date).days
    return 0 <= age < edit_days


def _save_version(document, employee, filename, mime_type, content, file_unique_id=""):
    _validate_file(document.kind, filename, content)
    last_number = (
        document.versions.aggregate(number=Max("version_number"))["number"] or 0
    )
    version = SupervisorDocumentVersion(
        document=document,
        version_number=last_number + 1,
        original_filename=Path(filename).name[:255],
        mime_type=(mime_type or "")[:100],
        size_bytes=len(content),
        telegram_file_unique_id=file_unique_id[:128],
        uploaded_by=employee,
    )
    version.file.save(filename, ContentFile(content), save=False)
    version.full_clean()
    version.save()
    document.save(update_fields=["updated_at"])
    record(
        "supervisor_document.version_uploaded",
        version,
        employee_actor_id=employee.id,
        metadata={
            "document_id": document.id,
            "kind": document.kind,
            "version": version.version_number,
            "workplace_id": document.workplace_id,
        },
    )
    return version


@transaction.atomic
def create_supervisor_document(
    employee_id,
    workplace_id,
    kind,
    filename,
    mime_type,
    content,
    *,
    description="",
    file_unique_id="",
    now=None,
):
    now = now or timezone.now()
    try:
        employee = Employee.objects.select_for_update().get(
            pk=employee_id, is_active=True
        )
    except Employee.DoesNotExist as error:
        raise SupervisorDocumentError("Your employee account is not active.") from error
    if employee.category != Employee.Category.SUPERVISOR:
        raise SupervisorDocumentError("Only shift supervisors can upload these documents.")
    try:
        workplace = Workplace.objects.select_for_update().get(
            pk=workplace_id, is_active=True
        )
    except Workplace.DoesNotExist as error:
        raise SupervisorDocumentError("This workplace is no longer available.") from error
    if not employee.workplaces.filter(pk=workplace.pk).exists():
        raise SupervisorDocumentError("This workplace is not assigned to you.")
    if kind not in SupervisorDocument.Kind.values:
        raise SupervisorDocumentError("Unknown document type.")
    local_date = now.astimezone(ZoneInfo(workplace.timezone)).date()
    document_date = (
        local_date.replace(day=1)
        if kind == SupervisorDocument.Kind.MONTHLY_INVENTORY
        else local_date
    )
    description = description.strip()
    if kind == SupervisorDocument.Kind.DELIVERY_NOTE and not description:
        raise SupervisorDocumentError("A short goods description is required.")
    delivery_number = None
    if kind == SupervisorDocument.Kind.DELIVERY_NOTE:
        delivery_number = (
            SupervisorDocument.objects.filter(
                workplace=workplace,
                kind=kind,
                document_date=document_date,
            ).aggregate(number=Max("delivery_number"))["number"]
            or 0
        ) + 1
    else:
        existing = SupervisorDocument.objects.filter(
            workplace=workplace, kind=kind, document_date=document_date
        ).exists()
        if existing:
            raise SupervisorDocumentError(
                "This document already exists. Use Update document to upload a new version."
            )
    try:
        document = SupervisorDocument.objects.create(
            supervisor=employee,
            workplace=workplace,
            kind=kind,
            document_date=document_date,
            delivery_number=delivery_number,
            description=description[:300],
        )
    except IntegrityError as error:
        raise SupervisorDocumentError(
            "This document was uploaded already. Use Update document."
        ) from error
    version = _save_version(
        document, employee, filename, mime_type, content, file_unique_id
    )
    return document, version


@transaction.atomic
def append_supervisor_document_version(
    employee_id,
    document_id,
    filename,
    mime_type,
    content,
    *,
    file_unique_id="",
    now=None,
):
    try:
        employee = Employee.objects.select_for_update().get(
            pk=employee_id, is_active=True
        )
    except Employee.DoesNotExist as error:
        raise SupervisorDocumentError("Your employee account is not active.") from error
    try:
        document = (
            SupervisorDocument.objects.select_for_update()
            .select_related("workplace")
            .get(pk=document_id)
        )
    except SupervisorDocument.DoesNotExist as error:
        raise SupervisorDocumentError("The selected document no longer exists.") from error
    if employee.category != Employee.Category.SUPERVISOR:
        raise SupervisorDocumentError("Only shift supervisors can upload these documents.")
    if not employee.workplaces.filter(pk=document.workplace_id).exists():
        raise SupervisorDocumentError("This workplace is not assigned to you.")
    if not can_supervisor_reupload(document, now=now):
        raise SupervisorDocumentError("The reupload period for this document has expired.")
    return _save_version(
        document, employee, filename, mime_type, content, file_unique_id
    )


def initial_upload_range(start, end, timezone_name):
    local_timezone = ZoneInfo(timezone_name)
    start_at = datetime.combine(start, time.min, tzinfo=local_timezone)
    end_at = datetime.combine(end + timedelta(days=1), time.min, tzinfo=local_timezone)
    return start_at, end_at
