from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

import holidays
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q, Sum
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.attendance.models import CorrectionRequest
from apps.attendance.services import AttendanceError, review_correction
from apps.auditlog.models import AuditEvent, record
from apps.incidents.models import Incident
from apps.leave_management.models import LeaveBalance, LeaveRequest, LeaveType
from apps.leave_management.services import LeaveError, review_leave
from apps.organization.forms import (
    AdminLeaveRequestForm,
    EmployeeForm,
    HolidayImportForm,
    LeaveBalanceForm,
    ManagerForm,
    TeamForm,
    WorkplaceForm,
    WorkplaceShiftFormSet,
)
from apps.organization.models import Company, Employee, EmployeeInvite, Holiday, Team, Workplace
from apps.supervisor_documents.forms import (
    SupervisorDocumentFilterForm,
    SupervisorDocumentSettingsForm,
)
from apps.supervisor_documents.models import (
    SupervisorDocument,
    SupervisorDocumentVersion,
)
from apps.supervisor_documents.services import initial_upload_range

from .forms import ExportForm
from .models import ExportJob
from .services import accessible_employees, accessible_shifts
from .tasks import generate_export


def _can_access_employee(user, employee):
    return user.can_manage_operations


@login_required
def dashboard(request):
    employees = accessible_employees(request.user)
    shifts = accessible_shifts(request.user)
    now = timezone.now()
    week_start = now - timedelta(days=now.weekday(), hours=now.hour, minutes=now.minute)
    completed = shifts.filter(ended_at__isnull=False, started_at__gte=week_start)
    seconds = sum((shift.duration.total_seconds() for shift in completed), 0)
    context = {
        "active_count": employees.filter(is_active=True).count(),
        "working": shifts.filter(ended_at__isnull=True).count(),
        "long_shifts": shifts.filter(ended_at__isnull=True, started_at__lt=now - timedelta(hours=14)).count(),
        "pending_leave": LeaveRequest.objects.filter(employee__in=employees, status="pending").count(),
        "pending_corrections": CorrectionRequest.objects.filter(shift__employee__in=employees, status="pending").count(),
        "open_incidents": Incident.objects.filter(employee__in=employees).exclude(status="resolved").count(),
        "week_hours": seconds / 3600,
        "recent_shifts": shifts[:8],
        "document_count": SupervisorDocument.objects.filter(
            created_at__gte=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ).count(),
    }
    return render(request, "reporting/dashboard.html", context)


@login_required
def employees(request):
    queryset = accessible_employees(request.user)
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(email__icontains=query))
    category = request.GET.get("category", "")
    if category in Employee.Category.values:
        queryset = queryset.filter(category=category)
    return render(
        request,
        "reporting/employees.html",
        {
            "employees": queryset,
            "query": query,
            "category": category,
            "categories": Employee.Category.choices,
        },
    )


@login_required
def managers(request):
    if not request.user.is_company_admin:
        raise PermissionDenied
    authenticators = Authenticator.objects.filter(user_id=OuterRef("pk"))
    queryset = (
        User.objects.filter(role=User.Role.MANAGER)
        .prefetch_related("managed_teams")
        .annotate(has_mfa=Exists(authenticators))
    )
    return render(request, "reporting/managers.html", {"managers": queryset})


@login_required
def teams(request):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    queryset = Team.objects.prefetch_related("managers").order_by("name")
    return render(request, "reporting/teams.html", {"teams": queryset})


@login_required
def team_edit(request, pk=None):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    team = get_object_or_404(Team, pk=pk) if pk else None
    form = TeamForm(request.POST or None, instance=team, user=request.user)
    if request.method == "POST" and form.is_valid():
        team = form.save()
        record("team.updated" if pk else "team.created", team, actor=request.user)
        messages.success(request, "Team saved.")
        return redirect("teams")
    return render(
        request,
        "reporting/form.html",
        {"form": form, "title": "Edit team" if pk else "Add team"},
    )


@login_required
def manager_edit(request, pk=None):
    if not request.user.is_company_admin:
        raise PermissionDenied
    manager = get_object_or_404(User, pk=pk, role=User.Role.MANAGER) if pk else None
    form = ManagerForm(request.POST or None, instance=manager)
    if request.method == "POST" and form.is_valid():
        manager = form.save()
        record("manager.updated" if pk else "manager.created", manager, actor=request.user)
        messages.success(request, "Manager saved.")
        return redirect("managers")
    return render(
        request,
        "reporting/form.html",
        {"form": form, "title": "Edit manager" if pk else "Add manager"},
    )


