"""API tests for the pricing app.

Acceptance criteria from specs/phase-5-pricing-waitlist.md covered here:

* **2** — a sub-teacher cannot create a ``PricingAgreement``, mirroring the
  placement-review permission test.
* **3** — a second agreement for the same student and level deactivates the first
  rather than deleting it, both rows still queryable, asserted over HTTP as well
  as at the model layer (test_models.py).

Plus the thing the spec is emphatic about but does not number: ``notes`` is the
lead's private reasoning and the family never sees it.
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
    TeacherProfileFactory,
)
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import OrganizationFactory
from pricing.models import PricingAgreement, PricingReason

from .factories import (
    HARDSHIP_RATE,
    PREMIUM_RATE,
    STANDARD_RATE,
    PremiumAgreementFactory,
    PricingAgreementFactory,
)


class PricingWorld(APITestCase):
    """Shared world: an academy, a lead, a sub-teacher, a student, and a level."""

    def setUp(self):
        self.organization = OrganizationFactory(name="Al-Furqan Academy")
        self.track = TrackFactory(
            organization=self.organization, name="Hifz", slug="hifz"
        )
        self.level = LevelFactory(track=self.track)
        self.lead = LeadTeacherFactory()
        self.sub = SubTeacherFactory()
        TeacherProfileFactory(user=self.sub, approved=True)
        self.student = StudentFactory()

        # Active memberships in the academy
        self.lead_membership = admit(
            self.lead, self.organization, role=OrganizationRole.OWNER
        )
        self.sub_membership = admit(
            self.sub, self.organization, role=OrganizationRole.TEACHER
        )
        self.student_membership = admit(
            self.student, self.organization, role=OrganizationRole.STAFF
        )

    @property
    def agreements_url(self):
        return reverse(
            "pricing:agreements",
            kwargs={"organization_pk": self.organization.pk},
        )

    @property
    def my_agreements_url(self):
        return reverse(
            "pricing:agreement-mine",
            kwargs={"organization_pk": self.organization.pk},
        )

    def payload(self, **overrides):
        body = {
            "student": self.student.pk,
            "level": self.level.pk,
            "standard_rate": str(STANDARD_RATE),
            "agreed_rate": str(HARDSHIP_RATE),
            "reason": PricingReason.DISCOUNT_HARDSHIP,
            "notes": "Agreed after their father lost work. Revisit in six months.",
        }
        body.update(overrides)
        return body

    def post_agreement(self, user=None, organization=None, **overrides):
        org = organization or self.organization
        url = reverse(
            "pricing:agreements", kwargs={"organization_pk": org.pk}
        )
        if user is not False:
            self.client.force_authenticate(user=user or self.lead)
        return self.client.post(url, self.payload(**overrides))



class PricingAgreementCreateAPITests(PricingWorld):
    """``POST /agreements/`` — the lead records a rate."""

    def test_the_lead_creates_an_agreement(self):
        response = self.post_agreement()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["student"]["id"], self.student.pk)
        self.assertEqual(response.data["level"]["id"], self.level.pk)
        self.assertEqual(Decimal(response.data["agreed_rate"]), HARDSHIP_RATE)
        self.assertEqual(Decimal(response.data["standard_rate"]), STANDARD_RATE)
        self.assertEqual(response.data["reason"], PricingReason.DISCOUNT_HARDSHIP)
        self.assertTrue(response.data["active"])
        self.assertTrue(PricingAgreement.objects.filter(pk=response.data["id"]).exists())

    def test_the_approver_is_the_requesting_lead_not_a_client_field(self):
        """Stamped from the request, like ``PlacementResult.reviewed_by``."""
        response = self.post_agreement(approved_by=self.sub.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["approved_by"], self.lead.username)
        self.assertEqual(
            PricingAgreement.objects.get(pk=response.data["id"]).approved_by, self.lead
        )

    def test_a_client_cannot_create_an_inactive_agreement(self):
        """``active`` is the model's to manage, not a flag a caller sets."""
        response = self.post_agreement(active=False)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["active"])

    def test_notes_are_optional(self):
        body = self.payload()
        del body["notes"]
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(self.agreements_url, body)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["notes"], "")

    def test_an_agreement_at_the_standard_rate_is_recordable(self):
        """"We discussed it and agreed no change" is an outcome worth storing."""
        response = self.post_agreement(
            agreed_rate=str(STANDARD_RATE), reason=PricingReason.STANDARD
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["reason"], PricingReason.STANDARD)

    def test_the_reason_display_is_published(self):
        """So a frontend shows "Discount — financial hardship", not a slug."""
        response = self.post_agreement()
        self.assertEqual(
            response.data["reason_display"], "Discount — financial hardship"
        )

    def test_an_unknown_reason_is_a_400(self):
        response = self.post_agreement(reason="mates_rates")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("reason", response.data)

    def test_a_negative_rate_is_a_400(self):
        response = self.post_agreement(agreed_rate="-5.00")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("agreed_rate", response.data)
        self.assertFalse(PricingAgreement.objects.exists())

    def test_an_unknown_level_is_a_400(self):
        response = self.post_agreement(level=999999)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

    def test_a_non_student_target_is_a_400(self):
        """The queryset is filtered to students, so a parent id is not found."""
        parent = ParentFactory()
        admit(parent, self.organization, role=OrganizationRole.STAFF)
        response = self.post_agreement(student=parent.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(PricingAgreement.objects.exists())



class PricingAgreementPermissionTests(PricingWorld):
    """Acceptance criterion 2 — lead only, no sub-teacher path at all."""

    def test_a_sub_teacher_cannot_create_an_agreement(self):
        """Acceptance criterion 2, mirroring the placement-review test."""
        response = self.post_agreement(user=self.sub)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(PricingAgreement.objects.exists())

    def test_an_approved_sub_teacher_still_cannot(self):
        """Approval makes a teacher bookable, not a rate-setter.

        The same distinction learnings.md records for placement review: a sub with
        ``TeacherProfile.approved=True`` gets a 403 all the same, because the gate
        is the role and not the approval.
        """
        self.sub.teacher_profile.approved = True
        self.sub.teacher_profile.save()

        response = self.post_agreement(user=self.sub)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_cannot_set_their_own_rate(self):
        response = self.post_agreement(user=self.student)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(PricingAgreement.objects.exists())

    def test_a_parent_cannot_set_their_childs_rate(self):
        link = ParentLinkFactory()
        response = self.post_agreement(user=link.parent, student=link.student.pk)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(PricingAgreement.objects.exists())

    def test_an_anonymous_caller_cannot_create_an_agreement(self):
        response = self.client.post(self.agreements_url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(PricingAgreement.objects.exists())

    def test_a_sub_teacher_cannot_read_a_students_pricing_history(self):
        """Reading is gated too — a sub has no business seeing family rates."""
        PricingAgreementFactory(student=self.student, level=self.level)
        self.client.force_authenticate(user=self.sub)
        response = self.client.get(self.agreements_url, {"student_id": self.student.pk})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SupersedeAPITests(PricingWorld):
    """Acceptance criterion 3, over HTTP."""

    def test_a_second_agreement_deactivates_the_first(self):
        """Acceptance criterion 3."""
        first = self.post_agreement()
        second = self.post_agreement(
            agreed_rate=str(PREMIUM_RATE), reason=PricingReason.PREMIUM_DIRECT
        )

        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertFalse(PricingAgreement.objects.get(pk=first.data["id"]).active)
        self.assertTrue(PricingAgreement.objects.get(pk=second.data["id"]).active)

    def test_both_rows_still_exist(self):
        """The other half of criterion 3: deactivated, not deleted."""
        first = self.post_agreement()
        second = self.post_agreement(agreed_rate=str(PREMIUM_RATE))

        rows = PricingAgreement.objects.filter(
            student=self.student, level=self.level
        )
        self.assertEqual(rows.count(), 2)
        self.assertEqual(
            {row.pk for row in rows}, {first.data["id"], second.data["id"]}
        )

    def test_the_history_endpoint_returns_active_and_superseded(self):
        self.post_agreement()
        self.post_agreement(agreed_rate=str(PREMIUM_RATE))

        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.agreements_url, {"student_id": self.student.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            [row["active"] for row in response.data],
            [True, False],
            "newest first, so the live agreement leads",
        )

    def test_a_replacement_for_another_level_does_not_supersede(self):
        other = LevelFactory(
            track=TrackFactory(
                organization=self.organization, name="Hifz 2", slug="hifz-x"
            )
        )
        first = self.post_agreement()
        self.post_agreement(level=other.pk)

        self.assertTrue(PricingAgreement.objects.get(pk=first.data["id"]).active)


class PricingHistoryAPITests(PricingWorld):
    """``GET /agreements/?student_id=`` — the lead's view of one family."""

    def get(self, user=None, **params):
        self.client.force_authenticate(user=user or self.lead)
        return self.client.get(self.agreements_url, params)

    def test_the_lead_reads_a_students_history(self):
        agreement = PricingAgreementFactory(student=self.student, level=self.level)
        response = self.get(student_id=self.student.pk)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [agreement.pk])
        self.assertEqual(response.data[0]["track"], self.level.track.slug)

    def test_the_history_includes_the_private_note(self):
        """The lead's own view holds their own reasoning."""
        PricingAgreementFactory(student=self.student, level=self.level)
        response = self.get(student_id=self.student.pk)
        self.assertTrue(response.data[0]["notes"])

    def test_another_students_agreements_are_not_listed(self):
        mine = PricingAgreementFactory(student=self.student, level=self.level)
        PricingAgreementFactory()

        response = self.get(student_id=self.student.pk)
        self.assertEqual([row["id"] for row in response.data], [mine.pk])

    def test_student_id_is_required(self):
        response = self.get()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student_id", response.data)

    def test_a_non_numeric_student_id_is_a_400(self):
        response = self.get(student_id="abc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student_id", response.data)

    def test_an_unknown_student_is_an_empty_list_not_a_404(self):
        response = self.get(student_id=999999)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])


