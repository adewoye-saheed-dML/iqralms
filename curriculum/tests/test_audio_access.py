"""Phase 6, scope 2 — placement audio is private, and reached only by a URL
that expires.

Acceptance criteria covered here: 5 (no placement response exposes a permanent
public object URL), 6 (an authorised lead can obtain a short-lived access URL)
and 7 (nobody can retrieve another student's sample through any placement
endpoint).

The access rule was put to the product owner rather than guessed (2026-08-26):
the lead teacher and the student whose voice it is, and nobody else. A
sub-teacher and a minor's linked parent are asserted *forbidden* for the same
reason Phase 2 asserts a sub-teacher cannot review — the decision is recorded as
a test, so widening it later has to be deliberate.

Both storage backends are exercised. The production one is an S3-compatible
bucket, and its presigned URLs are pure HMAC signing with no network call, so it
can be tested for real with fake credentials rather than mocked into agreeing.
"""

from urllib.parse import parse_qs, urlparse

from django.core.exceptions import SuspiciousOperation
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    MinorStudentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
)
from config.storage import PrivateLocalStorage, PrivateS3Storage
from curriculum.audio import (
    AUDIO_TOKEN_PARAM,
    PlacementAudioUnavailable,
    mint_audio_token,
    placement_audio_access,
)
from curriculum.models import PlacementResult

from .factories import (
    AUDIO_BYTES,
    BeginnerSkipPlacementFactory,
    PlacementResultFactory,
    TrackWithLevelsFactory,
)
from .test_models import audio_upload


def audio_url_endpoint(placement_or_pk):
    pk = getattr(placement_or_pk, "pk", placement_or_pk)
    return reverse("curriculum:placement-audio-url", args=[pk])


def download_endpoint(placement_or_pk):
    pk = getattr(placement_or_pk, "pk", placement_or_pk)
    return reverse("curriculum:placement-audio-download", args=[pk])


