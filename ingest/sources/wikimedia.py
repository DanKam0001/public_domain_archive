"""Wikimedia Commons adapter.

Commons is the strongest public domain image source that exists: licensing is
per file, machine-readable via ``extmetadata``, and — unlike an upload-anything
archive — reviewed by a community with an actual deletion process for
mislicensed material.

It is also the bulk workhorse for Phase 1, because ``generator=categorymembers``
walks a whole licence category with a continuation token and no meaningful rate
limit, where Openverse is capped at 200 requests/day anonymously.

The ``Restrictions`` pre-filter
-------------------------------
Commons exposes a ``Restrictions`` field flagging **non-copyright** constraints:
trademark, personality rights, and similar. A photograph can be genuinely CC0 in
copyright terms and still be unusable in an advertisement because it depicts an
identifiable person or a trademarked logo.

This adapter skips those items. That is *not* the adapter making a licence
judgement — licensing remains entirely the gate's business, on a different axis.
It is declining to harvest something we already know carries a restriction our
consumers cannot evaluate. Given that those consumers are automated and
commercial, shipping them a personality-rights-encumbered image would be exactly
the harm this project exists to avoid.
"""

from __future__ import annotations

import math
import random
import re
import string
from typing import Iterator

from .base import RawItem, Source, _int_or_none

API = "https://commons.wikimedia.org/w/api.php"

#: Categories to walk. Both are unambiguous dedications rather than licences
#: with conditions, which is what Phase 1 wants.
DEFAULT_CATEGORIES = (
    "Category:CC-Zero",
    "Category:Public_domain",
)

BATCH = 200  # generous but polite; the API allows more for some users

#: Width of the rendition we fetch, via the API's `iiurlwidth` parameter.
#:
#: We deliberately do NOT download originals. Measured on a real sample, Commons
#: originals average ~2.7MB and reach 9MB+, which projects to ~1.9GB for a
#: 700-item harvest — while CLIP resizes everything to 224px before looking at
#: it. A 1280px rendition is ~20x smaller and changes retrieval quality not at
#: all.
#:
#: PRODUCT TRADEOFF, not just an optimisation: it means the file we store and
#: serve is web-resolution, and a user wanting the true original follows
#: `source_url`. For Phase 1 that is clearly right — it makes ingestion minutes
#: instead of hours and keeps storage tiny. If full-resolution download becomes
#: part of the product's value, this is the line to revisit, and the cost lands
#: in R2 storage rather than in egress.
RENDITION_WIDTH = 1280