@login_required
@require_POST
@transaction.atomic
def manager_mfa_reset(request, pk):
    if not request.user.is_company_admin:
        raise PermissionDenied
    manager = get_object_or_404(
        User.objects.select_for_update(), pk=pk, role=User.Role.MANAGER
    )
    deleted = Authenticator.objects.filter(user=manager).delete()[0]
    record(
        "manager.mfa_reset",
        manager,
        actor=request.user,
        metadata={"authenticators_removed": deleted},
    )
    messages.success(
        request,
        _("Manager MFA was reset. They must enroll an authenticator again."),
    )
    return redirect("managers")


@login_required
def workplaces(request):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    queryset = Workplace.objects.prefetch_related("daily_shifts").order_by("name")
    return render(request, "reporting/workplaces.html", {"workplaces": queryset})


@login_required
def workplace_edit(request, pk=None):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    workplace = get_object_or_404(Workplace, pk=pk) if pk else Workplace()
    form = WorkplaceForm(request.POST or None, instance=workplace)
    formset = WorkplaceShiftFormSet(request.POST or None, instance=workplace, prefix="shifts")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        workplace = form.save()
        formset.instance = workplace
        formset.save()
        record("workplace.updated" if pk else "workplace.created", workplace, actor=request.user)
        messages.success(request, "Workplace and daily shifts saved.")
        return redirect("workplaces")
    return render(
        request,
        "reporting/workplace_form.html",
        {"form": form, "formset": formset, "title": "Edit workplace" if pk else "Add workplace"},
    )


@login_required
def holiday_calendar(request):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    form = HolidayImportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        country = form.cleaned_data["country_code"]
        year = form.cleaned_data["year"]
        imported = holidays.country_holidays(country, years=[year])
        for holiday_date, name in imported.items():
            Holiday.objects.update_or_create(
                country_code=country,
                date=holiday_date,
                defaults={"name": str(name), "source": "python-holidays"},
            )
        record(
            "holidays.imported",
            request.user,
            actor=request.user,
            metadata={"country": country, "year": year, "count": len(imported)},
        )
        messages.success(request, f"Imported {len(imported)} holidays for {country} {year}.")
        return redirect(f"{request.path}?country={country}&year={year}")
    country = request.GET.get("country", "MD").upper()
    try:
        year = int(request.GET.get("year", timezone.localdate().year))
    except ValueError:
        year = timezone.localdate().year
    items = Holiday.objects.filter(country_code=country, date__year=year)
    return render(
        request,
        "reporting/holidays.html",
        {"form": form, "holidays": items, "country": country, "year": year},
    )


@login_required
def employee_edit(request, pk=None):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    employee = get_object_or_404(Employee, pk=pk) if pk else None
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        employee = form.save()
        record("employee.updated" if pk else "employee.created", employee, actor=request.user)
        messages.success(request, "Employee saved.")
        return redirect("employees")
    return render(request, "reporting/form.html", {"form": form, "title": "Edit employee" if pk else "Add employee"})


@login_required
@require_POST
def employee_invite(request, pk):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    employee = get_object_or_404(Employee, pk=pk, is_active=True)
    invite = EmployeeInvite.issue(employee, request.user)
    record("employee.invite_issued", employee, actor=request.user)
    link = f"https://t.me/{settings.BOT_USERNAME.lstrip('@')}?start={invite.token}"
    messages.success(request, f"Invitation link (valid 72h): {link}")
    return redirect("employees")


@login_required
@require_POST
def employee_archive(request, pk):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    employee = get_object_or_404(Employee, pk=pk)
    employee.archive()
    record("employee.archived", employee, actor=request.user)
    messages.success(request, "Employee archived.")
    return redirect("employees")


@login_required
def shifts(request):
    queryset = accessible_shifts(request.user)
    start, end = request.GET.get("start"), request.GET.get("end")
    if start:
        queryset = queryset.filter(started_at__date__gte=start)
    if end:
        queryset = queryset.filter(started_at__date__lte=end)
    return render(request, "reporting/shifts.html", {"shifts": queryset[:500], "start": start, "end": end})


@login_required
def approvals(request):
    employees = accessible_employees(request.user)
    return render(request, "reporting/approvals.html", {
        "leave_requests": LeaveRequest.objects.filter(employee__in=employees, status="pending").select_related("employee", "leave_type"),
        "corrections": CorrectionRequest.objects.filter(shift__employee__in=employees, status="pending").select_related("shift__employee"),
    })


