from rest_framework import serializers
from .models import AuditLog

class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "organization",
            "actor",
            "actor_type",
            "actor_id_snapshot",
            "action",
            "object_type",
            "object_id",
            "metadata",
            "request_id",
            "created_at"
        ]
        read_only_fields = fields
