"""API views for the payout app — the Phase 8 surface, nothing more.

Six endpoints, and the interesting thing about them is which side of the wall
each caller is on: the lead generates, finalizes and reads the academy; a teacher
reads their own records and their own statement; a student or parent has no
endpoint here at all.

Every teacher-facing queryset is narrowed through one helper,
``services.payouts_for(teacher=...)``, rather than each view writing its own
filter. CLAUDE.md is explicit that private financial data must never be exposed
through a broad queryset, and the cheapest way to keep that promise is to have
exactly one place where "whose payouts" is decided.

Nothing here writes to ``Booking``. Generation reads completed sessions and
creates payout records; marking a session taught stays the scheduling app's
decision.
"""

import csv
from datetime import timezone as dt_timezone

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Role, User, TEACHER_ROLES
from organizations.models import MembershipStatus, OrganizationRole
from organizations.permissions import IsOrganizationMember
from organizations.views import OrganizationScopedMixin

from .exceptions import PayoutAlreadyFinalized
from .models import PayoutStatus, TeacherPayout
from .permissions import IsLeadTeacher, IsTeacher
from .serializers import (
    BulkFinalizationResultSerializer,
    GenerationResultSerializer,
    MyStatementSerializer,
    MyTeacherPayoutSerializer,
    PayoutBulkFinalizeSerializer,
    PayoutGenerateSerializer,
    StatementSerializer,
    TeacherPayoutConfirmSerializer,
    TeacherPayoutSerializer,
)
from .services import (
    PAYOUT_RELATED,
    bulk_finalize_payouts,
    generate_payouts,
    payouts_for,
    statement_for,
)


class AcademyScopedView(OrganizationScopedMixin):
    """Shared plumbing for the academy-scoped payout views.

    The organization comes from the URL kwarg ``organization_pk`` and is resolved
    to the caller's membership by the parent mixin; and it is put into the
    serializer context, which is how serializers narrow their querysets
    to one tenant.
    """

    organization_url_kwarg = "organization_pk"

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.caller_membership:
            context["organization"] = self.organization
        return context