@login_required
def leave_history(request):
    employees = accessible_employees(request.user)
    requests = LeaveRequest.objects.filter(employee__in=employees).select_related(
        "employee", "leave_type", "reviewed_by"
    )
    employee_id = request.GET.get("employee")
    status = request.GET.get("status")
    leave_type_id = request.GET.get("type")
    if employee_id:
        requests = requests.filter(employee_id=employee_id)
    if status:
        requests = requests.filter(status=status)
    if leave_type_id:
        requests = requests.filter(leave_type_id=leave_type_id)
    balances = list(
        LeaveBalance.objects.filter(employee__in=employees).select_related(
            "employee", "leave_type"
        )
    )
    for balance in balances:
        related = LeaveRequest.objects.filter(
            employee=balance.employee,
            leave_type=balance.leave_type,
            start_date__year=balance.year,
        )
        balance.approved_days = related.filter(status=LeaveRequest.Status.APPROVED).aggregate(
            total=Sum("requested_days")
        )["total"] or Decimal(0)
        balance.pending_days = related.filter(status=LeaveRequest.Status.PENDING).aggregate(
            total=Sum("requested_days")
        )["total"] or Decimal(0)
        balance.remaining_days = balance.allocated_days - balance.approved_days
    return render(
        request,
        "reporting/leave_history.html",
        {
            "leave_requests": requests,
            "balances": balances,
            "employees": employees,
            "leave_types": LeaveType.objects.filter(is_active=True),
            "statuses": LeaveRequest.Status.choices,
            "selected_employee": employee_id,
            "selected_status": status,
            "selected_type": leave_type_id,
        },
    )


@login_required
def leave_balance_edit(request, pk=None):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    balance = get_object_or_404(LeaveBalance, pk=pk) if pk else None
    form = LeaveBalanceForm(request.POST or None, instance=balance)
    if request.method == "POST" and form.is_valid():
        balance = form.save()
        record("leave.balance_updated", balance, actor=request.user)
        messages.success(request, "Leave balance saved.")
        return redirect("leave_history")
    return render(
        request,
        "reporting/form.html",
        {"form": form, "title": "Edit leave balance" if pk else "Add leave balance"},
    )


@login_required
def leave_request_edit(request, pk=None):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    leave = get_object_or_404(LeaveRequest, pk=pk) if pk else None
    form = AdminLeaveRequestForm(request.POST or None, instance=leave)
    if request.method == "POST" and form.is_valid():
        leave = form.save()
        if leave.status in {LeaveRequest.Status.APPROVED, LeaveRequest.Status.REJECTED}:
            leave.reviewed_by = request.user
            leave.reviewed_at = timezone.now()
            leave.save(update_fields=["reviewed_by", "reviewed_at"])
        record("leave.admin_updated", leave, actor=request.user)
        messages.success(request, "Day off saved.")
        return redirect("leave_history")
    return render(
        request,
        "reporting/form.html",
        {"form": form, "title": "Edit day off" if pk else "Add day off"},
    )


@login_required
@require_POST
def leave_review(request, pk, decision):
    leave = get_object_or_404(LeaveRequest.objects.select_related("employee"), pk=pk)
    if not _can_access_employee(request.user, leave.employee):
        raise PermissionDenied
    try:
        review_leave(pk, request.user, decision == "approve", request.POST.get("note", ""))
        messages.success(request, "Leave request reviewed.")
    except LeaveError as error:
        messages.error(request, str(error))
    return redirect("approvals")


@login_required
@require_POST
def correction_review(request, pk, decision):
    correction = get_object_or_404(CorrectionRequest.objects.select_related("shift__employee"), pk=pk)
    if not _can_access_employee(request.user, correction.shift.employee):
        raise PermissionDenied
    try:
        review_correction(pk, request.user, decision == "approve", request.POST.get("note", ""))
        messages.success(request, "Correction reviewed.")
    except AttendanceError as error:
        messages.error(request, str(error))
    return redirect("approvals")


@login_required
def incidents(request):
    queryset = Incident.objects.filter(employee__in=accessible_employees(request.user)).select_related("employee")
    return render(request, "reporting/incidents.html", {"incidents": queryset})


@login_required
@require_POST
def incident_resolve(request, pk):
    incident = get_object_or_404(Incident.objects.select_related("employee"), pk=pk)
    if not _can_access_employee(request.user, incident.employee):
        raise PermissionDenied
    incident.status = Incident.Status.RESOLVED
    incident.resolution = request.POST.get("resolution", "")
    incident.resolved_at = timezone.now()
    incident.assigned_to = request.user
    incident.save(update_fields=["status", "resolution", "resolved_at", "assigned_to"])
    record("incident.resolved", incident, actor=request.user)
    return redirect("incidents")


