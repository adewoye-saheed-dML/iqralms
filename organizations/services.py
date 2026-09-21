"""Domain services for organizations and invitations."""

from django.db import transaction
from django.utils import timezone

from audit_logs.models import AuditAction
from audit_logs.services import record_event
from .models import InvitationStatus, MembershipStatus, OrganizationInvitation, OrganizationMembership


def consume_invitation(*, invitation: OrganizationInvitation, user) -> OrganizationMembership:
    """Consume an invitation by creating an active membership, marking the invitation accepted, and recording an audit event."""
    membership = OrganizationMembership.objects.create(
        organization=invitation.organization,
        user=user,
        role=invitation.role,
        status=MembershipStatus.ACTIVE,
    )

    invitation.status = InvitationStatus.ACCEPTED
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_at"])

    record_event(
        organization=invitation.organization,
        actor=user,
        action=AuditAction.INVITATION_ACCEPTED,
        target=invitation,
        metadata={
            "email": invitation.email,
            "role": invitation.role,
            "membership_id": membership.id,
        },
    )

    return membership
