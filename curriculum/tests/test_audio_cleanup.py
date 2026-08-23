"""Placement audio cleanup — the retention rule from signals.py.

Product owner's call (2026-08-23): a placement keeps exactly one sample, so a
replaced or deleted recording must leave storage as well as the row.

Every test here drives ``captureOnCommitCallbacks(execute=True)``: cleanup is
deferred to ``transaction.on_commit`` so a rolled-back write cannot destroy a
file, and ``TestCase`` never commits, so the callbacks would otherwise be
captured and dropped. That is also the assertion that the deferral works — a
deletion done inline would pass without it.
"""

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase

from accounts.tests.factories import LeadTeacherFactory, StudentFactory
from curriculum.models import PlacementResult

from .factories import (
    PlacementResultFactory,
    ReviewedPlacementFactory,
    TrackWithLevelsFactory,
)
from .test_models import audio_upload


class PlacementAudioCleanupTests(TestCase):
    def setUp(self):
        self.student = StudentFactory()
        self.track = TrackWithLevelsFactory()

    def submit(self, **kwargs):
        """Submit inside a committing block so cleanup callbacks actually run."""
        with self.captureOnCommitCallbacks(execute=True):
            return PlacementResult.submit(
                student=self.student, track=self.track, **kwargs
            )

    def test_replacing_the_audio_deletes_the_displaced_file(self):
        first = self.submit(audio_sample=audio_upload("first.mp3"))
        displaced = first.audio_sample.name
        self.assertTrue(default_storage.exists(displaced))

        second = self.submit(audio_sample=audio_upload("second.mp3"))

        self.assertNotEqual(second.audio_sample.name, displaced)
        self.assertFalse(default_storage.exists(displaced))
        self.assertTrue(default_storage.exists(second.audio_sample.name))

    def test_resubmitting_as_a_beginner_deletes_the_audio(self):
        """The skip path clears the field, which displaces the file just as much."""
        first = self.submit(audio_sample=audio_upload())
        displaced = first.audio_sample.name

        skipped = self.submit(skipped_as_beginner=True)

        self.assertFalse(skipped.audio_sample)
        self.assertFalse(default_storage.exists(displaced))

    def test_a_review_leaves_the_audio_in_place(self):
        """Stamping a review does not touch audio, so nothing may be deleted."""
        placement = self.submit(audio_sample=audio_upload())
        name = placement.audio_sample.name

        with self.captureOnCommitCallbacks(execute=True):
            placement.review(
                recommended_level=self.track.levels.first(),
                reviewed_by=LeadTeacherFactory(),
            )

        self.assertTrue(default_storage.exists(name))
        placement.refresh_from_db()
        self.assertEqual(placement.audio_sample.name, name)

    def test_an_unrelated_field_edit_leaves_the_audio_in_place(self):
        placement = ReviewedPlacementFactory(student=self.student, track=self.track)
        name = placement.audio_sample.name

        with self.captureOnCommitCallbacks(execute=True):
            placement.save()

        self.assertTrue(default_storage.exists(name))

    def test_deleting_a_placement_deletes_its_audio(self):
        placement = self.submit(audio_sample=audio_upload())
        name = placement.audio_sample.name

        with self.captureOnCommitCallbacks(execute=True):
            placement.delete()

        self.assertFalse(default_storage.exists(name))

    def test_deleting_the_student_cascades_to_the_audio(self):
        """Registering the receivers takes the model off the fast-delete path."""
        placement = self.submit(audio_sample=audio_upload())
        name = placement.audio_sample.name

        with self.captureOnCommitCallbacks(execute=True):
            self.student.delete()

        self.assertFalse(PlacementResult.objects.exists())
        self.assertFalse(default_storage.exists(name))

    def test_queryset_delete_also_cleans_up(self):
        placement = self.submit(audio_sample=audio_upload())
        name = placement.audio_sample.name

        with self.captureOnCommitCallbacks(execute=True):
            PlacementResult.objects.all().delete()

        self.assertFalse(default_storage.exists(name))

    def test_deleting_a_beginner_skip_with_no_audio_is_harmless(self):
        skipped = self.submit(skipped_as_beginner=True)
        with self.captureOnCommitCallbacks(execute=True):
            skipped.delete()
        self.assertFalse(PlacementResult.objects.exists())

    def test_another_students_sample_is_untouched(self):
        """Cleanup is scoped to the row being written, not the whole directory."""
        mine = self.submit(audio_sample=audio_upload())
        theirs = PlacementResultFactory()
        theirs_name = theirs.audio_sample.name

        with self.captureOnCommitCallbacks(execute=True):
            mine.delete()

        self.assertFalse(default_storage.exists(mine.audio_sample.name))
        self.assertTrue(default_storage.exists(theirs_name))

    def test_a_rolled_back_replacement_keeps_the_original_file(self):
        """The reason cleanup is deferred: no commit, no deletion."""
        placement = self.submit(audio_sample=audio_upload("keeper.mp3"))
        name = placement.audio_sample.name

        # captureOnCommitCallbacks(execute=False) stands in for a rollback: the
        # write happened, the transaction never committed, so the callback that
        # would delete the displaced file must not have run.
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            PlacementResult.submit(
                student=self.student,
                track=self.track,
                audio_sample=audio_upload("replacement.mp3"),
            )

        self.assertTrue(callbacks)  # a deletion *was* queued
        self.assertTrue(default_storage.exists(name))

    def test_user_model_cascade_is_the_one_phase_one_configured(self):
        """Guards the cascade assumption above against a Phase 1 FK change."""
        field = PlacementResult._meta.get_field("student")
        self.assertIs(field.related_model, get_user_model())
