"""Serializers for the payout API.

Two things to know here.

**There are two read shapes, and the difference is deliberate.**
``TeacherPayoutSerializer`` is the lead's view of a payout — whose it is
included. ``MyTeacherPayoutSerializer`` is the teacher's own view, which omits
``teacher`` because there is only one answer and it is the caller. Separate
serializers rather than one with a conditional field, for the reason
``pricing.serializers`` gives: a field that is private only when someone
remembers to hide it eventually leaks.

**Neither shape mentions money the family pays.** No serializer here touches
``pricing.PricingAgreement``, and none touches assessment scores. A teacher
reading their statement learns what they earned and which sessions earned it —
not what each family negotiated, which is the lead's private record (mvp-spec
section 3).

Whether a payout is *legal* — the right teacher, a completed booking, an amount
that matches its own rate, a finalized record refusing to change — is never
decided here. Those rules live in ``TeacherPayout.clean()`` and are surfaced from
there, so the API and a direct ORM write cannot disagree.
"""

from datetime import timezone as dt_timezone

from rest_framework import serializers

from accounts.models import Role, User
from scheduling.models import Booking

from .models import StatementStatus, TeacherPayout


def as_drf_error(exc):
    """Re-raise a model ``ValidationError`` as a DRF one, so it lands as a 400.

    The local copy ``scheduling.serializers`` and ``pricing.serializers`` each
    keep, rather than an import across apps: it is four lines, and the apps are
    otherwise independent.
    """
    return serializers.ValidationError(
        getattr(exc, "message_dict", None) or {"detail": exc.messages}
    )


class PayoutSessionSerializer(serializers.ModelSerializer):
    """The session a payout is for — enough to reconcile a total, no more.

    This is what the spec means by "the session references needed to understand
    how the total was produced": which student, which level, when, how long, and
    whether it was a group class. ``cohort`` being non-null is also the reader's
    explanation for why a busy group session appears once rather than six times.
    """

    student = serializers.SlugRelatedField(slug_field="username", read_only=True)
    level = serializers.SlugRelatedField(slug_field="name", read_only=True)
    track = serializers.SlugRelatedField(
        source="level.track", slug_field="slug", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id",
            "student",
            "level",
            "track",
            "start_time_utc",
            "duration_minutes",
            "status",
            "status_display",
            "cohort",
        ]
        read_only_fields = fields


class MyTeacherPayoutSerializer(serializers.ModelSerializer):
    """The teacher's own payout: what they earned, and for which session.

    ``teacher`` is absent — the caller is the only possible answer. Everything
    that produced the amount is present, because a payout the teacher cannot
    check is not transparent: ``minutes_paid`` and ``rate_used`` are the two
    inputs, and ``amount`` is what the one formula made of them.
    """

    booking = PayoutSessionSerializer(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = TeacherPayout
        fields = [
            "id",
            "booking",
            "cohort",
            "minutes_paid",
            "rate_used",
            "amount",
            "currency",
            "status",
            "status_display",
            "created_at",
            "finalized_at",
        ]
        read_only_fields = fields


class TeacherPayoutSerializer(MyTeacherPayoutSerializer):
    """The lead's view: the same record, plus whose it is."""

    teacher = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta(MyTeacherPayoutSerializer.Meta):
        fields = ["id", "teacher"] + MyTeacherPayoutSerializer.Meta.fields[1:]
        read_only_fields = fields


class PayoutPeriodSerializer(serializers.Serializer):
    """A bounded period: ``[period_start, period_end)``, in UTC.

    Half-open, so two consecutive periods neither overlap nor leave a session
    unpaid. ``default_timezone`` is UTC explicitly rather than by inheritance from
    ``settings.TIME_ZONE``: a caller who posts a naive ``2026-08-01T00:00:00``
    must not have their own timezone silently decide which sessions fall in
    August, which is the failure mode the spec's period-boundary section warns
    about.
    """

    period_start = serializers.DateTimeField(default_timezone=dt_timezone.utc)
    period_end = serializers.DateTimeField(default_timezone=dt_timezone.utc)

    def validate(self, attrs):
        if attrs["period_end"] <= attrs["period_start"]:
            raise serializers.ValidationError(
                {"period_end": ["A period ends after it starts."]}
            )
        return attrs


class PayoutGenerateSerializer(PayoutPeriodSerializer):
    """The lead's generation request: a period, and optionally one teacher.

    ``teacher`` narrows a run to one person — useful when a rate was set late and
    only that teacher's sessions need picking up. Omitting it generates for the
    whole academy. Either way the run is idempotent, so narrowing is a convenience
    rather than a way to avoid duplicates.

    Only ``lead`` and ``sub`` accounts are offered, because nobody else can be a
    booking's teacher. A student id here is a 400 rather than an empty run.
    """

    teacher = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role__in=[Role.LEAD, Role.SUB]),
        required=False,
        allow_null=True,
    )


class SkippedBookingSerializer(serializers.Serializer):
    """One booking generation looked at and did not pay, and why.

    Reported rather than swallowed: ``no_payout_rate`` on a sub-teacher's session
    is the lead's cue to set a rate and run again, and it must not be
    indistinguishable from a quiet period.
    """

    booking_id = serializers.IntegerField(read_only=True)
    teacher_id = serializers.IntegerField(read_only=True)
    reason = serializers.CharField(read_only=True)


class GenerationResultSerializer(serializers.Serializer):
    """What a generation run did — created records, and skipped bookings."""

    period_start = serializers.DateTimeField(read_only=True)
    period_end = serializers.DateTimeField(read_only=True)
    created_count = serializers.IntegerField(read_only=True)
    skipped_count = serializers.IntegerField(read_only=True)
    total_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    created = TeacherPayoutSerializer(many=True, read_only=True)
    skipped = SkippedBookingSerializer(many=True, read_only=True)


class MyStatementSerializer(serializers.Serializer):
    """A teacher's own statement for one period.

    Computed from the payout records listed inside it, so the total and the rows
    cannot disagree — there is no statement table to fall out of step with them.
    ``status`` says how settled the period is; ``empty`` means no payout record
    exists for it yet, which is not the same statement as "you earned nothing".
    """

    period_start = serializers.DateTimeField(read_only=True)
    period_end = serializers.DateTimeField(read_only=True)
    session_count = serializers.IntegerField(read_only=True)
    finalized_count = serializers.IntegerField(read_only=True)
    total_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    currency = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(choices=StatementStatus.choices, read_only=True)
    payouts = MyTeacherPayoutSerializer(many=True, read_only=True)


class StatementSerializer(MyStatementSerializer):
    """The lead's view of any teacher's statement: the same totals, plus whose.

    ``teacher`` is a plain ``CharField`` over the username rather than a related
    field: a statement is a computed dataclass, not a model instance, so there is
    no model for a relational field to resolve against.
    """

    teacher = serializers.CharField(source="teacher.username", read_only=True)
    payouts = TeacherPayoutSerializer(many=True, read_only=True)