class WhoMayHearASampleTests(APITestCase):
    """Criteria 6 and 7 — the access rule, one test per party."""

    def setUp(self):
        self.placement = PlacementResultFactory()
        self.url = audio_url_endpoint(self.placement)

    def test_the_lead_teacher_gets_a_short_lived_url(self):
        """Criterion 6."""
        self.client.force_authenticate(user=LeadTeacherFactory())
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["url"])
        self.assertIsNotNone(response.data["expires_at"])
        # The expiry is the whole point, so it is asserted as a positive number
        # rather than merely present — a zero or absent TTL would be a URL that
        # never dies.
        self.assertGreater(response.data["expires_in"], 0)

    def test_the_owning_student_gets_a_url_for_their_own_sample(self):
        self.client.force_authenticate(user=self.placement.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["url"])

    def test_another_student_cannot_reach_it(self):
        """Criterion 7. A 404, not a 403 — a 403 would confirm the row exists."""
        self.client.force_authenticate(user=StudentFactory())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_sub_teacher_is_forbidden(self):
        """Not in the access rule. Recorded so that adding them is deliberate."""
        self.client.force_authenticate(user=SubTeacherFactory())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_parent_is_forbidden_even_for_their_own_child(self):
        """The option the product owner declined (2026-08-26).

        A guardian hearing their child's recording is a defensible product
        decision — but it is a *new* permission, and Phase 6 hardens what exists
        rather than widening who can reach student data.
        """
        minor = MinorStudentFactory()
        link = ParentLinkFactory(student=minor)
        placement = PlacementResultFactory(student=minor)

        self.client.force_authenticate(user=link.parent)
        response = self.client.get(audio_url_endpoint(placement))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_is_unauthorised(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_beginner_skip_has_nothing_to_hand_out(self):
        """No recording is a legitimate state, so a 404 and not a 500."""
        skipped = BeginnerSkipPlacementFactory()
        self.client.force_authenticate(user=LeadTeacherFactory())
        response = self.client.get(audio_url_endpoint(skipped))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_unknown_placement_is_a_404(self):
        self.client.force_authenticate(user=LeadTeacherFactory())
        response = self.client.get(audio_url_endpoint(9999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class NoPermanentPublicURLTests(APITestCase):
    """Criterion 5 — no placement response carries a permanent object URL.

    Asserted across *every* endpoint that serialises a placement rather than
    just the one that changed, because the field that used to leak the path was
    on the shared read serializer: removing it from one response and leaving it
    in another is exactly the mistake worth guarding.
    """

    def setUp(self):
        self.student = StudentFactory()
        self.track = TrackWithLevelsFactory()
        self.placement = PlacementResultFactory(student=self.student, track=self.track)

    def assert_no_object_url(self, payload):
        """No value in the payload may be a path into stored media."""
        rendered = str(payload)
        self.assertNotIn("/media/", rendered)
        self.assertNotIn("placements/", rendered)
        self.assertNotIn(self.placement.audio_sample.name, rendered)

    def test_the_students_own_list_carries_no_object_url(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(reverse("curriculum:placement-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data[0]["has_audio_sample"])
        self.assert_no_object_url(response.data)

    def test_the_leads_review_queue_carries_no_object_url(self):
        self.client.force_authenticate(user=LeadTeacherFactory())
        response = self.client.get(reverse("curriculum:placement-pending"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_no_object_url(response.data)

    def test_the_submit_response_carries_no_object_url(self):
        self.client.force_authenticate(user=StudentFactory())
        response = self.client.post(
            reverse("curriculum:placement-create"),
            {"track": self.track.id, "audio_sample": audio_upload("fresh.mp3")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("/media/", str(response.data))
        self.assertNotIn("placements/", str(response.data))

    def test_the_review_response_carries_no_object_url(self):
        lead = LeadTeacherFactory()
        self.client.force_authenticate(user=lead)
        response = self.client.post(
            reverse("curriculum:placement-review", args=[self.placement.pk]),
            {"recommended_level": self.track.levels.get(order=1).id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_no_object_url(response.data)

    def test_the_filename_is_published_without_its_storage_path(self):
        """A client needs a label; it does not need the bucket layout."""
        placement = PlacementResultFactory(audio_sample=audio_upload("surah-fatiha.mp3"))
        self.client.force_authenticate(user=placement.student)
        response = self.client.get(reverse("curriculum:placement-mine"))

        filename = response.data[0]["audio_filename"]
        self.assertIn("surah-fatiha", filename)
        self.assertNotIn("/", filename)


class LocalPrivateStorageTests(TestCase):
    """The development and test backend: private, and provably so."""

    def test_the_active_storage_has_no_public_urls(self):
        """The replacement for MEDIA_URL — asking for a URL is now an error.

        ``FileSystemStorage`` with an empty ``MEDIA_URL`` would return the bare
        stored path, which looks enough like a URL to end up in a response. This
        raising is what makes that impossible rather than merely discouraged.
        """
        placement = PlacementResultFactory()
        self.assertIsInstance(placement.audio_sample.storage, PrivateLocalStorage)
        with self.assertRaises(SuspiciousOperation):
            placement.audio_sample.url

    def test_it_does_not_claim_to_sign_its_own_urls(self):
        storage = PrivateLocalStorage()
        self.assertFalse(storage.provides_signed_urls)
        with self.assertRaises(NotImplementedError):
            storage.signed_url("placements/1/x.mp3", 300)

    def test_access_for_a_placement_with_no_audio_raises(self):
        with self.assertRaises(PlacementAudioUnavailable):
            placement_audio_access(BeginnerSkipPlacementFactory())


class PrivateBucketTests(TestCase):
    """The production backend, tested without a bucket.

    Presigning is HMAC over a canonical request — boto3 does it locally, with no
    call to S3 — so these assertions are about the real code path a deployment
    takes, not a stand-in for it.
    """

    def storage(self, **overrides):
        options = {
            "bucket_name": "quran-academy-test",
            "region_name": "us-east-1",
            "access_key": "AKIAIOSFODNN7EXAMPLE",
            "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "querystring_auth": True,
            "signature_version": "s3v4",
            "default_acl": None,
            "file_overwrite": False,
        }
        options.update(overrides)
        return PrivateS3Storage(**options)

    def test_the_bucket_backend_signs_its_own_urls(self):
        self.assertTrue(self.storage().provides_signed_urls)

    def test_a_minted_url_is_presigned_and_expiring(self):
        """Criterion 5 and 6 for the production path.

        The three query parameters asserted here are what make the URL both
        private and temporary: without a signature the object is unreachable,
        and the expiry is carried in the signature so it cannot be edited out.
        """
        url = self.storage().signed_url("placements/7/recitation.mp3", 300)
        query = parse_qs(urlparse(url).query)

        self.assertIn("X-Amz-Signature", query)
        self.assertIn("X-Amz-Credential", query)
        self.assertEqual(query["X-Amz-Expires"], ["300"])
        self.assertIn("placements/7/recitation.mp3", url)

    def test_the_ttl_the_caller_asks_for_is_the_ttl_in_the_url(self):
        """Per-call expiry, not just whatever the bucket was configured with."""
        url = self.storage(querystring_expire=3600).signed_url("placements/7/a.mp3", 60)
        self.assertEqual(parse_qs(urlparse(url).query)["X-Amz-Expires"], ["60"])

    def test_the_unsigned_url_form_is_not_what_the_application_uses(self):
        """A bucket configured without querystring auth would leak everything.

        Asserted as a contrast: with ``querystring_auth`` off, ``url()`` returns
        a plain object URL with no signature at all. That is the configuration
        Phase 6 forbids, and ``config/settings.py`` sets the flag that prevents
        it — this test is what makes the difference visible.
        """
        unsigned = self.storage(querystring_auth=False).url("placements/7/a.mp3")
        self.assertNotIn("X-Amz-Signature", unsigned)


class SignedURLRoundTripTests(APITestCase):
    """A minted URL actually works, and stops working for the right reasons.

    These run against local private storage, where the minted URL points at the
    token-gated view. Against a bucket the equivalent guarantees are the bucket's
    (see PrivateBucketTests), so what is tested here is the stand-in's own
    signing, expiry and scoping.
    """

    def setUp(self):
        self.student = StudentFactory()
        self.track = TrackWithLevelsFactory()
        self.placement = PlacementResultFactory(student=self.student, track=self.track)
        self.lead = LeadTeacherFactory()

    def mint(self, placement=None, user=None):
        """Ask the endpoint for a URL, as the lead unless told otherwise."""
        self.client.force_authenticate(user=user or self.lead)
        response = self.client.get(audio_url_endpoint(placement or self.placement))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["url"]

    def fetch(self, url):
        """Follow a minted URL the way a browser's audio element would.

        Deliberately unauthenticated: a presigned URL carries its own
        authorisation, and an ``<audio src>`` sends no Authorization header. If
        this needed credentials the mechanism would not work in a browser.
        """
        self.client.force_authenticate(user=None)
        return self.client.get(url)

    def test_a_minted_url_returns_the_audio(self):
        response = self.fetch(self.mint())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(response.streaming_content), AUDIO_BYTES)

    def test_the_url_is_not_the_storage_path(self):
        """It goes through the view, so the file is never served as static media."""
        url = self.mint()
        self.assertIn(download_endpoint(self.placement), url)
        self.assertIn(f"{AUDIO_TOKEN_PARAM}=", url)

    def test_no_token_is_a_404(self):
        response = self.fetch(download_endpoint(self.placement))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_tampered_token_is_a_404(self):
        url = self.mint()
        response = self.fetch(url[:-3] + "aaa")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_made_up_token_is_a_404(self):
        response = self.fetch(
            f"{download_endpoint(self.placement)}?{AUDIO_TOKEN_PARAM}=not-a-token"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_expired_token_is_a_404(self):
        """The property the whole mechanism exists for: URLs die.

        Expiry is forced by shortening the TTL the *reader* enforces rather than
        by waiting or freezing the clock, which keeps the test deterministic and
        still exercises the real signature check.
        """
        url = self.mint()
        with override_settings(PLACEMENT_AUDIO_URL_TTL_SECONDS=-1):
            response = self.fetch(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_one_students_token_cannot_be_pointed_at_another_placement(self):
        """Criterion 7, at the token layer rather than the endpoint layer.

        Editing the pk in the path must not work, because the pk is inside the
        signature. Without this check the download view would happily serve any
        placement to anyone holding any valid token.
        """
        someone_else = PlacementResultFactory()
        token = mint_audio_token(self.placement)

        response = self.fetch(
            f"{download_endpoint(someone_else)}?{AUDIO_TOKEN_PARAM}={token}"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_resubmitting_invalidates_a_token_already_issued(self):
        """A displaced recording's URLs stop working immediately, not at expiry.

        The stored object name is inside the signature, and re-submitting writes
        a new one — so the retention rule from Phase 2 (keep only the current
        sample) extends to the URLs handed out for the old one, with no
        revocation list to maintain.
        """
        url = self.mint()
        with self.captureOnCommitCallbacks(execute=True):
            PlacementResult.submit(
                student=self.student,
                track=self.track,
                audio_sample=audio_upload("replacement.mp3"),
            )

        response = self.fetch(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_student_can_play_back_their_own_sample_end_to_end(self):
        """The access rule's second party, all the way through to the bytes."""
        response = self.fetch(self.mint(user=self.student))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(response.streaming_content), AUDIO_BYTES)

    def test_the_download_route_refuses_when_the_bucket_signs_its_own_urls(self):
        """One way in per deployment.

        In production the minted URL points at the bucket, so this view is not
        part of the access path — and a route that still served files would be a
        second, application-side path to private objects that nothing else tests.
        """
        url = self.mint()
        with override_settings(
            STORAGES={
                "default": {
                    "BACKEND": "config.storage.PrivateS3Storage",
                    "OPTIONS": {
                        "bucket_name": "quran-academy-test",
                        "region_name": "us-east-1",
                        "access_key": "AKIAIOSFODNN7EXAMPLE",
                        "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                    },
                },
                "staticfiles": {
                    "BACKEND": (
                        "django.contrib.staticfiles.storage.StaticFilesStorage"
                    )
                },
            }
        ):
            response = self.fetch(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
