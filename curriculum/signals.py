"""Placement audio cleanup: keep only the sample a placement currently points at.

Retention decision (2026-08-23, product owner): a placement keeps exactly one
recitation sample. Replacing the audio deletes the displaced file, and deleting
the row deletes its file, so ``MEDIA_ROOT`` never accumulates orphans and a
deletion request is satisfied by deleting the row. The tradeoff accepted with
it: there is no recording of what a *past* review listened to. If an audit trail
is ever needed, that is a history table, not a change here (see tech-debt.md).

Two details this leans on, both deliberate:

* Deletion is deferred to ``transaction.on_commit``. Django's file storage is
  not transactional, so deleting inline would destroy the file even if the write
  that displaced it were rolled back — leaving a row pointing at nothing. In
  autocommit (this project's default) the callback runs immediately.
* Registering these receivers switches ``PlacementResult`` off Django's
  fast-delete path, which is what makes the file cleanup fire for *cascades*
  too — deleting a ``User`` cascades to their placements, and each one needs its
  file removed.
"""

from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import PlacementResult

#: Cleanup must not run for ``loaddata``, which writes rows verbatim.
_AUDIO_FIELD = "audio_sample"


def _delete_when_committed(storage, name):
    """Drop ``name`` from ``storage``, but only once the DB change sticks."""
    if not name:
        return
    transaction.on_commit(lambda: storage.delete(name))


@receiver(
    pre_save,
    sender=PlacementResult,
    dispatch_uid="curriculum.delete_displaced_placement_audio",
)
def delete_displaced_audio(sender, instance, raw=False, **kwargs):
    """Delete the previous sample when a placement's audio is replaced.

    Covers both ways a sample is displaced: re-submitting with a new recording,
    and re-submitting as a beginner (which clears the field entirely). A save
    that leaves the audio alone — stamping a review, editing in the admin — is
    a no-op here.
    """
    if raw or instance.pk is None:
        return

    stored_name = (
        sender.objects.filter(pk=instance.pk)
        .values_list(_AUDIO_FIELD, flat=True)
        .first()
    )
    if not stored_name:
        return

    # This runs before FileField.pre_save() commits the incoming upload, so an
    # unsaved file's name is still the raw upload name and cannot equal a stored
    # ``upload_to`` path. Comparing names is therefore enough to tell "replaced"
    # from "untouched" without a storage hit.
    incoming = instance.audio_sample.name if instance.audio_sample else None
    if incoming == stored_name:
        return

    _delete_when_committed(
        sender._meta.get_field(_AUDIO_FIELD).storage, stored_name
    )


@receiver(
    post_delete,
    sender=PlacementResult,
    dispatch_uid="curriculum.delete_placement_audio_on_delete",
)
def delete_audio_on_delete(sender, instance, **kwargs):
    """Delete a placement's sample when the row goes, cascades included."""
    audio = getattr(instance, _AUDIO_FIELD, None)
    if audio:
        _delete_when_committed(audio.storage, audio.name)
