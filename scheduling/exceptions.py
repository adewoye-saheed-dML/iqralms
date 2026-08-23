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
