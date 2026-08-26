"""Validation for uploaded placement audio (Phase 6).

Until this phase ``PlacementResult.audio_sample`` was a bare ``FileField``, so a
student account could POST a 2 GB executable and the platform would file it as a
recitation sample. CLAUDE.md's rule — user-uploaded files are untrusted input,
validate size, declared type, and actual content before persistence — is what
this module implements.

**Import-light on purpose.** ``config/settings.py`` imports
``MAX_PLACEMENT_AUDIO_BYTES`` from here to size ``DATA_UPLOAD_MAX_MEMORY_SIZE``,
so this module must not import models, settings, or DRF. It imports one thing
from Django, and that one thing does not read settings.

**Three checks, in the order that produces the most useful error:**

1. Size, against an explicit cap. Cheapest, and the only one an honest client
   trips.
2. Filename extension, against an allowlist. Tells a student "we don't take
   .aiff" rather than "your file is corrupt".
3. The file's own leading bytes, against that extension's signature. This is the
   one that matters for abuse: it is what rejects a payload renamed
   ``recitation.mp3``.

The declared ``Content-Type`` is checked *last and never alone*. It is a string
the client chose, so it can say anything; a mismatch is evidence of a problem
but agreement proves nothing. Rejecting on the signature is what makes the
check real.

Chosen values, recorded here rather than left implicit as the phase spec asks:
15 MiB, and six formats covering what a browser's MediaRecorder produces
(webm, ogg), what a phone records (m4a), and what a desktop recorder saves
(mp3, wav, flac).
"""

import os

from django.core.exceptions import ValidationError

#: 15 MiB. A two-minute recitation is roughly 2 MB as a 128 kbps MP3 and roughly
#: 10 MB as uncompressed mono WAV, so this takes an honest sample in any of the
#: allowed formats while refusing anything that is obviously not one. Kept as
#: bytes rather than megabytes because that is the unit every caller wants.
MAX_PLACEMENT_AUDIO_BYTES = 15 * 1024 * 1024

#: How much of the file the signature check reads. 12 bytes is the longest
#: signature below (WAV needs ``RIFF`` at 0 *and* ``WAVE`` at 8); 16 leaves
#: margin for a format added later without rereading the file.
SIGNATURE_PROBE_BYTES = 16

#: Declared types that carry no information — a browser that could not work out
#: what it was sending, not a client making a claim. Treated as "nothing
#: declared" rather than as a mismatch, because rejecting them would break honest
#: uploads while stopping no attacker (anyone spoofing a type picks a real one).
UNINFORMATIVE_CONTENT_TYPES = frozenset(
    {"", "application/octet-stream", "binary/octet-stream"}
)


def _is_wav(head):
    return head.startswith(b"RIFF") and head[8:12] == b"WAVE"


def _is_ogg(head):
    return head.startswith(b"OggS")


def _is_mp4(head):
    # ISO base media: a 4-byte box size, then the 'ftyp' box type.
    return head[4:8] == b"ftyp"


def _is_webm(head):
    # EBML header — shared with Matroska, which is the same container family.
    return head.startswith(b"\x1a\x45\xdf\xa3")


def _is_flac(head):
    return head.startswith(b"fLaC")


def _is_mp3(head):
    """An ID3v2 tag, or a raw MPEG audio frame header.

    The frame-sync form (eleven set bits: ``0xFF`` then the top three bits of
    the next byte) is the loosest test in this module — roughly one arbitrary
    byte pair in 2048 satisfies it. That is why MP3 is checked *last*: a real
    ``ftyp`` or ``OggS`` file is identified by its own signature first, and only
    bytes no specific signature claims are offered to this one.
    """
    if head.startswith(b"ID3"):
        return True
    return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0


class AudioFormat:
    """One accepted audio format: its extensions, its types, its signature."""

    def __init__(self, label, extensions, content_types, signature_matches):
        self.label = label
        self.extensions = frozenset(extensions)
        self.content_types = frozenset(content_types)
        self.signature_matches = signature_matches

    def __repr__(self):
        return f"<AudioFormat {self.label}>"


