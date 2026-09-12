from django.db import models
from accounts.models import User
from organizations.models import Organization

class AuditLog(models.Model):
    """
    Central, tenant-aware audit logging capability for sensitive academy administration
    and operational actions.
    """
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="audit_logs"
    )
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="audit_logs"
    )
    action = models.CharField(max_length=128)
    object_type = models.CharField(max_length=128)
    object_id = models.CharField(max_length=128)
    metadata = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.action} on {self.object_type} ({self.object_id})"

    def save(self, *args, **kwargs):
        # Audit records are append-only.
        if self.pk is not None:
            raise ValueError("Audit logs are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit logs cannot be deleted.")
