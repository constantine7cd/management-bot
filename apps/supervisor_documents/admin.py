from django.contrib import admin

from .models import SupervisorDocument, SupervisorDocumentVersion


class VersionInline(admin.TabularInline):
    model = SupervisorDocumentVersion
    extra = 0
    can_delete = False
    readonly_fields = (
        "version_number",
        "file",
        "original_filename",
        "mime_type",
        "size_bytes",
        "uploaded_by",
        "uploaded_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SupervisorDocument)
class SupervisorDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "document_date",
        "workplace",
        "supervisor",
        "delivery_number",
        "created_at",
    )
    list_filter = ("kind", "workplace")
    inlines = [VersionInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SupervisorDocumentVersion)
class SupervisorDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("document", "version_number", "uploaded_by", "uploaded_at")
    readonly_fields = ("uploaded_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