class Conflict(APIException):
    """409 — the request is valid but the record is in the wrong state.

    One caller: finalizing a payout that is already finalized. The same shape
    ``scheduling.views.Conflict`` uses for cancelling a booking that is not
    scheduled, and for the same reason — the request is well formed, the record
    has simply moved on.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This action conflicts with the current state of the payout."
    default_code = "conflict"


#: What a losing concurrent generation run is told. Generation pre-checks which
#: bookings are already paid and the database holds the same two rules as
#: constraints, so two runs over one period cannot both create a record — the
#: loser's whole transaction rolls back, having created nothing, and the honest
#: answer is "someone else is generating this period, try again" rather than a 500.
CONCURRENT_GENERATION_DETAIL = (
    "Another payout generation run for this period committed first. Nothing was "
    "created by this request; repeat it to pick up whatever is still missing."
)


#: Both period bounds, as the query-string names the spec uses.
PERIOD_PARAMS = [
    OpenApiParameter(
        name="start",
        type=str,
        location=OpenApiParameter.QUERY,
        description="Inclusive period start, ISO-8601 UTC, e.g. 2026-08-01T00:00:00Z.",
    ),
    OpenApiParameter(
        name="end",
        type=str,
        location=OpenApiParameter.QUERY,
        description="Exclusive period end, ISO-8601 UTC.",
    ),
]


def optional_datetime_param(request, name):
    """Read an optional ISO-8601 datetime query parameter, or 400 if unparseable.

    The local copy ``assessment.views`` keeps, and the reasoning transfers
    unchanged: a period bound that cannot be parsed is refused rather than
    ignored, because silently dropping it widens the window the caller asked for
    and a statement over the wrong period is worse than an error message.

    A value with no offset is read as UTC rather than as the caller's own zone.
    Which sessions belong to a period is decided by their stored UTC start, so
    letting local presentation move the boundary would move the money with it.
    """
    raw = request.query_params.get(name)
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        raise ValidationError(
            {name: ["Must be an ISO-8601 datetime, e.g. 2026-08-01T00:00:00Z."]}
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def optional_period(request):
    """The ``start``/``end`` window a listing asked for. Either may be absent."""
    start = optional_datetime_param(request, "start")
    end = optional_datetime_param(request, "end")
    if start and end and end <= start:
        raise ValidationError({"end": ["A period ends after it starts."]})
    return start, end


def required_period(request):
    """The window a *statement* asked for. Both bounds are required.

    A statement without bounds would be "everything ever earned", which is a
    different document than the one the spec describes — and one whose total
    changes every time a session is taught.
    """
    start, end = optional_period(request)
    missing = {
        name: ["This query parameter is required for a statement."]
        for name, value in (("start", start), ("end", end))
        if value is None
    }
    if missing:
        raise ValidationError(missing)
    return start, end


def requested_teacher(request, organization=None, name="teacher_id"):
    """The teacher an owner/admin endpoint is asking about, or None.

    An unknown id, or a teacher not belonging to the organization, is a 400
    rather than an empty statement, for the reason
    ``assessment.views.requested_track`` gives: answering "what does teacher 99
    earn" with zero sessions looks like a real answer.
    """
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        teacher_id = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: ["Must be an integer."]})
    qs = User.objects.filter(pk=teacher_id).filter(
        Q(role__in=TEACHER_ROLES) | Q(organization_memberships__role=OrganizationRole.TEACHER)
    )
    if organization is not None:
        qs = qs.filter(
            organization_memberships__organization=organization,
            organization_memberships__status=MembershipStatus.ACTIVE,
        )
    teacher = qs.distinct().first()
    if teacher is None:
        raise ValidationError({name: ["No such teacher."]})
    return teacher


# --- Teacher-facing ----------------------------------------------------------


class MyPayoutListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/payouts/organizations/<organization_pk>/mine/ — the caller's own payout records.

    Optionally bounded by ``?start=&end=``; unbounded it is the teacher's whole
    payout history in this academy, which is theirs to read. The queryset is scoped
    to ``request.user`` and ``self.organization``, and there is no parameter that
    could widen it — another teacher's records are not a 403 here, they simply are
    not in the set.

    Generated and finalized records both appear. A teacher seeing only finalized
    ones would have no way to check a draft before it becomes history.
    """

    serializer_class = MyTeacherPayoutSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsTeacher]

    def get_queryset(self):
        start, end = optional_period(self.request)
        return payouts_for(
            organization=self.organization,
            teacher=self.request.user,
            period_start=start,
            period_end=end,
        )

    @extend_schema(
        parameters=PERIOD_PARAMS,
        responses={
            200: OpenApiResponse(response=MyTeacherPayoutSerializer(many=True)),
            400: OpenApiResponse(description="Unparseable or inverted period."),
            403: OpenApiResponse(description="Not an active teacher account in this academy."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class MyStatementView(AcademyScopedView, generics.GenericAPIView):
    """GET /api/payouts/organizations/<organization_pk>/statements/mine/?start=&end= — the teacher's own statement.

    A statement is computed from the payout records it lists, so the total is
    always the sum of the rows shown underneath it. Both bounds are required: a
    statement is a document about a period.
    """

    serializer_class = MyStatementSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsTeacher]

    @extend_schema(
        parameters=PERIOD_PARAMS,
        responses={
            200: OpenApiResponse(response=MyStatementSerializer),
            400: OpenApiResponse(description="Missing, unparseable or inverted period."),
            403: OpenApiResponse(description="Not an active teacher account in this academy."),
        },
    )
    def get(self, request, *args, **kwargs):
        start, end = required_period(request)
        statement = statement_for(
            organization=self.organization,
            teacher=request.user,
            period_start=start,
            period_end=end,
        )
        return Response(self.get_serializer(statement).data)


class TeacherPayoutConfirmView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/payouts/organizations/<organization_pk>/<int:pk>/confirm/ — teacher confirms receipt or disputes payout.

    The teacher confirms having received the transfer (confirmed=True) or disputes it
    with a mandatory reason (confirmed=False, reason="...").
    Only the payout's assigned teacher may confirm it, and only once the payout is finalized.
    """

    serializer_class = TeacherPayoutConfirmSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsTeacher]

    def get_queryset(self):
        return (
            TeacherPayout.objects.in_organization(self.organization)
            .filter(teacher=self.request.user)
            .select_related(*PAYOUT_RELATED)
        )

    @extend_schema(
        request=TeacherPayoutConfirmSerializer,
        responses={
            200: OpenApiResponse(
                response=MyTeacherPayoutSerializer,
                description="Payout confirmation status recorded.",
            ),
            400: OpenApiResponse(description="Invalid payload or missing dispute reason."),
            403: OpenApiResponse(description="Not authorized or not the payout teacher."),
            404: OpenApiResponse(description="No such payout for this teacher in this academy."),
            409: OpenApiResponse(description="Payout cannot be confirmed because it is not finalized."),
        },
    )
    def post(self, request, *args, **kwargs):
        payout = self.get_object()
        if not payout.is_finalized:
            raise Conflict("Payout cannot be confirmed because it has not been cleared/finalized by the academy yet.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        payout.confirm_receipt(
            confirmed=data["confirmed"],
            notes=data.get("reason", ""),
        )

        return Response(
            MyTeacherPayoutSerializer(payout, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )


# --- Lead-facing -------------------------------------------------------------


class LeadPayoutListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/payouts/organizations/<organization_pk>/lead/ — academy-wide payout records.

    Filterable by ``?teacher_id=`` and by period. Unfiltered it is every payout
    record in the academy, which is the owner/admin's to see and nobody else's:
    the permission class is the only thing standing between this queryset and a
    sub-teacher reading their colleagues' income, which is why it is the *first*
    thing the class declares.
    """

    serializer_class = TeacherPayoutSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    def get_queryset(self):
        start, end = optional_period(self.request)
        return payouts_for(
            organization=self.organization,
            teacher=requested_teacher(self.request, organization=self.organization),
            period_start=start,
            period_end=end,
        )

    @extend_schema(
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter(
                name="teacher_id",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Narrow the listing to one teacher.",
            )
        ],
        responses={
            200: OpenApiResponse(response=TeacherPayoutSerializer(many=True)),
            400: OpenApiResponse(description="Unknown teacher, or a bad period."),
            403: OpenApiResponse(description="Not an organization owner or administrator."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class LeadStatementView(AcademyScopedView, generics.GenericAPIView):
    """GET /api/payouts/organizations/<organization_pk>/statements/?teacher_id=&start=&end= — any teacher's statement.

    The same computation ``statements/mine/`` performs, for a teacher the owner/admin
    names. ``teacher_id`` is required, because a statement is about one teacher —
    an academy-wide total is a different document and Phase 8 was not asked for
    one.
    """

    serializer_class = StatementSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    @extend_schema(
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter(
                name="teacher_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Whose statement to compute.",
            )
        ],
        responses={
            200: OpenApiResponse(response=StatementSerializer),
            400: OpenApiResponse(
                description="Missing teacher_id, unknown teacher, or a bad period."
            ),
            403: OpenApiResponse(description="Not an organization owner or administrator."),
        },
    )
    def get(self, request, *args, **kwargs):
        teacher = requested_teacher(request, organization=self.organization)
        if teacher is None:
            raise ValidationError(
                {"teacher_id": ["This query parameter is required for a statement."]}
            )
        start, end = required_period(request)
        statement = statement_for(
            organization=self.organization,
            teacher=teacher,
            period_start=start,
            period_end=end,
        )
        return Response(self.get_serializer(statement).data)


class PayoutGenerateView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/payouts/organizations/<organization_pk>/generate/ — turn a period's completed sessions into payouts.

    Safe to repeat, which is the property that makes it usable: a lead who is not
    sure whether the run went through can simply run it again, and the second run
    creates nothing. Existing records are never recalculated, so a rate raised
    yesterday does not reprice last month's payroll.

    The response reports both halves of the run. ``skipped`` is not noise: a
    sub-teacher's session skipped for ``no_payout_rate`` is the lead's cue to set
    a rate, and a group-class seat skipped for ``cohort_session_already_paid``
    explains why a busy session produced one record.
    """

    serializer_class = PayoutGenerateSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=GenerationResultSerializer,
                description=(
                    "Records created, plus every booking that was skipped and "
                    "why. A repeat run answers 201 with created_count 0."
                ),
            ),
            400: OpenApiResponse(description="Bad period, or an unknown teacher."),
            403: OpenApiResponse(description="Not an organization owner or administrator."),
        }
    )
    def post(self, request, *args, **kwargs):
        request_serializer = self.get_serializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        payload = request_serializer.validated_data
        try:
            result = generate_payouts(
                organization=self.organization,
                period_start=payload["period_start"],
                period_end=payload["period_end"],
                teacher=payload.get("teacher"),
            )
        except (DjangoValidationError, IntegrityError) as exc:
            # The one path that gets here is a genuine race: a payout this run
            # read as missing existed by the time it inserted. Both the
            # OneToOne on booking and the partial unique on cohort can raise it,
            # and either way the atomic() block has already rolled back.
            raise Conflict(CONCURRENT_GENERATION_DETAIL) from exc
        body = GenerationResultSerializer(
            result, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)


class PayoutFinalizeView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/payouts/organizations/<organization_pk>/<int:pk>/finalize/ — make one payout history.

    Owner/admin-only, and one-way: after this the record refuses every write, including
    the lead's own. A payout that is already finalized answers 409 rather than
    silently succeeding, because a second finalization means the caller is acting
    on a stale copy of a financial record.
    """

    serializer_class = TeacherPayoutSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    def get_queryset(self):
        return (
            TeacherPayout.objects.in_organization(self.organization)
            .select_related(*PAYOUT_RELATED)
        )

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(
                response=TeacherPayoutSerializer,
                description="Finalized. The record is now immutable.",
            ),
            403: OpenApiResponse(description="Not an organization owner or administrator."),
            404: OpenApiResponse(description="No such payout."),
            409: OpenApiResponse(description="Already finalized."),
        },
    )
    def post(self, request, *args, **kwargs):
        payout = self.get_object()
        try:
            payout.finalize()
        except PayoutAlreadyFinalized as exc:
            raise Conflict(str(exc)) from exc
        body = self.get_serializer(payout).data
        return Response(body, status=status.HTTP_200_OK)


class PayoutExportView(AcademyScopedView, generics.GenericAPIView):
    """GET /api/payouts/organizations/<organization_pk>/export/ — bank transfer batch CSV export.

    Enables the academy lead/owner to export a CSV batch file of payouts
    for offline execution via corporate or mobile banking (NIP/NUBAN batch payment).
    """

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    @extend_schema(
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter(
                name="teacher_id",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Narrow the export to one teacher.",
            ),
            OpenApiParameter(
                name="status",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by payout status (e.g. 'generated', 'finalized', 'all'). Defaults to 'all'.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="CSV batch export of teacher payouts with bank details."),
            400: OpenApiResponse(description="Invalid teacher_id or period parameter."),
            403: OpenApiResponse(description="Not an organization owner or administrator."),
        },
    )
    def get(self, request, *args, **kwargs):
        start, end = optional_period(request)
        teacher = requested_teacher(request, organization=self.organization)
        status_param = request.query_params.get("status", "all").strip().lower()

        rows = payouts_for(
            organization=self.organization,
            teacher=teacher,
            period_start=start,
            period_end=end,
        )
        if status_param in (PayoutStatus.GENERATED, PayoutStatus.FINALIZED):
            rows = rows.filter(status=status_param)

        # Prefetch teacher bank configs for this organization
        teacher_ids = list(rows.values_list("teacher_id", flat=True).distinct())
        from accounts.models import OrganizationTeacherConfiguration

        configs = OrganizationTeacherConfiguration.objects.filter(
            membership__organization=self.organization,
            membership__user_id__in=teacher_ids,
        ).select_related("membership__user", "membership__user__teacher_profile")
        config_map = {c.membership.user_id: c for c in configs}

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        filename_prefix = f"payouts_org_{self.organization.pk}"
        if start and end:
            s_str = start.strftime("%Y%m%d")
            e_str = end.strftime("%Y%m%d")
            filename = f"{filename_prefix}_{s_str}_to_{e_str}.csv"
        else:
            filename = f"{filename_prefix}_batch.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([
            "Beneficiary Name",
            "Bank Name",
            "Bank Code",
            "Account Number",
            "Amount",
            "Currency",
            "Narration",
            "Teacher Username",
            "Payout ID",
            "Status",
            "Duration (Minutes)",
            "Rate Used",
            "Session Start (UTC)",
            "Teacher Confirmation",
            "Teacher Notes",
        ])

        for p in rows:
            cfg = config_map.get(p.teacher_id)
            user = p.teacher
            profile = getattr(user, "teacher_profile", None)

            beneficiary_name = ""
            bank_name = ""
            bank_code = ""
            account_number = ""

            if cfg:
                beneficiary_name = cfg.effective_account_name
                bank_name = cfg.effective_bank_name
                bank_code = cfg.effective_bank_code
                account_number = cfg.effective_account_number
            elif profile:
                beneficiary_name = profile.account_name
                bank_name = profile.bank_name
                bank_code = profile.bank_code
                account_number = profile.account_number

            if not beneficiary_name:
                beneficiary_name = user.get_full_name() or user.username

            narration = f"Payout #{p.pk} - {user.username}"
            session_start = p.session_start_utc.isoformat() if p.session_start_utc else ""

            writer.writerow([
                beneficiary_name,
                bank_name,
                bank_code,
                account_number,
                f"{p.amount:.2f}",
                p.currency,
                narration,
                user.username,
                p.pk,
                p.status,
                p.minutes_paid,
                f"{p.rate_used:.2f}",
                session_start,
                p.confirmation_status,
                p.confirmation_notes,
            ])

        return response


class PayoutBulkFinalizeView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/payouts/organizations/<organization_pk>/bulk-finalize/ — mark multiple payouts as settled.

    Allows the lead or owner to finalize awaiting payouts in a period or for specific IDs
    after executing bank transfers off-platform.
    """

    serializer_class = PayoutBulkFinalizeSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    @extend_schema(
        request=PayoutBulkFinalizeSerializer,
        responses={
            200: OpenApiResponse(
                response=BulkFinalizationResultSerializer,
                description="Batch finalization complete.",
            ),
            400: OpenApiResponse(description="Invalid request parameters."),
            403: OpenApiResponse(description="Not an organization owner or administrator."),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        payout_ids = data.get("payout_ids")
        period_start = data.get("period_start")
        period_end = data.get("period_end")
        teacher_id = data.get("teacher_id")

        teacher = None
        if teacher_id is not None:
            teacher = requested_teacher(request, organization=self.organization, name="teacher_id")

        result = bulk_finalize_payouts(
            organization=self.organization,
            payout_ids=payout_ids,
            period_start=period_start,
            period_end=period_end,
            teacher=teacher,
        )
        return Response(result, status=status.HTTP_200_OK)
