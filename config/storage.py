"""Private storage backends for user uploads (Phase 6).

Phases 2-5 kept placement recordings on local disk under a public ``MEDIA_URL``,
which meant anyone holding the path could fetch a minor's voice recording. This
module replaces that with two backends that share one rule and one interface.

**The rule:** an object is never reachable from a URL that does not expire.

**The interface:** ``signed_url(name, expires_in)`` returns a URL usable for
``expires_in`` seconds and no longer. ``provides_signed_urls`` says whether the
backend can mint one itself:

* ``PrivateS3Storage`` — an S3-compatible private bucket. It can, using the
  bucket's own presigned URLs, so nothing but the client and the bucket is
  involved in serving the file. This is the production backend.
* ``PrivateLocalStorage`` — local disk for development and tests. It cannot, so
  ``provides_signed_urls`` is False and the caller falls back to a token-gated
  Django view. Its ``url()`` **raises**, which is the point: a
  ``FileSystemStorage`` with an empty ``MEDIA_URL`` would happily hand back a
  bare relative path, and something would eventually try to serve it.

Callers do not choose between these. ``curriculum.audio.placement_audio_access``
is the one place that reads ``provides_signed_urls``, so adding a third backend
means adding it here and nowhere else.
"""

from django.core.exceptions import SuspiciousOperation
from django.core.files.storage import FileSystemStorage
from storages.backends.s3 import S3Storage


class PrivateStorageMixin:
    """The contract both backends answer to."""

    #: Whether this backend can mint its own expiring URL. False means the
    #: caller must arrange private access some other way — see the module
    #: docstring.
    provides_signed_urls = False

    def signed_url(self, name, expires_in):
        """A URL for ``name`` that stops working after ``expires_in`` seconds."""
        raise NotImplementedError(
            f"{type(self).__name__} cannot mint signed URLs; check "
            "provides_signed_urls before calling this."
        )


class PrivateS3Storage(PrivateStorageMixin, S3Storage):
    """An S3-compatible bucket holding objects nobody can read unsigned.

    The privacy is configuration rather than code — ``querystring_auth``,
    ``default_acl=None`` and ``file_overwrite=False``, all set in
    ``config/settings.py`` where the reasoning for each is written down. What
    this class adds is the uniform ``signed_url`` entry point, so that the code
    minting a placement-audio URL does not have to know it is talking to S3.

    ``expires_in`` is passed per call rather than relying on the configured
    ``querystring_expire``, so a caller that wants a shorter-lived URL gets one
    without reconfiguring the bucket.
    """

    provides_signed_urls = True

    def signed_url(self, name, expires_in):
        return self.url(name, expire=expires_in)


class PrivateLocalStorage(PrivateStorageMixin, FileSystemStorage):
    """``MEDIA_ROOT`` on local disk, with no public URL at all.

    The development and test stand-in for a private bucket. It deliberately does
    *not* implement ``signed_url``: signing a local path needs a view to serve
    it, a token to gate that view, and a permission re-check inside it — all of
    which live in ``curriculum/`` alongside the permission rules they enforce,
    not in a storage backend.

    ``location`` is not passed in ``STORAGES["default"]["OPTIONS"]``, so it
    resolves ``settings.MEDIA_ROOT`` lazily. That is what lets the test runner
    redirect every upload into a throwaway directory.
    """

    def url(self, name):
        """Always raises. There is no public URL for a recitation sample.

        ``FileSystemStorage.url()`` with an empty ``MEDIA_URL`` returns the bare
        stored path — a string that looks enough like a URL to end up in an API
        response or a template. Raising instead means the mistake surfaces in
        development, at the line that made it, rather than as a leaked path in
        production.
        """
        raise SuspiciousOperation(
            f"{type(self).__name__} has no public URLs: {name!r} is private "
            "user-uploaded media. Use curriculum.audio.placement_audio_access() "
            "to mint a short-lived signed URL instead."
        )
