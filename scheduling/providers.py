import hashlib
import json
import urllib.request
import urllib.error
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

class DailyProvider(MeetingProvider):
    def create_meeting(self, identity: str, **kwargs) -> MeetingResult:
        hash_hex = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
        room_name = f"iqralms-{hash_hex}"
        daily_domain = getattr(settings, "DAILY_DOMAIN", "iqralms.daily.co")
        api_key = getattr(settings, "DAILY_API_KEY", "")

        join_url = f"https://{daily_domain}/{room_name}"

        # If an API key is provided, provision the room via Daily.co REST API with cloud recording enabled
        if api_key:
            try:
                payload = {
                    "name": room_name,
                    "privacy": "public",
                    "properties": {
                        "enable_chat": True,
                        "enable_screenshare": True,
                        "enable_knocking": False,
                        "enable_recording": "cloud" if getattr(settings, "DAILY_ENABLE_RECORDING", True) else "off",
                        "start_video_off": False,
                        "start_audio_off": False,
                    },
                }
                exp_timestamp = kwargs.get("exp")
                if exp_timestamp:
                    payload["properties"]["exp"] = int(exp_timestamp)

                req = urllib.request.Request(
                    "https://api.daily.co/v1/rooms",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status in (200, 201):
                        data = json.loads(resp.read().decode("utf-8"))
                        join_url = data.get("url") or join_url
            except urllib.error.HTTPError as e:
                # Room already exists or bad request falls back to deterministic room URL
                join_url = f"https://{daily_domain}/{room_name}"
            except Exception:
                # Network or timeout failure gracefully falls back to deterministic join_url
                pass

        return MeetingResult(
            provider="daily",
            provider_meeting_id=room_name,
            join_url=join_url
        )

def get_provider(provider_name: str) -> MeetingProvider:
    normalized = (provider_name or "").lower()
    if normalized == "jitsi":
        return JitsiProvider()
    if normalized == "daily":
        return DailyProvider()
    raise ValueError(f"Unsupported video provider: {provider_name}")

def create_meeting(provider_name: str, identity: str, **kwargs) -> MeetingResult:
    provider = get_provider(provider_name)
    return provider.create_meeting(identity, **kwargs)
