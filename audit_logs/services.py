import json
from django.db import transaction
from django.db.models import Model
from organizations.models import active_membership
from .middleware import get_current_request_id

from .models import AuditLog, AuditAction, ActorType

# Keys that must never be stored in metadata
SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "new_password",
    "old_password",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "secret",
    "secret_key",
    "client_secret",
    "private_key",
    "credential",
    "credentials",
    "authorization",
    "authorization_header",
    "cookie",
    "session",
    "session_key",
    "set_cookie",
}

def sanitize_metadata(data):
    """Recursively remove sensitive keys from metadata."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if str(k).lower() in SENSITIVE_KEYS:
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_metadata(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_metadata(item) for item in data]
    else:
        return data

def get_object_info(target):
    if target is None:
        return "", ""
    if isinstance(target, Model):
        return target._meta.model_name, str(target.pk)
    if isinstance(target, type) and issubclass(target, Model):
        return target._meta.model_name, ""
    return str(type(target).__name__).lower(), str(target)

@transaction.atomic
def record_event(*, organization, actor=None, action: str, target=None, metadata: dict = None, request_id: str = None):
    """
    Record an audit event securely.
    """
    # Validate action
    if action not in AuditAction.values:
        raise ValueError(f"Unknown audit action: {action}")

    # Verify actor is a member of the organization, unless it's a system action (actor is None)
    actor_type = ActorType.SYSTEM
    actor_id_snapshot = ""
    if actor is not None:
        membership = active_membership(user=actor, organization=organization)
        if not membership:
            raise ValueError("Actor is not an active member of the organization")
        actor_type = ActorType.USER
        actor_id_snapshot = str(actor.pk)

    # Reject cross-tenant targets if the target has an organization field
    if hasattr(target, 'organization') and target.organization is not None:
        if target.organization.pk != organization.pk:
            raise ValueError("Cross-academy target relationships are rejected")
    elif hasattr(target, 'organization_id') and target.organization_id is not None:
        if target.organization_id != organization.pk:
            raise ValueError("Cross-academy target relationships are rejected")

    object_type, object_id = get_object_info(target)
    
    clean_metadata = sanitize_metadata(metadata or {})
    
    if request_id is None:
        request_id = get_current_request_id() or ""

    return AuditLog.objects.create(
        organization=organization,
        actor=actor,
        actor_type=actor_type,
        actor_id_snapshot=actor_id_snapshot,
        action=action,
        object_type=object_type,
        object_id=object_id,
        metadata=clean_metadata,
        request_id=request_id
    )

