"""Domain errors raised by curriculum models.

Plain exceptions, deliberately free of any DRF import: models raise these and
the view layer translates them into HTTP responses (see views.Conflict).
"""


class PlacementAlreadyReviewed(Exception):
    """Raised when a review is attempted on an already-reviewed placement.

    Per the Phase 2 decision, review is a one-way transition: correcting a
    level means the admin, or the student re-submitting the placement.
    """


class TrackHasNoFirstLevel(Exception):
    """Raised when a beginner skip cannot resolve the track's ``order=1`` level.

    ``skipped_as_beginner`` auto-places into the first level of the track, so a
    track with no levels has nowhere to put the student.
    """
