"""Exception types.

Distinct classes rather than one generic error, because a script running
unattended needs to tell apart "slow down" from "this will never work" without
parsing message strings.
"""

from __future__ import annotations


class ArchiveError(Exception):
    """Base class for every error raised by this package."""


class NotFound(ArchiveError):
    """No such item, or it has been withdrawn following a licensing report."""


class RateLimited(ArchiveError):
    """Too many requests. Back off and retry.

    The client already retries this automatically; seeing it means the retries
    were also exhausted.
    """


class ServerError(ArchiveError):
    """The archive returned 5xx."""


class RequestError(ArchiveError):
    """The request itself was rejected — a bad query, an invalid id."""