@login_required
def supervisor_documents(request):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    workplaces = Workplace.objects.order_by("name")
    supervisors = Employee.objects.filter(
        category=Employee.Category.SUPERVISOR
    ).order_by("last_name", "first_name")
    form = SupervisorDocumentFilterForm(
        request.GET or None, workplaces=workplaces, supervisors=supervisors
    )
    queryset = SupervisorDocument.objects.select_related(
        "supervisor", "workplace"
    ).prefetch_related("versions")
    if form.is_valid():
        filters = form.cleaned_data
        if filters["kind"]:
            queryset = queryset.filter(kind=filters["kind"])
        if filters["workplace"]:
            queryset = queryset.filter(workplace_id=filters["workplace"])
        if filters["supervisor"]:
            queryset = queryset.filter(supervisor_id=filters["supervisor"])
        if filters["start"] or filters["end"]:
            company_timezone = (
                Company.objects.values_list("timezone", flat=True).first()
                or "Europe/Chisinau"
            )
            start = filters["start"] or date(2000, 1, 1)
            end = filters["end"] or timezone.localdate()
            start_at, end_at = initial_upload_range(start, end, company_timezone)
            queryset = queryset.filter(created_at__gte=start_at, created_at__lt=end_at)
    company = Company.objects.first()
    return render(
        request,
        "reporting/supervisor_documents/list.html",
        {
            "documents": queryset[:500],
            "filter_form": form,
            "edit_days": company.supervisor_document_edit_days if company else 2,
        },
    )


@login_required
def supervisor_document_detail(request, pk):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    document = get_object_or_404(
        SupervisorDocument.objects.select_related(
            "supervisor", "workplace"
        ).prefetch_related("versions__uploaded_by"),
        pk=pk,
    )
    return render(
        request,
        "reporting/supervisor_documents/detail.html",
        {"document": document},
    )


@login_required
def supervisor_document_download(request, pk):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    version = get_object_or_404(
        SupervisorDocumentVersion.objects.select_related("document"), pk=pk
    )
    record(
        "supervisor_document.downloaded",
        version,
        actor=request.user,
        metadata={"document_id": version.document_id, "version": version.version_number},
    )
    return FileResponse(
        version.file.open("rb"),
        as_attachment=True,
        filename=Path(version.file.name).name,
    )


@login_required
@require_POST
def supervisor_document_bulk_download(request):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    try:
        document_ids = {int(value) for value in request.POST.getlist("documents")}
    except ValueError:
        return HttpResponse("Invalid document selection", status=400)
    if not document_ids or len(document_ids) > 100:
        messages.error(request, "Select between 1 and 100 documents.")
        return redirect("supervisor_documents")
    documents = list(
        SupervisorDocument.objects.filter(pk__in=document_ids).prefetch_related(
            "versions"
        )
    )
    archive = SpooledTemporaryFile(max_size=10 * 1024 * 1024, mode="w+b")
    with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
        for document in documents:
            version = document.latest_version
            if version:
                with version.file.open("rb") as source:
                    zip_file.writestr(Path(version.file.name).name, source.read())
    archive.seek(0)
    record(
        "supervisor_document.bulk_downloaded",
        request.user,
        actor=request.user,
        metadata={"document_count": len(documents)},
    )
    return FileResponse(
        archive,
        as_attachment=True,
        filename=f"supervisor-documents-{timezone.localdate():%Y-%m-%d}.zip",
    )


@login_required
@require_POST
@transaction.atomic
def supervisor_document_settings(request):
    if not request.user.can_manage_operations:
        raise PermissionDenied
    company = Company.objects.select_for_update().first()
    if company is None:
        company = Company.objects.create()
    form = SupervisorDocumentSettingsForm(request.POST, instance=company)
    if form.is_valid():
        company = form.save()
        record(
            "supervisor_document.settings_updated",
            company,
            actor=request.user,
            metadata={"edit_days": company.supervisor_document_edit_days},
        )
        messages.success(request, _("Supervisor document reupload window updated."))
    else:
        messages.error(request, _("Enter a reupload window from 0 to 30 days."))
    return redirect("supervisor_documents")


@login_required
def exports(request):
    form = ExportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        job = ExportJob.objects.create(
            requested_by=request.user,
            kind=form.cleaned_data["kind"],
            filters={"start": str(form.cleaned_data["start"]), "end": str(form.cleaned_data["end"])},
        )
        generate_export.delay(str(job.pk))
        messages.success(request, "Export queued.")
        return redirect("exports")
    jobs = ExportJob.objects.filter(requested_by=request.user)
    return render(request, "reporting/exports.html", {"form": form, "jobs": jobs})


@login_required
def export_download(request, pk):
    job = get_object_or_404(ExportJob, pk=pk, requested_by=request.user, status=ExportJob.Status.READY)
    if job.expires_at <= timezone.now() or not job.file:
        return HttpResponse("Export expired", status=410)
    return FileResponse(job.file.open("rb"), as_attachment=True, filename=job.file.name.rsplit("/", 1)[-1])


@login_required
def audit(request):
    if not request.user.is_company_admin:
        raise PermissionDenied
    return render(request, "reporting/audit.html", {"events": AuditEvent.objects.select_related("actor")[:500]})


def health_live(request):
    return JsonResponse({"status": "ok"})


def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return JsonResponse({"status": "ready"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
