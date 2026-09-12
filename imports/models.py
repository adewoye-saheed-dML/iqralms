from django.db import models
from django.utils.translation import gettext_lazy as _

class ImportKind(models.TextChoices):
    TEACHERS = "teachers", _("Teachers")
    STUDENTS = "students", _("Students")
    PARENTS = "parents", _("Parents")

class ImportStatus(models.TextChoices):
    UPLOADED = "uploaded", _("Uploaded")
    VALIDATED = "validated", _("Validated")
    FAILED = "failed", _("Failed")
    RUNNING = "running", _("Running")
    COMPLETED = "completed", _("Completed")
    PARTIALLY_COMPLETED = "partially_completed", _("Partially Completed")

class ImportJob(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="import_jobs"
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_imports"
    )
    kind = models.CharField(max_length=32, choices=ImportKind.choices)
    
    file_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField()
    file_type = models.CharField(max_length=128)
    
    status = models.CharField(
        max_length=32, 
        choices=ImportStatus.choices, 
        default=ImportStatus.UPLOADED
    )
    
    column_mapping = models.JSONField(default=dict, blank=True)
    valid_rows = models.JSONField(default=list, blank=True)
    
    row_count = models.PositiveIntegerField(default=0)
    valid_row_count = models.PositiveIntegerField(default=0)
    invalid_row_count = models.PositiveIntegerField(default=0)
    
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    
    error_report = models.JSONField(default=list, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} import ({self.get_status_display()})"
