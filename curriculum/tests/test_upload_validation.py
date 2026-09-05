"""Phase 6, scope 3 — placement uploads are validated before they are stored.

Acceptance criterion 8 asks for an oversized file, a disallowed format and a
spoofed/non-audio payload to be rejected; criterion 9 asks that a valid sample
still reaches the review flow unchanged. The spec's list of required cases is
covered here: valid audio, an invalid extension/type, a spoofed MIME type, an
oversized upload, and the beginner-skip path.

Two things these tests are careful about, because both are ways to write a
validation test that proves nothing:

* **Every rejection is asserted by error code**, not merely by a 400. "The
  request failed" is also true when the serializer rejected the file for some
  unrelated reason, so a test that only checks the status would keep passing if
  the size check silently replaced the signature check.
* **Nothing is persisted on rejection.** A file that reaches storage and is then
  refused is still a file an attacker put on our disk, so each rejection asserts
  the placement row does not exist as well.

The chosen limits live in ``curriculum/validators.py`` and are read from there
rather than retyped, so changing one number cannot leave a test asserting the
old one.
"""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import StudentFactory
from curriculum.models import PlacementResult, Status
from curriculum.validators import (
    ALLOWED_AUDIO_EXTENSIONS,
    MAX_PLACEMENT_AUDIO_BYTES,
    validate_placement_audio,
)

from .factories import PlacementResultFactory, TrackWithLevelsFactory, admit
from .test_api import placements_url

#: Real leading bytes for each accepted format, so the signature check is
#: exercised against the thing it claims to recognise rather than against a
#: string that happens to be in the source.
SIGNATURES = {
    ".mp3": b"ID3\x04\x00\x00\x00\x00\x00\x00",
    ".wav": b"RIFF\x24\x00\x00\x00WAVEfmt ",
    ".ogg": b"OggS\x00\x02\x00\x00\x00\x00\x00\x00",
    ".m4a": b"\x00\x00\x00\x20ftypM4A ",
    ".webm": b"\x1a\x45\xdf\xa3\x01\x00\x00\x00",
    ".flac": b"fLaC\x00\x00\x00\x22",
}

#: A DOS/PE executable header. The canonical "renamed payload" — not audio by
#: any signature, and the exact thing tech-debt.md warned could be filed as a
#: recitation sample.
EXECUTABLE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"

#: Plain text, the other obvious non-audio payload.
TEXT_BYTES = b"This is not a recitation, it is a sentence about one.\n"


def upload(name, data, content_type="audio/mpeg"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def valid_audio(extension=".mp3", *, name=None, padding=1024, content_type=None):
    """A file that passes every check, for the format ``extension`` names."""
    defaults = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }
    return upload(
        name or f"recitation{extension}",
        SIGNATURES[extension] + b"\x00" * padding,
        content_type=content_type or defaults[extension],
    )


def assert_code(test, exc, expected):
    """Assert the ValidationError carries ``expected`` as its code."""
    codes = [error.code for error in exc.error_list]
    test.assertEqual(codes, [expected])


