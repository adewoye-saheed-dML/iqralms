import json
from django.db import transaction
from django.db.models import Model
from organizations.models import active_membership

from .models import AuditLog

# Keys that must never be stored in metadata
SENSITIVE_KEYS = {
    "password",
    "access_token",
    "refresh_token",
    "api_key",
    "secret_key",
    "authorization",
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
        return "none", ""
    if isinstance(target, Model):
        return target._meta.model_name, str(target.pk)
    if isinstance(target, type) and issubclass(target, Model):
        return target._meta.model_name, ""
    return str(type(target).__name__).lower(), str(target)

@transaction.atomic
def record_event(organization, actor, action: str, target, metadata: dict = None, request_id: str = ""):
    """
    Record an audit event securely.
    """
    # Verify actor is a member of the organization, unless it's a system action (actor is None)
    if actor is not None:
        membership = active_membership(user=actor, organization=organization)
        if not membership:
            raise ValueError("Actor is not an active member of the organization")

    # Reject cross-tenant targets if the target has an organization field
    if hasattr(target, 'organization') and target.organization is not None:
        if target.organization.pk != organization.pk:
            raise ValueError("Cross-academy target relationships are rejected")
    elif hasattr(target, 'organization_id') and target.organization_id is not None:
        if target.organization_id != organization.pk:
            raise ValueError("Cross-academy target relationships are rejected")

    object_type, object_id = get_object_info(target)
    
    clean_metadata = sanitize_metadata(metadata or {})

    return AuditLog.objects.create(
        organization=organization,
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=object_id,
        metadata=clean_metadata,
        request_id=request_id
    )
