"""Openverse adapter.

Openverse (https://openverse.org) aggregates openly-licensed media from Flickr,
museums, and other providers, and — crucially — exposes licence as a **queryable
filter with a canonical identifier per item**. That is exactly the property that
archive.org's image metadata lacks.

We still pass every licence signal to the gate rather than trusting the filter.
Asking for ``license=cc0,pdm`` and *verifying* that we got CC0 or PDM are
different things, and only the second one is a safety property.

Rate limits (measured, anonymous)
---------------------------------
``20/min`` burst and ``200/day`` sustained; ``page_size`` above 20 and deep
pagination both return 401 without a key. So this adapter is deliberately shaped
to issue **many shallow queries across varied seed terms** rather than paging
deep into one. That happens to give better topical diversity too.

A free API key (registerable at ``/v1/auth_tokens/register/``) lifts these
limits and is worth doing before any large harvest.
"""

from __future__ import annotations

from typing import Iterator

from .base import RawItem, Source, _int_or_none

API = "https://api.openverse.org/v1/images/"

#: Seed terms exist because Openverse has no "list everything" endpoint — a
#: query is required. Breadth here is what makes the Phase 1 sample diverse
#: enough to evaluate search quality honestly; a catalog of nothing but
#: landscapes would flatter CLIP and tell us little.
DEFAULT_SEEDS = (
    "forest", "mountain", "ocean", "city street", "portrait", "flower",
    "bird", "architecture", "desert", "river", "snow", "sunset",
    "market", "bridge", "farm", "storm", "factory", "harbour",
    "library", "train", "garden", "canyon", "village", "coastline",
)

# Anonymous callers are capped in pagination depth; going past this earns a 401.
MAX_PAGE = 10
PAGE_SIZE = 20


class OpenverseSource(Source):
    name = "openverse"
    delay = 3.5  # keeps us under the measured 20/min burst with margin

    def __init__(self, *args, seeds: tuple[str, ...] = DEFAULT_SEEDS, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seeds = seeds

    def harvest(self, limit: int) -> Iterator[RawItem]:
        seen: set[str] = set()
        yielded = 0

        # Round-robin across seeds rather than exhausting one at a time, so a
        # partial run (interrupted, or limit reached early) is still topically
        # broad instead of being 300 pictures of forests.
        for page in range(1, MAX_PAGE + 1):
            for seed in self.seeds:
                if yielded >= limit:
                    return
                try:
                    payload = self._get(
                        API,
                        params={
                            "q": seed,
                            # Ask for permissive licences only. This is an
                            # efficiency measure, not a safety one — the gate
                            # still independently verifies every item.
                            "license": "cc0,pdm",
                            "page_size": PAGE_SIZE,
                            "page": page,
                        },
                    )
                except Exception:
                    # One bad seed or a transient failure must not kill a long
                    # harvest. Skip and continue; the run reports totals at the end.
                    continue

                for record in payload.get("results", []):
                    item_id = record.get("id")
                    if not item_id or item_id in seen:
                        continue
                    seen.add(item_id)

                    item = self._to_raw_item(record)
                    if item is None:
                        continue

                    yield item
                    yielded += 1
                    if yielded >= limit:
                        return

    def _to_raw_item(self, record: dict) -> RawItem | None:
        file_url = record.get("url")
        # foreign_landing_url is the human-facing page at the original provider.
        # It is what a user clicks to verify the licence claim themselves, so an
        # item without one is not verifiable and we drop it.
        landing = record.get("foreign_landing_url")
        if not file_url or not landing:
            return None

        return RawItem(
            source=self.name,
            source_id=str(record["id"]),
            source_url=landing,
            file_url=file_url,
            title=record.get("title"),
            description=None,          # Openverse exposes tags, not descriptions
            creator=record.get("creator"),
            # All three licence signals, unmodified. `license` is a canonical
            # short id ("cc0", "pdm"); license_url disambiguates the version.
            license_url=record.get("license_url"),
            license_text=record.get("attribution"),
            explicit_license_id=record.get("license"),
            mime=record.get("filetype"),
            width=_int_or_none(record.get("width")),
            height=_int_or_none(record.get("height")),
            bytes=_int_or_none(record.get("filesize")),
            raw=record,
        )
