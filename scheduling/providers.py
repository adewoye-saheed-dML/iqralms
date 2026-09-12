import uuid
from dataclasses import dataclass
from django.conf import settings

@dataclass
class MeetingResult:
    provider: str
    provider_meeting_id: str
    join_url: str

class MeetingProvider:
    def create_meeting(self, identity: str, **kwargs) -> MeetingResult:
        raise NotImplementedError

class JitsiProvider(MeetingProvider):
    def create_meeting(self, identity: str, **kwargs) -> MeetingResult:
        import hashlib
        # Jitsi room names exist just by making the URL
        # We hash the identity to make it idempotent and stable for the same session.
        hash_hex = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
        room_name = f"quranacademy-{hash_hex}"
        join_url = f"https://{settings.JITSI_DOMAIN}/{room_name}"
        return MeetingResult(
            provider="jitsi",
            provider_meeting_id=room_name,
            join_url=join_url
        )

def get_provider(provider_name: str) -> MeetingProvider:
    if provider_name == "jitsi":
        return JitsiProvider()
    raise ValueError(f"Unsupported video provider: {provider_name}")

def create_meeting(provider_name: str, identity: str, **kwargs) -> MeetingResult:
    provider = get_provider(provider_name)
    return provider.create_meeting(identity, **kwargs)