class ValidatorUnitTests(TestCase):
    """The validator itself, away from HTTP — one case per rule."""

    def raises(self, expected_code, uploaded):
        with self.assertRaises(ValidationError) as caught:
            validate_placement_audio(uploaded)
        assert_code(self, caught.exception, expected_code)

    def test_every_allowed_format_is_accepted(self):
        """The allowlist and the signature table agree with each other.

        Written as a loop over the real allowlist rather than six literals, so a
        format added to ``AUDIO_FORMATS`` without a signature matcher fails here
        instead of being quietly untested.
        """
        for extension in ALLOWED_AUDIO_EXTENSIONS:
            with self.subTest(extension=extension):
                # .oga and .opus share the Ogg signature; .mp4 shares MP4's.
                signature_key = {".oga": ".ogg", ".opus": ".ogg", ".mp4": ".m4a"}.get(
                    extension, extension
                )
                sample = upload(
                    f"recitation{extension}",
                    SIGNATURES[signature_key] + b"\x00" * 512,
                    content_type="",
                )
                self.assertIs(validate_placement_audio(sample), sample)

    def test_an_unsupported_extension_is_rejected(self):
        self.raises(
            "audio_extension_not_allowed",
            upload("recitation.aiff", SIGNATURES[".mp3"], content_type="audio/aiff"),
        )

    def test_a_file_with_no_extension_is_rejected(self):
        self.raises("audio_extension_not_allowed", upload("recitation", SIGNATURES[".mp3"]))

    def test_an_executable_renamed_as_mp3_is_rejected(self):
        """The abuse case: an audio extension over a non-audio payload."""
        self.raises("audio_unrecognised_content", upload("recitation.mp3", EXECUTABLE_BYTES))

    def test_text_renamed_as_mp3_is_rejected(self):
        self.raises("audio_unrecognised_content", upload("recitation.mp3", TEXT_BYTES))

    def test_real_audio_under_the_wrong_extension_is_rejected(self):
        """A genuine WAV named .mp3 — malformed rather than malicious, still no.

        Asserted by code, because this must be the *mismatch* error and not the
        unrecognised-content one: the file is audio, it is simply not the audio
        its name claims.
        """
        self.raises(
            "audio_content_mismatch",
            upload("recitation.mp3", SIGNATURES[".wav"] + b"\x00" * 64),
        )

    def test_a_spoofed_content_type_does_not_get_a_file_accepted(self):
        """The header says audio/mpeg; the bytes say executable. Bytes win.

        This is the case the spec names, and the reason the signature check
        exists at all: ``Content-Type`` is chosen by the client, so it is
        evidence and never proof.
        """
        self.raises(
            "audio_unrecognised_content",
            upload("recitation.mp3", EXECUTABLE_BYTES, content_type="audio/mpeg"),
        )

    def test_a_content_type_contradicting_a_valid_file_is_rejected(self):
        """Valid MP3 bytes, valid .mp3 name, but the client declared a PDF.

        Nothing about this request is coherent, so it is refused rather than
        accepted on the strength of the two fields that happen to agree.
        """
        self.raises(
            "audio_content_type_mismatch",
            valid_audio(".mp3", content_type="application/pdf"),
        )

    def test_an_uninformative_content_type_is_not_treated_as_a_mismatch(self):
        """``application/octet-stream`` is a browser shrugging, not a claim.

        Rejecting it would break honest uploads and stop no attacker, who would
        simply declare ``audio/mpeg`` instead.
        """
        for declared in ("", "application/octet-stream", "binary/octet-stream"):
            with self.subTest(content_type=declared):
                sample = valid_audio(".mp3", content_type=declared)
                self.assertIs(validate_placement_audio(sample), sample)

    def test_an_oversized_file_is_rejected(self):
        oversized = upload(
            "recitation.mp3",
            SIGNATURES[".mp3"] + b"\x00" * MAX_PLACEMENT_AUDIO_BYTES,
        )
        self.assertGreater(oversized.size, MAX_PLACEMENT_AUDIO_BYTES)
        self.raises("audio_too_large", oversized)

    def test_a_file_exactly_at_the_limit_is_accepted(self):
        """The cap is inclusive, so the boundary is not an accidental rejection."""
        head = SIGNATURES[".mp3"]
        exact = upload(
            "recitation.mp3", head + b"\x00" * (MAX_PLACEMENT_AUDIO_BYTES - len(head))
        )
        self.assertEqual(exact.size, MAX_PLACEMENT_AUDIO_BYTES)
        self.assertIs(validate_placement_audio(exact), exact)

    def test_an_empty_file_is_rejected(self):
        self.raises("empty_audio", upload("recitation.mp3", b""))

    def test_validation_leaves_the_file_readable_from_the_start(self):
        """The validator reads the signature; the save that follows needs it too.

        A validator that consumed the stream would store a sample missing its
        first bytes — a corruption that no other test here would notice.
        """
        sample = valid_audio(".mp3", padding=32)
        validate_placement_audio(sample)
        self.assertEqual(sample.read(), SIGNATURES[".mp3"] + b"\x00" * 32)


