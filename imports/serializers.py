from rest_framework import serializers
from .models import ImportJob, ImportKind

class ImportJobValidateSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)
    kind = serializers.ChoiceField(choices=ImportKind.choices, required=True)
    column_mapping = serializers.JSONField(required=False, default=dict)
    
    def validate_file(self, value):
        # Basic validation: CSV or XLSX
        ext = value.name.lower()
        if not (ext.endswith('.csv') or ext.endswith('.xlsx')):
            raise serializers.ValidationError("File must be .csv or .xlsx")
        
        # Prevent oversized files (e.g. 5MB)
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("File size exceeds 5MB limit.")
            
        return value

class ImportJobResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportJob
        fields = [
            "id",
            "organization",
            "created_by",
            "kind",
            "file_name",
            "file_size",
            "file_type",
            "status",
            "column_mapping",
            "row_count",
            "valid_row_count",
            "invalid_row_count",
            "created_count",
            "updated_count",
            "skipped_count",
            "error_count",
            "error_report",
            "created_at",
            "started_at",
            "completed_at"
        ]
        read_only_fields = fields