class MyPricingAPITests(PricingWorld):
    """``GET /agreements/mine/`` — what the family sees, and what it does not."""

    def get(self, user=None, organization=None):
        org = organization or self.organization
        url = reverse(
            "pricing:agreement-mine",
            kwargs={"organization_pk": org.pk},
        )
        if user is not False:
            self.client.force_authenticate(user=user or self.student)
        return self.client.get(url)

    def test_a_student_sees_their_active_agreement(self):
        agreement = PricingAgreementFactory(student=self.student, level=self.level)
        response = self.get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [agreement.pk])
        self.assertEqual(Decimal(response.data[0]["agreed_rate"]), HARDSHIP_RATE)

    def test_the_private_note_is_never_published_to_the_family(self):
        """The spec: notes are "the lead's private reasoning, not the family's business"."""
        PricingAgreementFactory(student=self.student, level=self.level)
        payload = self.get().data[0]

        self.assertNotIn("notes", payload)
        self.assertNotIn("approved_by", payload)

    def test_superseded_agreements_are_not_shown_to_the_family(self):
        """The spec: their active agreement per level, not the full history."""
        PricingAgreementFactory(student=self.student, level=self.level)
        current = PremiumAgreementFactory(student=self.student, level=self.level)

        response = self.get()
        self.assertEqual([row["id"] for row in response.data], [current.pk])

    def test_one_row_per_level(self):
        other = LevelFactory(
            track=TrackFactory(
                organization=self.organization, name="Arabic", slug="arabic-x"
            )
        )
        PricingAgreementFactory(student=self.student, level=self.level)
        PricingAgreementFactory(student=self.student, level=other)

        response = self.get()
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            {row["level"]["id"] for row in response.data}, {self.level.pk, other.pk}
        )

    def test_another_students_agreement_is_not_visible(self):
        PricingAgreementFactory()
        self.assertEqual(list(self.get().data), [])

    def test_a_parent_cannot_read_their_childs_rate_here(self):
        """Narrower than booking on purpose — see permissions.py and tech-debt.md."""
        link = ParentLinkFactory()
        admit(link.parent, self.organization, role=OrganizationRole.STAFF)
        PricingAgreementFactory(student=link.student, level=self.level)

        response = self.get(user=link.parent)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_teacher_has_no_agreements_of_their_own(self):
        for user in (self.lead, self.sub):
            with self.subTest(role=user.role):
                self.assertEqual(self.get(user=user).status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_is_refused(self):
        response = self.get(user=False)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PricingTenancyAPITests(PricingWorld):
    """Multi-academy boundary and tenant isolation tests for the API surface."""

    def setUp(self):
        super().setUp()
        self.other_org = OrganizationFactory(name="Dar Al-Quran")
        self.other_lead = LeadTeacherFactory()
        self.other_student = StudentFactory()
        self.other_track = TrackFactory(
            organization=self.other_org, name="Hifz", slug="other-hifz"
        )
        self.other_level = LevelFactory(track=self.other_track)

        admit(self.other_lead, self.other_org, role=OrganizationRole.OWNER)
        admit(self.other_student, self.other_org, role=OrganizationRole.STAFF)

    def test_post_agreement_with_foreign_level_fails(self):
        """Cannot create an agreement using a level belonging to another academy."""
        response = self.post_agreement(level=self.other_level.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)
        self.assertFalse(PricingAgreement.objects.filter(level=self.other_level).exists())

    def test_post_agreement_with_foreign_student_fails(self):
        """Cannot create an agreement for a student not enrolled in this academy."""
        response = self.post_agreement(student=self.other_student.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(PricingAgreement.objects.filter(student=self.other_student).exists())

    def test_cross_academy_lead_cannot_create_agreement(self):
        """A lead from another academy cannot post agreements to this academy."""
        response = self.post_agreement(user=self.other_lead)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(PricingAgreement.objects.exists())

    def test_cross_academy_lead_cannot_view_agreements(self):
        """A lead from another academy cannot view agreements in this academy."""
        self.client.force_authenticate(user=self.other_lead)
        response = self.client.get(
            self.agreements_url, {"student_id": self.student.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_suspended_lead_cannot_access_agreements(self):
        """A lead with a suspended membership cannot manage agreements."""
        self.lead_membership.status = MembershipStatus.SUSPENDED
        self.lead_membership.save()

        response = self.post_agreement(user=self.lead)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.lead)
        get_response = self.client.get(
            self.agreements_url, {"student_id": self.student.pk}
        )
        self.assertEqual(get_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_academy_student_cannot_view_mine(self):
        """A student from another academy cannot view mine under this academy."""
        self.client.force_authenticate(user=self.other_student)
        response = self.client.get(self.my_agreements_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_suspended_student_cannot_view_mine(self):
        """A suspended student cannot view their agreements in this academy."""
        self.student_membership.status = MembershipStatus.SUSPENDED
        self.student_membership.save()

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.my_agreements_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_retired_unscoped_routes_return_404(self):
        """Legacy unscoped routes are completely removed to prevent bypass."""
        self.client.force_authenticate(user=self.lead)
        self.assertEqual(
            self.client.get("/api/pricing/agreements/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.post("/api/pricing/agreements/", {}).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.client.force_authenticate(user=self.student)
        self.assertEqual(
            self.client.get("/api/pricing/agreements/mine/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_multi_academy_student_mine_scoped_to_organization(self):
        """A student belonging to multiple academies only sees agreements for the requested academy."""
        admit(self.student, self.other_org, role=OrganizationRole.STAFF)
        agreement_1 = PricingAgreementFactory(student=self.student, level=self.level)
        agreement_2 = PricingAgreementFactory(
            student=self.student, level=self.other_level
        )

        self.client.force_authenticate(user=self.student)
        res_1 = self.client.get(self.my_agreements_url)
        self.assertEqual(res_1.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in res_1.data], [agreement_1.pk])

        other_mine_url = reverse(
            "pricing:agreement-mine",
            kwargs={"organization_pk": self.other_org.pk},
        )
        res_2 = self.client.get(other_mine_url)
        self.assertEqual(res_2.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in res_2.data], [agreement_2.pk])

    def test_student_history_scoped_to_organization(self):
        """The lead's student history lookup only includes agreements within their academy."""
        admit(self.student, self.other_org, role=OrganizationRole.STAFF)
        agreement_1 = PricingAgreementFactory(student=self.student, level=self.level)
        PricingAgreementFactory(student=self.student, level=self.other_level)

        self.client.force_authenticate(user=self.lead)
        response = self.client.get(
            self.agreements_url, {"student_id": self.student.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [agreement_1.pk])