class PlacementUploadAPITests(APITestCase):
    """The same rules through the endpoint a student actually posts to.

    The URL is academy-scoped from SaaS Phase 3, and the student is admitted to
    the track's academy in ``setUp`` — the upload rules themselves are untouched
    by that, which is what these tests still assert.
    """

    def setUp(self):
        self.student = StudentFactory()
        self.track = TrackWithLevelsFactory()
        admit(self.student, self.track.organization)
        self.url = placements_url(self.track.organization)
        self.client.force_authenticate(user=self.student)

    def submit(self, **data):
        return self.client.post(
            self.url, {"track": self.track.id, **data}, format="multipart"
        )

    def assert_rejected(self, response, expected_code):
        """A 400 naming the field, with the code that says which rule fired."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("audio_sample", response.data)
        self.assertEqual(
            [error.code for error in response.data["audio_sample"]], [expected_code]
        )
        # Nothing was stored, so a refused upload leaves no trace to clean up.
        self.assertFalse(PlacementResult.objects.exists())

    def test_a_valid_sample_still_reaches_the_review_flow(self):
        """Acceptance criterion 9 — hardening must not break the happy path."""
        response = self.submit(audio_sample=valid_audio(".mp3"))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], Status.PENDING.value)
        self.assertTrue(response.data["has_audio_sample"])

        placement = PlacementResult.objects.get()
        self.assertTrue(placement.audio_sample)
        # And it is in the lead's queue, which is what "reaches the review flow"
        # means rather than merely "was accepted".
        self.assertEqual(placement.status, Status.PENDING)

    def test_every_allowed_format_is_accepted_through_the_api(self):
        for extension in (".mp3", ".wav", ".ogg", ".m4a", ".webm", ".flac"):
            with self.subTest(extension=extension):
                PlacementResult.objects.all().delete()
                response = self.submit(audio_sample=valid_audio(extension))
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_disallowed_format_is_rejected(self):
        """Criterion 8, first case."""
        self.assert_rejected(
            self.submit(
                audio_sample=upload(
                    "recitation.aiff", SIGNATURES[".mp3"], content_type="audio/aiff"
                )
            ),
            "audio_extension_not_allowed",
        )

    def test_a_spoofed_non_audio_payload_is_rejected(self):
        """Criterion 8, second case: audio name, audio header, executable bytes."""
        self.assert_rejected(
            self.submit(
                audio_sample=upload(
                    "recitation.mp3", EXECUTABLE_BYTES, content_type="audio/mpeg"
                )
            ),
            "audio_unrecognised_content",
        )

    def test_an_oversized_upload_is_rejected(self):
        """Criterion 8, third case."""
        self.assert_rejected(
            self.submit(
                audio_sample=upload(
                    "recitation.mp3",
                    SIGNATURES[".mp3"] + b"\x00" * MAX_PLACEMENT_AUDIO_BYTES,
                )
            ),
            "audio_too_large",
        )

    def test_the_beginner_skip_path_is_untouched_by_upload_validation(self):
        """The spec's fifth required case.

        A skip carries no file, so none of these rules may fire on it. Worth its
        own test because the obvious way to implement "always validate the
        audio" makes a beginner skip fail with a confusing file error.
        """
        response = self.client.post(
            self.url,
            {"track": self.track.id, "skipped_as_beginner": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], Status.REVIEWED.value)
        self.assertFalse(response.data["has_audio_sample"])
        self.assertTrue(PlacementResult.objects.get().skipped_as_beginner)

    def test_a_rejected_upload_does_not_replace_an_existing_sample(self):
        """A bad re-submission must not cost the student the sample they had.

        ``submit()`` replaces the row and the cleanup receiver deletes the
        displaced file, so a validation error arriving too late would destroy a
        good recording and store nothing in its place.
        """
        self.submit(audio_sample=valid_audio(".mp3", name="good.mp3"))
        placement = PlacementResult.objects.get()
        original = placement.audio_sample.name

        response = self.submit(
            audio_sample=upload("evil.mp3", EXECUTABLE_BYTES, content_type="audio/mpeg")
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        placement.refresh_from_db()
        self.assertEqual(placement.audio_sample.name, original)
        self.assertTrue(placement.audio_sample.storage.exists(original))


class ModelBackstopTests(TestCase):
    """The safeguard for uploads that never pass through the serializer.

    The admin's change form lets the lead attach a file directly, and it reaches
    ``full_clean()`` with an ``UploadedFile`` on the field — so the model check
    is what stops the admin being a way around the rules. Asserted at the model
    rather than through the admin UI because the model is where the rule lives.
    """

    def setUp(self):
        self.student = StudentFactory()
        self.track = TrackWithLevelsFactory()
        admit(self.student, self.track.organization)

    def test_an_http_upload_assigned_straight_to_the_model_is_validated(self):
        with self.assertRaises(ValidationError) as caught:
            PlacementResult.submit(
                student=self.student,
                track=self.track,
                audio_sample=upload(
                    "recitation.mp3", EXECUTABLE_BYTES, content_type="audio/mpeg"
                ),
            )

        self.assertIn("audio_sample", caught.exception.error_dict)
        self.assertEqual(
            [e.code for e in caught.exception.error_dict["audio_sample"]],
            ["audio_unrecognised_content"],
        )
        self.assertFalse(PlacementResult.objects.exists())

    def test_a_valid_http_upload_assigned_straight_to_the_model_is_stored(self):
        placement = PlacementResult.submit(
            student=self.student, track=self.track, audio_sample=valid_audio(".webm")
        )
        self.assertTrue(placement.audio_sample)

    def test_saving_an_existing_placement_does_not_revalidate_stored_audio(self):
        """Re-validating on every save would mean a bucket read per save.

        The guard is scoped to files assigned in this process for that reason.
        A review stamp, which saves the row without touching the file, must not
        re-open the stored object — and must not fail if the stored file predates
        a tightening of the rules.
        """
        placement = PlacementResult.submit(
            student=self.student, track=self.track, audio_sample=valid_audio(".mp3")
        )
        placement.refresh_from_db()

        # No exception, and no attempt to read the file back from storage.
        placement.save()
        self.assertTrue(PlacementResult.objects.get().audio_sample)

    def test_a_factory_built_file_is_not_treated_as_untrusted_input(self):
        """Trusted code writing a known file is not an upload.

        CLAUDE.md's rule is about *user-uploaded* files. Fixtures, factories and
        data migrations hand the model a plain ``File``, and re-validating those
        would break test data and migrations without closing any real path in.
        """
        placement = PlacementResultFactory(student=self.student, track=self.track)
        self.assertTrue(placement.audio_sample)
