"""Domain errors raised by payout models and services.

Plain exceptions with no DRF import, the same split ``scheduling/exceptions.py``
and ``curriculum/exceptions.py`` use: the model raises, the view layer decides
which status code that is.
"""


class PayoutAlreadyFinalized(Exception):
    """Raised when finalizing a payout that is already finalized.

    Not a silent no-op: a finalized payout is a historical financial decision,
    so a second finalization is either a double-click or a caller working from a
    stale view of the record. The view layer turns this into a 409, the same way
    ``BookingNotCancellable`` is handled.
    """
