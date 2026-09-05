"""factory_boy factories for the curriculum app.

Per CLAUDE.md every model gets a factory here before tests are written against
it. Three factories deliberately derive values rather than hardcoding them, so a
factory can never assert a state the model would reject:

* ``LevelFactory.order`` asks the model for the next legal order — ``Level``
  rejects gaps, so a hardcoded order would break the moment a track had two
  levels.
* ``BeginnerSkipPlacementFactory`` builds its track *with* levels, because a
  beginner skip resolves the track's ``order=1`` level at save time.
* the placement factories **admit their participants** into the track's academy
  before saving. SaaS Phase 3 made "the student is an active member of the academy
  that owns this track" a model invariant, and it is checked in ``save()`` — so a
  factory that built the row first and the membership afterwards would produce a
  ``ValidationError`` rather than a placement. ``admit()`` is idempotent, so a test
  that sets up its own membership (a suspended one, say) keeps it.

``TrackFactory`` gets a bare ``OrganizationFactory`` rather than the ``academy()``
helper: curriculum reads nothing from an academy's owner, and giving every one of
the ~200 tracks this suite builds an extra owner account would be cost without
coverage. The tenant-isolation suite uses ``academy()``, because *there* the owner
is the caller.
"""

import factory
from django.utils import timezone as dj_timezone

from accounts.tests.factories import LeadTeacherFactory, StudentFactory
from curriculum.models import Level, PlacementResult, TeacherTrack, Track
from organizations.models import OrganizationMembership, OrganizationRole
from organizations.tests.factories import (
    OrganizationFactory,
    OrganizationMembershipFactory,
)

#: Stands in for a recitation recording. The bytes are never inspected — only
#: the presence of a file matters to the audio-or-skip invariant.
AUDIO_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00fake-recitation-sample"

#: How many levels TrackWithLevelsFactory builds when not told otherwise.
DEFAULT_LEVEL_COUNT = 3


def admit(user, organization, role=None):
    """Give ``user`` an active membership in ``organization``, if they have none.

    Idempotent on purpose: a test that has already built the membership it cares
    about — suspended, or with a particular organization role — must not have it
    silently replaced by a factory's default.

    The role defaults to ``teacher`` for a teaching account and ``staff`` for
    everyone else. ``OrganizationRole`` has no ``student`` or ``parent`` value (a
    gap tech-debt.md records), and ``staff`` is the stand-in Phase 2's tests
    settled on: it means "belongs here, with no authority over anything".
    """
    if user is None or organization is None:
        return None
    organization_id = getattr(organization, "pk", organization)
    existing = OrganizationMembership.objects.filter(
        organization_id=organization_id, user=user
    ).first()
    if existing is not None:
        return existing
    if role is None:
        role = (
            OrganizationRole.TEACHER if user.is_teacher else OrganizationRole.STAFF
        )
    return OrganizationMembershipFactory(
        organization_id=organization_id, user=user, role=role
    )


class TrackFactory(factory.django.DjangoModelFactory):
    """A track with no levels yet, owned by its own new academy."""

    class Meta:
        model = Track

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Track {n}")
    slug = factory.Sequence(lambda n: f"track-{n}")


class LevelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Level

    track = factory.SubFactory(TrackFactory)
    # Levels are appended, never inserted — ask the model what comes next
    # rather than guessing, or the second level in a track fails to save.
    order = factory.LazyAttribute(lambda o: Level.next_order_for(o.track))
    name = factory.Sequence(lambda n: f"Level {n}")
    min_age = None
    group_eligible = False


class GroupEligibleLevelFactory(LevelFactory):
    """A level that may run as a cohort — the precondition Phase 4 reads."""

    group_eligible = True


class TrackWithLevelsFactory(TrackFactory):
    """A track that already has a ladder of levels, orders 1..n.

    ``TrackWithLevelsFactory(levels=1)`` builds just the first level;
    ``levels=0`` builds none, which is the case a beginner skip cannot resolve.
    """

    @factory.post_generation
    def levels(obj, create, extracted, **kwargs):
        if not create:
            return
        count = DEFAULT_LEVEL_COUNT if extracted is None else extracted
        for _ in range(count):
            LevelFactory(track=obj)


class TeacherTrackFactory(factory.django.DjangoModelFactory):
    """One teacher's eligibility for one track, inside one academy.

    ``membership`` is required rather than built, for the reason
    ``accounts.OrganizationTeacherConfigurationFactory`` gives: which
    academy-and-teacher pair is being configured *is* the subject of every test
    that uses this, so naming it is the point. ``track`` must belong to the same
    academy — the model refuses anything else.
    """

    class Meta:
        model = TeacherTrack

    track = factory.SubFactory(TrackFactory)
    active = True


class PlacementResultFactory(factory.django.DjangoModelFactory):
    """A pending placement: audio submitted, waiting on the lead's review."""

    class Meta:
        model = PlacementResult

    student = factory.SubFactory(StudentFactory)
    track = factory.SubFactory(TrackFactory)
    audio_sample = factory.django.FileField(
        filename="recitation.mp3", data=AUDIO_BYTES
    )
    skipped_as_beginner = False

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        # Before the row, not after: PlacementResult.save() validates that the
        # student — and the reviewer, when there is one — is an active member of
        # the academy that owns the track.
        organization = getattr(kwargs.get("track"), "organization_id", None)
        admit(kwargs.get("student"), organization)
        admit(kwargs.get("reviewed_by"), organization)
        return super()._create(model_class, *args, **kwargs)


class BeginnerSkipPlacementFactory(PlacementResultFactory):
    """A self-declared beginner: ``save()`` places them at the track's level 1."""

    track = factory.SubFactory(TrackWithLevelsFactory)
    audio_sample = None
    skipped_as_beginner = True


class ReviewedPlacementFactory(PlacementResultFactory):
    """An audio placement the lead teacher has already levelled."""

    track = factory.SubFactory(TrackWithLevelsFactory)
    # Meta.ordering puts the track's levels in order, so first() is order=1.
    recommended_level = factory.LazyAttribute(lambda o: o.track.levels.first())
    reviewed_by = factory.SubFactory(LeadTeacherFactory)
    reviewed_at = factory.LazyFunction(dj_timezone.now)
