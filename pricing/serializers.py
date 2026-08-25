"""Serializers for the pricing app.

The one thing to know here: **there are two read shapes, and the difference is
deliberate.** ``PricingAgreementSerializer`` is what the lead sees — the whole
row, ``notes`` included. ``MyPricingAgreementSerializer`` is what the family
sees, and it omits ``notes`` because that field is the lead's private reasoning
about a negotiation, not something the other party to it reads back. The spec
says so in as many words, so the two shapes are separate serializers rather than
one with a conditional field: a field that is private only when someone remembers
to hide it eventually leaks.

Whether an agreement is *legal* — a student who is really a student, an approver
who is really the lead, at most one active rate per level — is never decided here.
Those rules live in ``PricingAgreement.clean()`` and ``save()`` and are surfaced
from there, so the API and a direct ORM write cannot disagree.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from accounts.models import Role, User
from curriculum.models import Level
from curriculum.serializers import LevelSerializer

from .models import PricingAgreement


def as_drf_error(exc):
    """Re-raise a model ``ValidationError`` as a DRF one, so it lands as a 400.

    A local copy of the helper ``scheduling.serializers`` has, rather than an
    import across apps: it is four lines, and the two apps are otherwise
    independent of each other.
    """
    return serializers.ValidationError(
        getattr(exc, "message_dict", None) or {"detail": exc.messages}
    )


class PricingStudentSerializer(serializers.ModelSerializer):
    """The student as a pricing row needs them — no signup_code, no sprawl."""

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "timezone"]
        read_only_fields = fields


class PricingAgreementSerializer(serializers.ModelSerializer):
    """The lead's view: everything, including the private note.

    Used by the create response and by the per-student history listing, so a
    superseded row reads exactly like a live one apart from ``active``.
    """

    student = PricingStudentSerializer(read_only=True)
    level = LevelSerializer(read_only=True)
    track = serializers.SlugRelatedField(
        source="level.track", slug_field="slug", read_only=True
    )
    approved_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)

    class Meta:
        model = PricingAgreement
        fields = [
            "id",
            "student",
            "level",
            "track",
            "standard_rate",
            "agreed_rate",
            "reason",
            "reason_display",
            "approved_by",
            "notes",
            "active",
        ]
        read_only_fields = fields


class MyPricingAgreementSerializer(serializers.ModelSerializer):
    """The family's view: their rate, without the lead's reasoning.

    ``notes`` and ``approved_by`` are both absent. The note is private by
    instruction; the approver is omitted because there is exactly one lead and
    "who agreed this" is internal bookkeeping rather than something the family
    needs from this endpoint.
    """

    level = LevelSerializer(read_only=True)
    track = serializers.SlugRelatedField(
        source="level.track", slug_field="slug", read_only=True
    )
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)

    class Meta:
        model = PricingAgreement
        fields = [
            "id",
            "level",
            "track",
            "standard_rate",
            "agreed_rate",
            "reason",
            "reason_display",
            "active",
        ]
        read_only_fields = fields


class PricingAgreementCreateSerializer(serializers.ModelSerializer):
    """The lead records a rate. Creating one supersedes the previous one.

    ``approved_by`` is not a client field — it is the requesting lead, the same
    way ``PlacementResult.reviewed_by`` is stamped from the request rather than
    supplied. ``active`` is not a client field either: a new agreement is the live
    one by definition, and deactivating an old one is the model's job, not a flag
    a caller sets.
    """

    student = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=Role.STUDENT)
    )
    level = serializers.PrimaryKeyRelatedField(queryset=Level.objects.all())

    class Meta:
        model = PricingAgreement
        fields = ["student", "level", "standard_rate", "agreed_rate", "reason", "notes"]
        extra_kwargs = {"notes": {"required": False}}

    def create(self, validated_data):
        agreement = PricingAgreement(
            approved_by=self.context["request"].user, **validated_data
        )
        try:
            agreement.save()
        except DjangoValidationError as exc:
            # A non-student student, or an approver who is not the lead — both
            # model rules, surfaced as 400s rather than 500s.
            raise as_drf_error(exc) from exc
        return agreement
