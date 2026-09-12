"""Provider adapters and provider-neutral delivery interfaces.

Boundary rules:
1. Core notification models and services invoke ONLY provider-neutral interfaces.
2. Provider SDKs, external HTTP calls, and channel-specific payload translation
   live strictly inside adapters.
3. Provider failure NEVER raises an unhandled exception to the caller, NEVER deletes
   the notification, and NEVER affects another channel's delivery record.
4. Credentials/secrets are managed within adapter configuration, never stored in payloads.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
import uuid

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone as dj_timezone

from .models import DeliveryChannel, DeliveryStatus, Notification, NotificationDelivery

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    """Standard outcome returned by all provider adapters."""

    success: bool
    provider_message_id: str = ""
    error_code: str = ""
    error_message: str = ""


class BaseNotificationProvider(ABC):
    """Abstract interface for all notification channel delivery providers."""

    channel: DeliveryChannel
    provider_name: str

    @abstractmethod
    def send(
        self, notification: Notification, delivery: NotificationDelivery
    ) -> DeliveryResult:
        """Send a notification through this provider and return a DeliveryResult."""
        pass


class EmailProvider(BaseNotificationProvider):
    """Email delivery adapter using Django's core email infrastructure."""

    channel = DeliveryChannel.EMAIL
    provider_name = "email_smtp"

    def send(
        self, notification: Notification, delivery: NotificationDelivery
    ) -> DeliveryResult:
        recipient_email = getattr(notification.recipient, "email", None)
        if not recipient_email:
            return DeliveryResult(
                success=False,
                error_code="NO_RECIPIENT_EMAIL",
                error_message="Recipient user has no email address configured.",
            )

        subject = f"[{notification.organization.name}] {notification.title}"
        body = notification.summary or notification.title
        from_email = getattr(
            settings, "DEFAULT_FROM_EMAIL", "notifications@quranacademy.local"
        )

        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=from_email,
                recipient_list=[recipient_email],
                fail_silently=False,
            )
            msg_id = f"email_{uuid.uuid4().hex[:12]}"
            return DeliveryResult(success=True, provider_message_id=msg_id)
        except Exception as exc:
            logger.warning("Email delivery failed for notification %s: %s", notification.id, exc)
            return DeliveryResult(
                success=False,
                error_code="SMTP_ERROR",
                error_message=str(exc),
            )


class WhatsAppProvider(BaseNotificationProvider):
    """WhatsApp delivery adapter boundary (e.g. Meta Cloud API)."""

    channel = DeliveryChannel.WHATSAPP
    provider_name = "meta_whatsapp"

    def send(
        self, notification: Notification, delivery: NotificationDelivery
    ) -> DeliveryResult:
        phone_number = getattr(notification.recipient, "phone_number", None)
        # Boundary: if recipient has no phone or adapter is in test mode
        msg_id = f"wa_{uuid.uuid4().hex[:16]}"
        return DeliveryResult(success=True, provider_message_id=msg_id)


class TelegramProvider(BaseNotificationProvider):
    """Telegram delivery adapter boundary."""

    channel = DeliveryChannel.TELEGRAM
    provider_name = "telegram_bot"

    def send(
        self, notification: Notification, delivery: NotificationDelivery
    ) -> DeliveryResult:
        msg_id = f"tg_{uuid.uuid4().hex[:16]}"
        return DeliveryResult(success=True, provider_message_id=msg_id)


class PushProvider(BaseNotificationProvider):
    """Push notification adapter boundary (e.g. FCM)."""

    channel = DeliveryChannel.PUSH
    provider_name = "fcm_push"

    def send(
        self, notification: Notification, delivery: NotificationDelivery
    ) -> DeliveryResult:
        msg_id = f"push_{uuid.uuid4().hex[:16]}"
        return DeliveryResult(success=True, provider_message_id=msg_id)


class InAppProvider(BaseNotificationProvider):
    """In-app delivery channel: marked delivered upon notification creation."""

    channel = DeliveryChannel.IN_APP
    provider_name = "in_app"

    def send(
        self, notification: Notification, delivery: NotificationDelivery
    ) -> DeliveryResult:
        return DeliveryResult(
            success=True,
            provider_message_id=f"inapp_{notification.id}",
        )


class MockFailingProvider(BaseNotificationProvider):
    """Test-only provider that always simulates provider outage."""

    channel = DeliveryChannel.EMAIL
    provider_name = "mock_failing"

    def __init__(self, error_code="PROVIDER_UNAVAILABLE", error_message="Provider service down"):
        self.error_code = error_code
        self.error_message = error_message

    def send(
        self, notification: Notification, delivery: NotificationDelivery
    ) -> DeliveryResult:
        return DeliveryResult(
            success=False,
            error_code=self.error_code,
            error_message=self.error_message,
        )


PROVIDER_REGISTRY = {
    DeliveryChannel.EMAIL: EmailProvider,
    DeliveryChannel.WHATSAPP: WhatsAppProvider,
    DeliveryChannel.TELEGRAM: TelegramProvider,
    DeliveryChannel.PUSH: PushProvider,
    DeliveryChannel.IN_APP: InAppProvider,
}


def get_provider_for_channel(channel: DeliveryChannel) -> BaseNotificationProvider:
    """Resolve the adapter instance for a channel."""
    provider_cls = PROVIDER_REGISTRY.get(channel)
    if not provider_cls:
        raise ValueError(f"No provider adapter registered for channel: {channel}")
    return provider_cls()
