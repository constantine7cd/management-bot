from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.organization.models import Employee, Workplace


def supervisor_document_upload_path(instance, filename):
    document = instance.document
    extension = Path(filename).suffix.lower()
    workplace = slugify(document.workplace.name) or f"workplace-{document.workplace_id}"
    base = f"{document.document_date:%Y-%m-%d}_{document.kind}_{workplace}"
    if document.kind == SupervisorDocument.Kind.DELIVERY_NOTE:
        base += f"_note-{document.delivery_number:03d}"
    return (
        f"supervisor-documents/{document.document_date:%Y/%m}/"
        f"{base}_v{instance.version_number}{extension}"
    )


class SupervisorDocument(models.Model):
    class Kind(models.TextChoices):
        DAILY_TIMESHEET = "daily_timesheet", _("Daily timesheet")
        DELIVERY_NOTE = "delivery_note", _("Delivery note")
        MONTHLY_INVENTORY = "monthly_inventory", _("Monthly inventory")

    supervisor = models.ForeignKey(
        Employee, related_name="supervisor_documents", on_delete=models.PROTECT
    )
    workplace = models.ForeignKey(
        Workplace, related_name="supervisor_documents", on_delete=models.PROTECT
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    document_date = models.DateField(
        help_text="Day for daily documents; first day of the month for monthly inventory."
    )
    delivery_number = models.PositiveIntegerField(null=True, blank=True)
    description = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workplace", "kind", "document_date"],
                condition=models.Q(kind="daily_timesheet"),
                name="unique_daily_timesheet_per_workplace",
            ),
            models.UniqueConstraint(
                fields=["workplace", "kind", "document_date"],
                condition=models.Q(kind="monthly_inventory"),
                name="unique_monthly_inventory_per_workplace",
            ),
            models.UniqueConstraint(
                fields=["workplace", "kind", "document_date", "delivery_number"],
                condition=models.Q(kind="delivery_note"),
                name="unique_delivery_number_per_workplace_day",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="delivery_note", delivery_number__isnull=False)
                    | ~models.Q(kind="delivery_note")
                ),
                name="delivery_notes_require_number",
            ),
        ]

    def __str__(self):
        suffix = (
            f" #{self.delivery_number}" if self.delivery_number is not None else ""
        )
        return f"{self.get_kind_display()}{suffix} · {self.workplace} · {self.document_date}"

    def clean(self):
        super().clean()
        if self.supervisor.category != Employee.Category.SUPERVISOR:
            raise ValidationError({"supervisor": "The employee must be a shift supervisor."})
        if self.kind == self.Kind.DELIVERY_NOTE:
            if not self.description.strip():
                raise ValidationError({"description": "A goods description is required."})
        elif self.delivery_number is not None:
            raise ValidationError(
                {"delivery_number": "Only delivery notes have a delivery number."}
            )
        if (
            self.kind == self.Kind.MONTHLY_INVENTORY
            and self.document_date.day != 1
        ):
            raise ValidationError(
                {"document_date": "Monthly inventory must use the first day of its month."}
            )

    @property
    def latest_version(self):
        versions = list(self.versions.all())
        return versions[0] if versions else None


class SupervisorDocumentVersion(models.Model):
    document = models.ForeignKey(
        SupervisorDocument, related_name="versions", on_delete=models.PROTECT
    )
    version_number = models.PositiveIntegerField()
    file = models.FileField(
        upload_to=supervisor_document_upload_path,
        validators=[FileExtensionValidator(["xlsx", "xls", "pdf", "jpg", "jpeg", "png"])],
    )
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField()
    telegram_file_unique_id = models.CharField(max_length=128, blank=True)
    uploaded_by = models.ForeignKey(
        Employee, related_name="supervisor_document_versions", on_delete=models.PROTECT
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "version_number"],
                name="unique_supervisor_document_version",
            )
        ]

    def __str__(self):
        return f"{self.document} · v{self.version_number}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Supervisor document versions are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Supervisor document versions are immutable")
