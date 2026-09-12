"""factory_boy factories for the notifications domain.

Factories build valid models:
- Recipient is automatically an active member of the notification's academy.
- Deliveries are tied to valid notifications.
"""

import factory
from accounts.tests.factories import UserFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory

from notifications.models import (
    DeliveryChannel,
    DeliveryStatus,
    EventType,
    Notification,
    NotificationDelivery,
)


class NotificationFactory(factory.django.DjangoModelFactory):
    """A valid tenant-scoped notification with an active recipient membership."""

    class Meta:
        model = Notification

    organization = factory.SubFactory(OrganizationFactory)
    recipient = factory.SubFactory(UserFactory)
    event_type = EventType.BOOKING_CONFIRMED
    title = "Session Confirmed"
    summary = "Your session has been confirmed."
    payload = factory.LazyFunction(dict)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        org = kwargs.get("organization")
        recipient = kwargs.get("recipient")
        if org and recipient:
            # Ensure the recipient is an active member in this academy so clean() passes
            OrganizationMembership.objects.get_or_create(
                organization=org,
                user=recipient,
                defaults={
                    "role": OrganizationRole.TEACHER if getattr(recipient, "is_teacher", False) else OrganizationRole.STAFF,
                    "status": MembershipStatus.ACTIVE,
                },
            )
        return super()._create(model_class, *args, **kwargs)


class NotificationDeliveryFactory(factory.django.DjangoModelFactory):
    """A valid delivery attempt tied to a notification."""

    class Meta:
        model = NotificationDelivery

    notification = factory.SubFactory(NotificationFactory)
    channel = DeliveryChannel.EMAIL
    provider = "email_smtp"
    status = DeliveryStatus.SENT
    attempt_count = 1
    provider_message_id = factory.Sequence(lambda n: f"msg_{n}")
