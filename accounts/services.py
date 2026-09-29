"""Domain services for accounts, parent links, and minor student onboarding."""

import datetime
import logging
from django.db import transaction
from django.utils import timezone

from .models import ParentLink, PendingParentLink, Role, User
from notifications.services import (
    send_invitation_email,
    send_parent_guardian_notification_email,
)

logger = logging.getLogger(__name__)


def fulfill_pending_parent_links(parent_user: User) -> list[ParentLink]:
    """Find any pending parent links for this parent's email and create ParentLinks.

    Called whenever a parent account is created or registers.
    """
    if parent_user.role != Role.PARENT:
        return []

    pending_items = list(
        PendingParentLink.objects.filter(
            parent_email__iexact=parent_user.email
        ).select_related("student")
    )
    if not pending_items:
        return []

    created_links = []
    with transaction.atomic():
        for item in pending_items:
            if item.student.role == Role.STUDENT and item.student_id != parent_user.id:
                link, created = ParentLink.objects.get_or_create(
                    parent=parent_user,
                    student=item.student,
                )
                if created:
                    created_links.append(link)
        PendingParentLink.objects.filter(
            id__in=[item.id for item in pending_items]
        ).delete()

    return created_links


def process_minor_student_parent_email(
    student: User,
    parent_email: str,
    organization=None,
) -> dict:
    """Handle parent association and invitation for a minor student signup.

    If a parent account already exists for parent_email:
      - Immediately creates ParentLink.
      - Sends notification email to parent.
      - If organization is provided, ensures parent has OrganizationMembership.
    If no parent account exists:
      - Records PendingParentLink.
      - If organization is provided: creates OrganizationInvitation with role='parent'
        and sends invitation email.
      - If no organization: sends guardian notification email with instructions and signup code.
    """
    clean_email = parent_email.strip().lower()
    if not clean_email:
        return {"status": "none"}

    existing_parent = User.objects.filter(email__iexact=clean_email, role=Role.PARENT).first()

    if existing_parent and existing_parent.id != student.id:
        link, created = ParentLink.objects.get_or_create(
            parent=existing_parent,
            student=student,
        )
        send_parent_guardian_notification_email(
            student=student,
            parent_email=clean_email,
            is_existing_parent=True,
            signup_code=student.signup_code,
            organization=organization,
        )

        if organization:
            from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
            OrganizationMembership.objects.get_or_create(
                organization=organization,
                user=existing_parent,
                defaults={
                    "role": OrganizationRole.PARENT,
                    "status": MembershipStatus.ACTIVE,
                },
            )

        return {"status": "linked", "link": link, "parent": existing_parent}

    # Parent does not exist or does not yet have parent role
    pending_link, _ = PendingParentLink.objects.get_or_create(
        student=student,
        parent_email=clean_email,
    )

    if organization:
        from organizations.models import (
            InvitationStatus,
            OrganizationInvitation,
            OrganizationRole,
        )

        existing_invitation = OrganizationInvitation.objects.filter(
            organization=organization,
            email__iexact=clean_email,
            status=InvitationStatus.PENDING,
        ).first()

        if existing_invitation and existing_invitation.is_valid():
            raw_token = getattr(existing_invitation, "raw_token", "")
            if raw_token:
                send_invitation_email(
                    existing_invitation,
                    raw_token,
                    student=student,
                    signup_code=student.signup_code,
                )
        else:
            token, digest = OrganizationInvitation.generate_token_and_digest()
            invitation = OrganizationInvitation.objects.create(
                organization=organization,
                email=clean_email,
                role=OrganizationRole.PARENT,
                token_digest=digest,
                expires_at=timezone.now() + datetime.timedelta(days=7),
                status=InvitationStatus.PENDING,
            )
            invitation.raw_token = token
            send_invitation_email(
                invitation,
                token,
                student=student,
                signup_code=student.signup_code,
            )
    else:
        send_parent_guardian_notification_email(
            student=student,
            parent_email=clean_email,
            is_existing_parent=False,
            signup_code=student.signup_code,
            organization=None,
        )

    return {"status": "invited", "pending_link": pending_link}
