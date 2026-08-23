"""factory_boy factories for the curriculum app.

Per CLAUDE.md every model gets a factory here before tests are written against
it. Two factories deliberately derive values rather than hardcoding them, so a
factory can never assert a state the model would reject:

* ``LevelFactory.order`` asks the model for the next legal order — ``Level``
  rejects gaps, so a hardcoded order would break the moment a track had two
  levels.
* ``BeginnerSkipPlacementFactory`` builds its track *with* levels, because a
  beginner skip resolves the track's ``order=1`` level at save time.
"""

import factory
from django.utils import timezone as dj_timezone

from accounts.tests.factories import LeadTeacherFactory, StudentFactory
from curriculum.models import Level, PlacementResult, Track

#: Stands in for a recitation recording. The bytes are never inspected — only
#: the presence of a file matters to the audio-or-skip invariant.
AUDIO_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00fake-recitation-sample"

#: How many levels TrackWithLevelsFactory builds when not told otherwise.
DEFAULT_LEVEL_COUNT = 3


class TrackFactory(factory.django.DjangoModelFactory):
    """A track with no levels yet."""

    class Meta:
        model = Track

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
