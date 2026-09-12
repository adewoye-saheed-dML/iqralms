from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.db import transaction
from drf_spectacular.utils import extend_schema, OpenApiResponse

from organizations.views import OrganizationScopedMixin
from .models import ImportJob, ImportStatus
from .serializers import ImportJobValidateSerializer, ImportJobResponseSerializer
from .permissions import CanImportRecords
from .services import parse_import_file, guess_column_mapping, ImportValidator, commit_import

class AcademyScopedView(OrganizationScopedMixin):
    organization_url_kwarg = "organization_pk"

class ImportValidateView(AcademyScopedView, generics.CreateAPIView):
    permission_classes = [CanImportRecords]
    serializer_class = ImportJobValidateSerializer
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="imports_validate",
        summary="Upload and validate an import file",
        responses={201: ImportJobResponseSerializer}
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        file_obj = serializer.validated_data["file"]
        kind = serializer.validated_data["kind"]
        provided_mapping = serializer.validated_data.get("column_mapping", {})
        
        # 1. Parse
        try:
            raw_rows = parse_import_file(file_obj, file_obj.name)
        except Exception as e:
            return Response({"error": f"Failed to parse file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
            
        if not raw_rows:
            return Response({"error": "File is empty or could not be parsed into rows."}, status=status.HTTP_400_BAD_REQUEST)
            
        # 2. Map
        headers = list(raw_rows[0].keys())
        mapping = provided_mapping or guess_column_mapping(headers, kind)
        
        # 3. Create Job
        with transaction.atomic():
            job = ImportJob.objects.create(
                organization=self.organization,
                created_by=request.user,
                kind=kind,
                file_name=file_obj.name,
                file_size=file_obj.size,
                file_type=file_obj.content_type,
                column_mapping=mapping
            )
            
            # 4. Validate all rows
            validator = ImportValidator(job, raw_rows)
            validator.validate()
            
            from audit_logs.services import record_event
            record_event(
                organization=self.organization,
                actor=request.user,
                action="bulk_import.validated",
                target=job,
                metadata={
                    "kind": job.kind,
                    "row_count": job.row_count,
                    "valid_row_count": job.valid_row_count,
                    "invalid_row_count": job.invalid_row_count,
                }
            )
            
        response_serializer = ImportJobResponseSerializer(job)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class ImportCommitView(AcademyScopedView, generics.GenericAPIView):
    permission_classes = [CanImportRecords]
    
    def get_queryset(self):
        return ImportJob.objects.filter(organization=self.organization)

    @extend_schema(
        operation_id="imports_commit",
        summary="Commit a validated import job",
        request=None,
        responses={200: ImportJobResponseSerializer}
    )
    def post(self, request, *args, **kwargs):
        # Prevent concurrent commits on the same job
        with transaction.atomic():
            try:
                job = self.get_queryset().select_for_update().get(pk=kwargs["pk"])
            except ImportJob.DoesNotExist:
                from rest_framework.exceptions import NotFound
                raise NotFound("Import job not found in this academy.")
            
            if job.status != ImportStatus.VALIDATED:
                return Response(
                    {"error": "Job is not in a validated state and cannot be committed."}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            try:
                commit_import(job)
                from audit_logs.services import record_event
                record_event(
                    organization=self.organization,
                    actor=request.user,
                    action="bulk_import.committed",
                    target=job,
                    metadata={
                        "kind": job.kind,
                        "row_count": job.row_count,
                        "valid_row_count": job.valid_row_count,
                        "created_count": job.created_count,
                        "updated_count": job.updated_count,
                        "error_count": len(job.error_report) if job.error_report else 0,
                    }
                )
            except Exception as e:
                # job object in memory has error state from commit_import
                response_serializer = ImportJobResponseSerializer(job)
                return Response(response_serializer.data, status=status.HTTP_400_BAD_REQUEST)
                
            response_serializer = ImportJobResponseSerializer(job)
            return Response(response_serializer.data, status=status.HTTP_200_OK)
