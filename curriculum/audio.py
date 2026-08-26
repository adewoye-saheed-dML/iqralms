"""Short-lived private access to a placement recitation sample (Phase 6).

One function mints the URLs and one function checks the tokens, so there is a
single place to look for "how does anybody hear a recitation sample".

Two mechanisms, chosen by what the storage backend can do:

* **A private bucket** (production) mints its own presigned URL. The client
  fetches the object straight from the bucket; the application is not in the
  path, and no code here needs to know the file's bytes exist.
* **Local private storage** (development, tests) cannot sign anything, so this
  module signs a token with ``SECRET_KEY`` and points the client at a Django
  view that checks it and streams the file. That view is the *only* way a local
  file becomes readable over HTTP — there is no media route any more.

Both produce a URL that stops working. That is the property the phase is for:
Phases 2-5 served these files from a permanent public path, and a permanent URL
to a minor's voice recording is the thing being removed.

**What the token authorises, and what it does not.** A minted token is a bearer
capability for one stored object, exactly like the presigned URL it stands in
for: whoever holds it can fetch that file until it expires. Authorisation
happens *before* minting — in ``PlacementAudioURLView``, which decides that this
requester may hear this sample. The download view re-checks that the token is
intact, unexpired, and still points at the sample the placement currently holds,
but it does not re-authenticate: the holder of a valid token is authorised by
having been given it. A re-submitted sample gets a new storage key, so tokens
for the displaced recording stop resolving even before they expire.
"""

from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone as dj_timezone

#: Namespaces the signature. A token minted for placement audio cannot be
#: replayed against any other ``django.core.signing`` consumer, present or
#: future, even though both use ``SECRET_KEY``.
AUDIO_TOKEN_SALT = "curriculum.placement-audio"

#: Query parameter carrying the token, for the local-storage path.
AUDIO_TOKEN_PARAM = "token"


class PlacementAudioUnavailable(Exception):
    """This placement has no stored sample to grant access to."""


def _ttl_seconds():
    return settings.PLACEMENT_AUDIO_URL_TTL_SECONDS


def mint_audio_token(placement):
    """A signed, timestamped token for ``placement``'s current sample.

    The stored object's name is inside the signature, so a token cannot be
    carried over to a replacement recording — re-submitting invalidates every
    token already issued, without needing a revocation list.
    """
    return signing.TimestampSigner(salt=AUDIO_TOKEN_SALT).sign_object(
        {"placement": placement.pk, "name": placement.audio_sample.name}
    )


def read_audio_token(token):
    """The payload of a valid, unexpired token, or None.

    Every failure mode — tampered signature, wrong salt, malformed payload,
    expired timestamp — returns None rather than raising, because the caller's
    answer is the same 404 for all of them. Distinguishing them in an API
    response would tell an attacker which part of a guessed token was wrong.
    """
    try:
        return signing.TimestampSigner(salt=AUDIO_TOKEN_SALT).unsign_object(
            token, max_age=_ttl_seconds()
        )
    except (signing.BadSignature, signing.SignatureExpired, ValueError, TypeError):
        return None


def placement_audio_access(placement, request=None):
    """``(url, expires_at)`` giving temporary read access to the sample.

    Raises ``PlacementAudioUnavailable`` when the placement has no recording —
    a beginner skip, which is a legitimate state and not an error condition, so
    the caller turns it into a 404 rather than a 500.

    ``request`` is only used to make the local-storage URL absolute. The bucket
    path does not need it, which is why it is optional rather than required.
    """
    audio = placement.audio_sample
    if not audio:
        raise PlacementAudioUnavailable(
            "This placement has no audio sample — the student skipped as a "
            "beginner."
        )

    expires_in = _ttl_seconds()
    expires_at = dj_timezone.now() + timedelta(seconds=expires_in)
    storage = audio.storage

    if getattr(storage, "provides_signed_urls", False):
        return storage.signed_url(audio.name, expires_in), expires_at

    path = reverse("curriculum:placement-audio-download", args=[placement.pk])
    url = f"{path}?{AUDIO_TOKEN_PARAM}={mint_audio_token(placement)}"
    return (request.build_absolute_uri(url) if request else url), expires_at