#: Commons serves PDFs, DjVu, TIFF, SVG and video through the same image API.
#: Filtering on the ORIGINAL mime here — before any download — is what keeps a
#: book scan or a video from entering an image catalog.
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif",
               "image/tiff", "image/svg+xml"}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class WikimediaSource(Source):
    name = "wikimedia_commons"
    delay = 1.0

    def __init__(self, *args, categories: tuple[str, ...] = DEFAULT_CATEGORIES,
                 shuffle: bool = True, shards: int = 24, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.categories = categories
        #: Shuffle by default. A sequential walk is alphabetically clustered and
        #: produces an unrepresentative corpus; see :meth:`harvest`.
        self.shuffle = shuffle
        #: More shards means broader coverage but more API calls, since each
        #: shard pays its own first request. 24 keeps a 1,000-item run to a
        #: couple of dozen extra calls.
        self.shards = shards
        #: Diagnostic counter. Reported at the end of a run so a large number of
        #: skips is visible rather than silently shrinking the harvest.
        self.skipped_restricted = 0

    def harvest(self, limit: int) -> Iterator[RawItem]:
        """Harvest across many randomly-placed shards rather than sequentially.

        MediaWiki orders category members alphabetically, so walking a category
        from the start produces an alphabetically clustered sample. Measured on
        the first 1,038-item corpus: 51 items by Ferdowsi, a long run of Met
        Museum artworks whose titles begin with a quotation mark, and no cats at
        all. Retrieval evaluated on that is evaluated on an unrepresentative
        draw.

        Three approaches were measured against the live API:

          * ``generator=random`` — genuinely uniform, but cannot be combined
            with a category filter, so most results carry licences the gate
            will reject and those requests are wasted.
          * ``gcmsort=timestamp`` with random start dates — sounds right, since
            spreading across upload history should spread across contributors.
            In practice category membership is very lumpy in time, so most
            shards landed in sparse stretches or past the newest member:
            ~1.3 new items per shard, and a 200-item request returned 121.
          * ``gcmstartsortkeyprefix`` with random prefixes — **used here**.
            20 shards returned 1,000 items with zero empty shards, because it
            samples uniformly over members that actually exist rather than over
            a timeline.

        Prefix sharding also attacks the original bias head-on: each prefix
        bucket contributes equally regardless of how many items sit under it,
        so one bulk upload owning the front of the alphabet stops mattering.

        Measured over 200 items against the old sequential walk:

            distinct creators   66 -> 85   (top creator 17% -> 5%)
            distinct initials    6 -> 20   (top initial 90% -> 10%)
            redundant dupes     22% -> 0%
        """
        if not self.shuffle:
            yield from self._harvest_sequential(limit)
            return

        per_shard = max(5, math.ceil(limit / self.shards))
        seen: set[str] = set()
        yielded = 0

        # Shards are drawn one at a time rather than planned up front, so the
        # loop self-corrects when one lands somewhere unproductive and simply
        # draws another. With a fixed plan, wasted shards were lost outright and
        # a 200-item request returned 91.
        attempts = 0
        max_attempts = self.shards * 4

        while yielded < limit and attempts < max_attempts:
            attempts += 1
            category = random.choice(self.categories)
            for item in self._walk(category, per_shard, start=_random_prefix()):
                if item.source_id in seen:
                    continue
                seen.add(item.source_id)
                yield item
                yielded += 1
                if yielded >= limit:
                    return

    def _harvest_sequential(self, limit: int) -> Iterator[RawItem]:
        """Deterministic alphabetical walk. Kept for reproducible runs."""
        yielded = 0
        for category in self.categories:
            for item in self._walk(category, limit - yielded):
                yield item
                yielded += 1
                if yielded >= limit:
                    return

    def _walk(self, category: str, limit: int, start: str | None = None) -> Iterator[RawItem]:
        """Walk one category, optionally beginning at a sort-key prefix.

        ``start`` is a two-character prefix (e.g. ``"Dq"``); the walk begins at
        the first member sorting at or after it. That is how sharding places
        each slice somewhere different in the category's ordering.
        """
        yielded = 0

        # MediaWiki's continuation is a dict, not a single token, and which keys
        # it contains varies. When `prop=imageinfo` cannot fit every page's data
        # into one response it continues with `iistart` rather than
        # `gcmcontinue` — so reading only `gcmcontinue` makes a large category
        # look exhausted after one page. (It did: a 700-item harvest stopped at
        # 75.) The documented contract is to echo the whole `continue` object
        # back, so that is what we do.
        continuation: dict[str, str] = {}

        while yielded < limit:
            params = {
                "action": "query",
                "format": "json",
                "generator": "categorymembers",
                "gcmtitle": category,
                "gcmtype": "file",
                "gcmlimit": BATCH,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                # Ask for a scaled rendition alongside the original; the
                # response then carries `thumburl`. See RENDITION_WIDTH.
                "iiurlwidth": RENDITION_WIDTH,
            }
            # Only on the first request of a shard; after that the continuation
            # token carries the position.
            if start and not continuation:
                params["gcmstartsortkeyprefix"] = start
            params.update(continuation)

            try:
                payload = self._get(API, params=params)
            except Exception:
                break

            pages = (payload.get("query") or {}).get("pages") or {}
            if not pages:
                break

            for page in pages.values():
                item = self._to_raw_item(page)
                if item is None:
                    continue
                yield item
                yielded += 1
                if yielded >= limit:
                    return

            continuation = payload.get("continue") or {}
            if not continuation:
                break  # category genuinely exhausted

    def _to_raw_item(self, page: dict) -> RawItem | None:
        info_list = page.get("imageinfo") or []
        if not info_list:
            return None
        info = info_list[0]
        meta = info.get("extmetadata") or {}

        def field(key: str) -> str | None:
            value = (meta.get(key) or {}).get("value")
            return _clean(value) if value else None

        # Non-copyright restrictions — see module docstring.
        if field("Restrictions"):
            self.skipped_restricted += 1
            return None

        # Filter on the ORIGINAL mime, before spending any bandwidth. Commons
        # returns PDFs, DjVu, video and audio through this same endpoint, and
        # their `thumburl` is a rendered JPEG page — which would quietly admit
        # a scanned book into an image catalog.
        original_mime = (info.get("mime") or "").lower()
        if original_mime not in IMAGE_MIMES:
            return None

        # Prefer the scaled rendition; fall back to the original if the API
        # declined to produce one (it does that for some formats).
        file_url = info.get("thumburl") or info.get("url")
        landing = info.get("descriptionurl")
        if not file_url or not landing:
            return None

        return RawItem(
            source=self.name,
            source_id=str(page.get("pageid")),
            source_url=landing,
            file_url=file_url,
            title=field("ObjectName") or page.get("title"),
            description=field("ImageDescription"),
            creator=field("Artist"),      # arrives as HTML; _clean strips it
            license_url=(meta.get("LicenseUrl") or {}).get("value"),
            license_text=field("LicenseShortName"),
            explicit_license_id=(meta.get("License") or {}).get("value"),
            mime=info.get("mime"),
            width=_int_or_none(info.get("width")),
            height=_int_or_none(info.get("height")),
            bytes=_int_or_none(info.get("size")),
            raw={"pageid": page.get("pageid"), "title": page.get("title"),
                 # Keep a pointer to the true original even though we store a
                 # rendition, so full resolution stays recoverable later.
                 "original_url": info.get("url"),
                 "original_mime": original_mime,
                 "original_width": info.get("width"),
                 "original_height": info.get("height"),
                 "original_bytes": info.get("size"),
                 "extmetadata": meta},
        )


def _clean(value: str) -> str:
    """Strip the HTML that Commons embeds in creator and description fields.

    Artist in particular is usually an anchor tag wrapping a username. Left raw
    it would end up rendered as markup in the UI and in SDK output.
    """
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", value)).strip()


#: Shards start at a random point in the category's alphabetical ordering.
#:
#: Two strategies were measured against the live API. Sampling random *dates*
#: (`gcmsort=timestamp`) sounds better — spreading across upload history should
#: spread across contributors — but category membership is extremely lumpy in
#: time, so most shards landed in sparse stretches or past the newest member and
#: returned almost nothing: ~1.3 new items per shard, and a 200-item request
#: yielded 121.
#:
#: Random sortkey prefixes are uniform over the members that actually exist
#: rather than over a timeline: 20 shards returned 1,000 items with ZERO empty
#: shards. It also directly counteracts the original bias, because each prefix
#: bucket contributes equally no matter how many items sit under it — which is
#: the whole problem with one bulk upload owning the front of the alphabet.
_PREFIX_HEAD = string.ascii_uppercase + string.digits


def _random_prefix() -> str:
    """A random two-character sort-key prefix, e.g. 'Dq', '7m'."""
    return random.choice(_PREFIX_HEAD) + random.choice(string.ascii_lowercase)
