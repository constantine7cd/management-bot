from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.attendance.services import AttendanceError, start_shift
from apps.botapp.handlers import menu
from apps.botapp.texts import TEXTS
from apps.organization.models import Company, Employee
from apps.supervisor_documents.models import SupervisorDocument
from apps.supervisor_documents.services import (
    SupervisorDocumentError,
    append_supervisor_document_version,
    can_supervisor_reupload,
    create_supervisor_document,
)

pytestmark = pytest.mark.django_db

XLSX = b"PK\x03\x04test-workbook"
PDF = b"%PDF-1.7 test-note"


@pytest.fixture
def supervisor(team, workplace):
    employee = Employee.objects.create(
        first_name="Irina",
        last_name="Supervisor",
        team=team,
        category=Employee.Category.SUPERVISOR,
        telegram_user_id=9001,
    )
    employee.workplaces.add(workplace)
    return employee


@pytest.fixture(autouse=True)
def private_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


def create_document(supervisor, workplace, kind, *, filename="report.xlsx", content=XLSX, description=""):
    return create_supervisor_document(
        supervisor.id,
        workplace.id,
        kind,
        filename,
        "application/octet-stream",
        content,
        description=description,
    )


def test_existing_employee_category_defaults_to_operations(employee):
    assert employee.category == Employee.Category.OPERATIONS


def test_supervisor_menu_replaces_attendance_actions(supervisor):
    labels = [button.text for row in menu("en", supervisor.category).keyboard for button in row]
    assert TEXTS["en"]["upload_document"] in labels
    assert TEXTS["en"]["update_document"] in labels
    assert TEXTS["en"]["start"] not in labels
    assert TEXTS["en"]["end"] not in labels
    assert TEXTS["en"]["stats"] not in labels
    assert TEXTS["en"]["correction"] not in labels


def test_operations_menu_has_no_supervisor_document_actions(employee):
    labels = [button.text for row in menu("en", employee.category).keyboard for button in row]
    assert TEXTS["en"]["start"] in labels
    assert TEXTS["en"]["upload_document"] not in labels
    assert TEXTS["en"]["update_document"] not in labels


def test_supervisor_cannot_start_attendance_shift(supervisor, workplace_shift):
    with pytest.raises(AttendanceError, match="do not use attendance"):
        start_shift(
            supervisor.id,
            workplace_shift.workplace.latitude,
            workplace_shift.workplace.longitude,
            10,
            timezone.now(),
            workplace_shift.id,
        )


def test_daily_timesheet_has_structured_filename_and_unique_slot(
    supervisor, workplace
):
    document, version = create_document(
        supervisor, workplace, SupervisorDocument.Kind.DAILY_TIMESHEET
    )

    assert document.document_date == timezone.now().astimezone(
        ZoneInfo(document.workplace.timezone)
    ).date()
    assert version.version_number == 1
    assert "daily_timesheet_main-office_v1.xlsx" in version.file.name
    with pytest.raises(SupervisorDocumentError, match="already exists"):
        create_document(
            supervisor, workplace, SupervisorDocument.Kind.DAILY_TIMESHEET
        )


def test_delivery_notes_require_description_and_receive_daily_numbers(
    supervisor, workplace
):
    with pytest.raises(SupervisorDocumentError, match="description is required"):
        create_document(
            supervisor,
            workplace,
            SupervisorDocument.Kind.DELIVERY_NOTE,
            filename="note.pdf",
            content=PDF,
        )

    first, first_version = create_document(
        supervisor,
        workplace,
        SupervisorDocument.Kind.DELIVERY_NOTE,
        filename="note.pdf",
        content=PDF,
        description="Flour and oil",
    )
    second, _version = create_document(
        supervisor,
        workplace,
        SupervisorDocument.Kind.DELIVERY_NOTE,
        filename="note.pdf",
        content=PDF,
        description="Packaging",
    )

    assert (first.delivery_number, second.delivery_number) == (1, 2)
    assert first.description == "Flour and oil"
    assert "delivery_note_main-office_note-001_v1.pdf" in first_version.file.name


def test_monthly_inventory_uses_first_day_of_workplace_month(supervisor, workplace):
    document, _version = create_document(
        supervisor, workplace, SupervisorDocument.Kind.MONTHLY_INVENTORY
    )
    assert document.document_date.day == 1


def test_file_extension_and_content_are_both_validated(supervisor, workplace):
    with pytest.raises(SupervisorDocumentError, match="not allowed"):
        create_document(
            supervisor,
            workplace,
            SupervisorDocument.Kind.DAILY_TIMESHEET,
            filename="malware.exe",
        )
    with pytest.raises(SupervisorDocumentError, match="does not match"):
        create_document(
            supervisor,
            workplace,
            SupervisorDocument.Kind.DAILY_TIMESHEET,
            content=b"not-an-excel-file",
        )


