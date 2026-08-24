"""Domain errors raised by scheduling models.

Plain exceptions, deliberately free of any DRF import: models raise these and
the view layer translates them into HTTP responses (see views.Conflict). Same
split as curriculum/exceptions.py.
"""


class BookingNotCancellable(Exception):
    """Raised when cancelling a booking that is not currently ``scheduled``.

    A completed or no-show session is attendance history, and an already
    cancelled one has nothing left to cancel. Neither is a silent no-op.
    """


class CohortFull(Exception):
    """Raised by ``Cohort.add_student`` when a seat cannot be given.

    Named for the case that matters — the class is at ``max_students`` — but also
    covers a non-student being seated. A plain exception rather than a
    ``ValidationError`` because ``add_student`` is a behaviour method, not a
    field-level check: the view layer turns it into a 409, the same way
    ``BookingNotCancellable`` is handled.
    """


class NoCapacity(Exception):
    """Raised by the routing engine when steps 1-3 all fail.

    The Phase 4 spec is explicit that this is correct behaviour rather than a
    bug to route around: booking someone outside their availability or over their
    cap just to avoid an error defeats the entire point of the phase. Carries the
    per-step reasons so the response can say *why* honestly.
    """

    def __init__(self, message, considered=None):
        super().__init__(message)
        self.considered = considered or {}
