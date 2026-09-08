"""archive.org adapter — video (Phase 2).

archive.org is the best public domain *video* source that exists, and it is a
poor public domain *image* source. That asymmetry is measured, not assumed, and
it is why this adapter exists for movies while Phase 1's images came from
Openverse and Wikimedia Commons.

What the measurements showed
----------------------------
For images (Phase 1 planning): 11 of 12 items sampled from the ``nasa``
collection had no licence metadata at all, and the one that did was CC BY-NC —
a NonCommercial work sitting inside the collection everyone assumes is public
domain. Unusable as a primary image source.

For video, sampling by *collection* looks equally bad — 9 of 12 Prelinger items
carry no licence field. But sampling the corpus by the licence field itself
tells a different story:

    mediatype:movies AND licenseurl:*publicdomain*   ->  433,445 items

and of 60 sampled from that set, the gate admitted **60** — 47 Public Domain
Mark, 13 CC0. Modern, machine-readable, unambiguous.

So the rule stays exactly what it was: never infer a licence from collection
membership, always read it per item. Applied to video, that rule *finds* a large
clean corpus rather than excluding one.

Why this yields shots, not films
--------------------------------
A film is not a useful retrieval unit. Nobody wants "this 40-minute
documentary"; they want the seven seconds of a steam train crossing a bridge.
Phase 2 therefore treats each detected shot as the indexed item, which is also
what makes ``auto_broll(script)`` possible later — a script line maps to a shot,
not to a feature film.

Measured derivative sizes: MPEG4 median 133MB, h.264 median 83MB. Downloading
whole films to index them is the expensive part, and storing them would be
1TB per ~10,000 films. See ``ingest/pipeline/shots.py`` for how that is avoided.
"""

from __future__ import annotations

import random
from typing import Iterator

from .base import RawItem, Source, _int_or_none

SEARCH_API = "https://archive.org/advancedsearch.php"
METADATA_API = "https://archive.org/metadata"
DOWNLOAD_BASE = "https://archive.org/download"

#: Only items whose licence field positively asserts public domain. This is a
#: search-efficiency filter, not a safety one — the gate independently verifies
#: every item, exactly as it does for every other source.
QUERY = "mediatype:movies AND licenseurl:*publicdomain*"

#: Preference order for which derivative to fetch. Smaller h.264 derivatives
#: first: we decode frames for shot detection and CLIP resizes to 224px, so a
#: larger master buys nothing and costs bandwidth measured in tens of MB.
PREFERRED_FORMATS = ("h.264 IA", "h.264", "MPEG4", "512Kb MPEG4", "Ogg Video")

MAX_VIDEO_BYTES = 600 * 1024 * 1024


class ArchiveOrgVideoSource(Source):
    name = "archive_org_video"
    #: archive.org is slower and more rate-sensitive than Commons, and each of
    #: our requests fans out into a metadata lookup.
    delay = 1.5

    def __init__(self, *args, query: str = QUERY, shards: int = 24,
                 shuffle: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.query = query
        self.shards = shards
        self.shuffle = shuffle
        self.skipped_no_video = 0

    def harvest(self, limit: int) -> Iterator[RawItem]:
        """Yield candidate films.

        Paged from random offsets for the same reason the Wikimedia adapter
        shards: a sequential walk returns one relevance ordering and produces a
        corpus that looks nothing like the archive.
        """
        seen: set[str] = set()
        yielded = 0
        rows = 100

        # How deep the result set goes, so random pages land inside it.
        total = self._count()
        max_page = max(1, min(total // rows, 500))

        pages = list(range(1, max_page + 1))
        if self.shuffle:
            random.shuffle(pages)

        for page in pages:
            if yielded >= limit:
                return
            try:
                payload = self._get(SEARCH_API, params={
                    "q": self.query,
                    "fl[]": ["identifier", "title", "description", "creator",
                             "licenseurl", "year"],
                    "rows": rows, "page": page, "output": "json",
                })
            except Exception:
                continue

            for doc in (payload.get("response") or {}).get("docs", []):
                identifier = doc.get("identifier")
                if not identifier or identifier in seen:
                    continue
                seen.add(identifier)

                item = self._to_raw_item(doc)
                if item is None:
                    continue
                yield item
                yielded += 1
                if yielded >= limit:
                    return

    def _count(self) -> int:
        try:
            payload = self._get(SEARCH_API, params={
                "q": self.query, "rows": 0, "output": "json"})
            return int((payload.get("response") or {}).get("numFound", 0))
        except Exception:
            return 0

    def _to_raw_item(self, doc: dict) -> RawItem | None:
        identifier = doc["identifier"]
        try:
            meta = self._get(f"{METADATA_API}/{identifier}")
        except Exception:
            return None

        video = self._pick_derivative(meta.get("files") or [])
        if video is None:
            self.skipped_no_video += 1
            return None

        metadata = meta.get("metadata") or {}
        return RawItem(
            source=self.name,
            source_id=identifier,
            source_url=f"https://archive.org/details/{identifier}",
            file_url=f"{DOWNLOAD_BASE}/{identifier}/{video['name']}",
            title=_first(doc.get("title")) or metadata.get("title"),
            description=_truncate(_first(doc.get("description"))),
            creator=_first(doc.get("creator")),
            # Every licence signal, verbatim, for the gate to judge.
            license_url=metadata.get("licenseurl") or _first(doc.get("licenseurl")),
            license_text=metadata.get("rights"),
            explicit_license_id=metadata.get("license"),
            mime="video/mp4",
            bytes=_int_or_none(video.get("size")),
            raw={"identifier": identifier, "metadata": metadata,
                 "chosen_file": video.get("name"),
                 "chosen_format": video.get("format")},
        )

    def _pick_derivative(self, files: list[dict]) -> dict | None:
        """Smallest acceptable video derivative, by preference order."""
        by_format: dict[str, list[dict]] = {}
        for f in files:
            fmt = f.get("format")
            if fmt in PREFERRED_FORMATS and f.get("name"):
                size = _int_or_none(f.get("size")) or 0
                if 0 < size <= MAX_VIDEO_BYTES:
                    by_format.setdefault(fmt, []).append(f)
        for fmt in PREFERRED_FORMATS:
            candidates = by_format.get(fmt)
            if candidates:
                return min(candidates, key=lambda f: int(f.get("size") or 0))
        return None


def _first(value):
    """archive.org returns some fields as a string and others as a list."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _truncate(text, limit: int = 2000):
    if not text:
        return None
    return text[:limit]