#: The allowlist. Order is the *detection* order and matters: the loose MP3
#: frame-sync test is last so that every specific signature gets first refusal.
AUDIO_FORMATS = (
    AudioFormat(
        "WAV",
        {".wav"},
        {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"},
        _is_wav,
    ),
    AudioFormat(
        "Ogg",
        {".ogg", ".oga", ".opus"},
        {"audio/ogg", "audio/vorbis", "audio/opus", "application/ogg"},
        _is_ogg,
    ),
    AudioFormat(
        "MPEG-4 audio",
        {".m4a", ".mp4"},
        {"audio/mp4", "audio/m4a", "audio/x-m4a", "video/mp4"},
        _is_mp4,
    ),
    AudioFormat(
        "WebM",
        # A browser recording with MediaRecorder produces this by default on
        # Chrome and Firefox, so it is the most likely format in practice.
        {".webm"},
        {"audio/webm", "video/webm"},
        _is_webm,
    ),
    AudioFormat("FLAC", {".flac"}, {"audio/flac", "audio/x-flac"}, _is_flac),
    AudioFormat(
        "MP3",
        {".mp3"},
        {"audio/mpeg", "audio/mp3", "audio/mpeg3", "audio/x-mpeg-3"},
        _is_mp3,
    ),
)

_FORMAT_BY_EXTENSION = {
    extension: audio_format
    for audio_format in AUDIO_FORMATS
    for extension in audio_format.extensions
}

#: For error messages and for the tests to assert against.
ALLOWED_AUDIO_EXTENSIONS = tuple(sorted(_FORMAT_BY_EXTENSION))


def _extension_of(name):
    return os.path.splitext(name or "")[1].lower()


def _read_signature(upload):
    """The first bytes of ``upload``, leaving it rewound for the actual save.

    ``File.chunks()`` rewinds before writing, so this is belt and braces — but
    a validator that consumes a stream and hands back a half-read file is a
    genuinely nasty bug to find, so it does not rely on that.
    """
    try:
        upload.seek(0)
    except (AttributeError, OSError, ValueError):
        pass
    head = upload.read(SIGNATURE_PROBE_BYTES) or b""
    try:
        upload.seek(0)
    except (AttributeError, OSError, ValueError):
        pass
    return head


def _detect(head):
    for audio_format in AUDIO_FORMATS:
        if audio_format.signature_matches(head):
            return audio_format
    return None


def validate_placement_audio(upload):
    """Raise ``ValidationError`` unless ``upload`` is an acceptable sample.

    Used in two places, which is deliberate: ``PlacementSubmitSerializer``
    calls it so the API answers with a clean field error, and
    ``PlacementResult.clean()`` calls it for anything that reaches the model
    without passing through that serializer — the admin's upload widget above
    all. See the comment there for why the model check is scoped to files that
    arrived over HTTP.

    Returns ``upload`` so it can be used as a DRF or Django field validator.
    """
    size = getattr(upload, "size", None)

    if size is not None:
        if size <= 0:
            raise ValidationError(
                "That file is empty. Record a sample and try again.",
                code="empty_audio",
            )
        if size > MAX_PLACEMENT_AUDIO_BYTES:
            raise ValidationError(
                "That sample is %(given).1f MB; the limit is %(limit).0f MB. A "
                "short recitation is enough — there is no need to send a long "
                "or uncompressed recording.",
                code="audio_too_large",
                params={
                    "given": size / (1024 * 1024),
                    "limit": MAX_PLACEMENT_AUDIO_BYTES / (1024 * 1024),
                },
            )

    extension = _extension_of(getattr(upload, "name", ""))
    expected = _FORMAT_BY_EXTENSION.get(extension)
    if expected is None:
        raise ValidationError(
            "%(extension)s is not a supported audio format. Send one of: "
            "%(allowed)s.",
            code="audio_extension_not_allowed",
            params={
                "extension": f"'{extension}'" if extension else "That file type",
                "allowed": ", ".join(ALLOWED_AUDIO_EXTENSIONS),
            },
        )

    detected = _detect(_read_signature(upload))
    if detected is None:
        raise ValidationError(
            "That file does not look like audio. Its contents do not match any "
            "supported format, whatever its name or declared type says.",
            code="audio_unrecognised_content",
        )
    if detected is not expected:
        raise ValidationError(
            "That file is named like %(claimed)s but its contents are "
            "%(actual)s. Send the file in the format its name says, or rename "
            "it to match.",
            code="audio_content_mismatch",
            params={"claimed": expected.label, "actual": detected.label},
        )

    declared = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    if declared not in UNINFORMATIVE_CONTENT_TYPES:
        if declared not in expected.content_types:
            raise ValidationError(
                "The declared content type '%(declared)s' does not match a "
                "%(expected)s upload.",
                code="audio_content_type_mismatch",
                params={"declared": declared, "expected": expected.label},
            )

    return upload