def test_reupload_keeps_versions_and_respects_company_window(
    supervisor, workplace
):
    company = Company.objects.create(
        name="Factory", supervisor_document_edit_days=2
    )
    document, first = create_document(
        supervisor, workplace, SupervisorDocument.Kind.DAILY_TIMESHEET
    )
    second = append_supervisor_document_version(
        supervisor.id,
        document.id,
        "corrected.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        XLSX,
        now=document.created_at + timedelta(days=1),
    )

    assert (first.version_number, second.version_number) == (1, 2)
    assert list(document.versions.values_list("version_number", flat=True)) == [2, 1]
    assert Path(first.file.name).name != Path(second.file.name).name
    with pytest.raises(ValueError, match="immutable"):
        second.save()

    assert not can_supervisor_reupload(
        document, now=document.created_at + timedelta(days=2), edit_days=2
    )

    company.supervisor_document_edit_days = 0
    company.save(update_fields=["supervisor_document_edit_days"])
    assert not can_supervisor_reupload(document)
    with pytest.raises(SupervisorDocumentError, match="expired"):
        append_supervisor_document_version(
            supervisor.id,
            document.id,
            "third.xlsx",
            "application/octet-stream",
            XLSX,
        )


def test_assigned_supervisor_can_add_version_created_by_colleague(
    supervisor, workplace, team
):
    Company.objects.create(name="Factory", supervisor_document_edit_days=2)
    colleague = Employee.objects.create(
        first_name="Victor",
        last_name="Supervisor",
        team=team,
        category=Employee.Category.SUPERVISOR,
    )
    colleague.workplaces.add(workplace)
    document, _first = create_document(
        supervisor, workplace, SupervisorDocument.Kind.DAILY_TIMESHEET
    )

    version = append_supervisor_document_version(
        colleague.id,
        document.id,
        "colleague.xlsx",
        "application/octet-stream",
        XLSX,
    )

    assert version.uploaded_by == colleague
    assert version.version_number == 2


def test_document_model_rejects_non_supervisor(employee, workplace):
    document = SupervisorDocument(
        supervisor=employee,
        workplace=workplace,
        kind=SupervisorDocument.Kind.DELIVERY_NOTE,
        document_date=timezone.localdate(),
        delivery_number=1,
        description="Goods",
    )
    with pytest.raises(ValidationError, match="shift supervisor"):
        document.full_clean()


def test_manager_can_filter_view_history_and_download_latest(
    client, manager, supervisor, workplace
):
    Company.objects.create(name="Factory", supervisor_document_edit_days=2)
    document, _first = create_document(
        supervisor,
        workplace,
        SupervisorDocument.Kind.DELIVERY_NOTE,
        filename="note.pdf",
        content=PDF,
        description="Steel parts",
    )
    latest = append_supervisor_document_version(
        supervisor.id,
        document.id,
        "corrected.pdf",
        "application/pdf",
        PDF,
    )
    client.force_login(manager)

    listing = client.get(
        reverse("supervisor_documents"),
        {"kind": SupervisorDocument.Kind.DELIVERY_NOTE, "workplace": workplace.id},
    )
    assert listing.status_code == 200
    assert b"Steel parts" in listing.content
    assert b"v2" in listing.content

    history = client.get(reverse("supervisor_document_detail", args=[document.pk]))
    assert history.status_code == 200
    assert b"v1" in history.content
    assert b"v2" in history.content

    download = client.get(reverse("supervisor_document_download", args=[latest.pk]))
    assert download.status_code == 200
    assert download["Content-Disposition"].startswith("attachment;")


def test_manager_can_change_reupload_window(client, manager):
    company = Company.objects.create(name="Factory", supervisor_document_edit_days=2)
    client.force_login(manager)

    response = client.post(
        reverse("supervisor_document_settings"),
        {"supervisor_document_edit_days": 5},
    )

    assert response.status_code == 302
    company.refresh_from_db()
    assert company.supervisor_document_edit_days == 5


def test_date_filter_uses_initial_upload_but_returns_latest_version(
    client, manager, supervisor, workplace
):
    Company.objects.create(name="Factory", supervisor_document_edit_days=2)
    document, _first = create_document(
        supervisor, workplace, SupervisorDocument.Kind.DAILY_TIMESHEET
    )
    append_supervisor_document_version(
        supervisor.id,
        document.id,
        "latest.xlsx",
        "application/octet-stream",
        XLSX,
    )
    SupervisorDocument.objects.filter(pk=document.pk).update(
        created_at=datetime(2026, 1, 5, 10, tzinfo=ZoneInfo("UTC"))
    )
    client.force_login(manager)

    matching = client.get(
        reverse("supervisor_documents"),
        {"start": "2026-01-05", "end": "2026-01-05"},
    )
    non_matching = client.get(
        reverse("supervisor_documents"),
        {"start": "2026-01-06", "end": "2026-01-06"},
    )

    assert b"v2" in matching.content
    assert b"Daily timesheet" in matching.content
    assert list(non_matching.context["documents"]) == []


def test_bulk_download_contains_only_latest_selected_version(
    client, manager, supervisor, workplace
):
    Company.objects.create(name="Factory", supervisor_document_edit_days=2)
    document, first = create_document(
        supervisor, workplace, SupervisorDocument.Kind.DAILY_TIMESHEET
    )
    latest = append_supervisor_document_version(
        supervisor.id,
        document.id,
        "latest.xlsx",
        "application/octet-stream",
        XLSX,
    )
    client.force_login(manager)

    response = client.post(
        reverse("supervisor_document_bulk_download"),
        {"documents": [document.pk]},
    )
    payload = b"".join(response.streaming_content)

    with ZipFile(BytesIO(payload)) as archive:
        assert archive.namelist() == [Path(latest.file.name).name]
        assert Path(first.file.name).name not in archive.namelist()
