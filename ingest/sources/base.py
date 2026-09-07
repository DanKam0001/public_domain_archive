"""The source adapter interface.

An adapter's job is to **report what a source claims** and nothing more.

It must not interpret, normalise, or improve a licence. It passes the source's
raw licence signals through to the gate and lets the gate decide. An adapter
that helpfully maps an unrecognised licence onto a known one has defeated the
entire safety model, because the gate can then no longer tell a real CC0
declaration from an adapter's guess.

This is why ``RawItem`` carries three separate, all-optional licence fields
rather than one tidy ``license`` string: real sources express licensing in
different shapes, and flattening them here would mean making exactly the
judgement calls that belong in the gate.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests


@dataclass(frozen=True)
class RawItem:
    """One candidate item, exactly as the source described it.

    Nothing here has been validated. An instance of this class is a *claim*,
    not a catalog entry — it becomes an entry only after the gate admits it.
    """

    source: str
    source_id: str
    source_url: str          # human-facing page, for verification. Always required.
    file_url: str            # direct link to the actual bytes

    title: str | None = None
    description: str | None = None
    creator: str | None = None

    # Licence signals, verbatim. Any or all may be None — that is the common
    # case in real data and must remain expressible.
    license_url: str | None = None
    license_text: str | None = None
    explicit_license_id: str | None = None

    mime: str | None = None
    width: int | None = None
    height: int | None = None
    bytes: int | None = None

    # The source's full response for this item, kept verbatim as evidence of
    # what we were told and when.
    raw: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    """Base class for all sources.

    Subclasses implement :meth:`harvest`. Everything else here exists to make
    adapters polite by default rather than by remembering to be.
    """

    #: Stable identifier, stored on every row. Changing it orphans existing data.
    name: str

    #: Seconds between requests. These archives are mostly non-profits running on
    #: donations; we are a guest. Slow and welcome beats fast and blocked.
    delay: float = 0.5

    def __init__(self, user_agent: str, session: requests.Session | None = None) -> None:
        if not user_agent or "http" not in user_agent:
            # Several of these APIs will rate-limit or block anonymous-looking
            # traffic, and they are right to. Identify properly or don't run.
            raise ValueError(
                "user_agent must identify the project and give a contact URL, "
                "e.g. 'public_domain_archive/0.1 (+https://github.com/...)'"
            )
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request = 0.0

    @abstractmethod
    def harvest(self, limit: int) -> Iterator[RawItem]:
        """Yield up to ``limit`` candidate items.

        Candidates, not catalog entries: the caller runs each one past the gate,
        and adapters should expect a substantial rejection rate. For sources
        with poor licence metadata that rate approaches 100%, which is the
        system working correctly rather than the adapter failing.
        """

    # -- helpers for subclasses ---------------------------------------------

    def _get(self, url: str, params: dict[str, Any] | None = None,
             timeout: int = 30, retries: int = 3) -> dict[str, Any]:
        """GET returning JSON, rate-limited and retried with backoff.

        Retries on 429 and 5xx only. A 4xx other than 429 means we asked wrongly
        and retrying would just be rude.
        """
        for attempt in range(retries):
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_request = time.monotonic()

            response = self.session.get(url, params=params, timeout=timeout)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == retries - 1:
                    response.raise_for_status()
                # Honour Retry-After when the server tells us; otherwise back off.
                wait = float(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                time.sleep(min(wait, 60))
                continue

            response.raise_for_status()
            return response.json()

        raise RuntimeError(f"unreachable: retries exhausted for {url}")


def _int_or_none(value: Any) -> int | None:
    """Sources are inconsistent about numeric types; some send strings, some
    send nulls, some omit the field. None of that should crash a harvest."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
