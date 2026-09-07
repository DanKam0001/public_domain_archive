"""Orchestrates one ingestion run.

Order matters: **gate first, download second.** We decide whether we are allowed
to hold an item before spending bandwidth on it, which is both cheaper and the
correct posture — we should never have a copy of something we were not entitled
to index.

Failure policy: any single item may fail (dead link, truncated JPEG, exotic
format, transient 500) and none of those may kill a run that has already done
hours of work. Every failure is counted by category and reported at the end, so
a systematic problem shows up as a large bucket rather than as silence.
"""

from __future__ import annotations

import collections
import dataclasses
import time
from typing import Callable

import requests

from ..licensing.gate import LicenseGate
from ..sources import Source
from .embed import Embedder, load_image
from .store import Storage, object_keys, to_row

# Formats we can decode and that browsers can display. Commons in particular
# serves plenty of PDFs, TIFFs, SVGs and videos through the same image API.
ACCEPTED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

EXTENSION = {"image/jpeg": "jpg", "image/png": "png",
             "image/webp": "webp", "image/gif": "gif"}

MAX_BYTES = 25 * 1024 * 1024

#: Pause between image downloads. Small, but it keeps a 1,000-item run from
#: looking like a scrape to the CDN serving it.
FETCH_DELAY = 0.15


@dataclasses.dataclass
class Stats:
    harvested: int = 0
    admitted: int = 0
    stored: int = 0
    rejected: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    failed: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    licenses: collections.Counter = dataclasses.field(default_factory=collections.Counter)


def run(
    source: Source,
    gate: LicenseGate,
    limit: int,
    *,
    dry_run: bool = False,
    batch_size: int = 16,
    log: Callable[[str], None] = print,
) -> Stats:
    stats = Stats()
    storage = None if dry_run else Storage()
    embedder = None if dry_run else Embedder()
    if embedder:
        log(f"embedding model: {embedder.model_id} on {embedder.device}")

    session = requests.Session()
    session.headers["User-Agent"] = source.session.headers["User-Agent"]

    pending: list[tuple] = []

    def flush() -> None:
        """Embed and store one batch.

        Batched because CLIP on a batch of 16 costs barely more than one image,
        and the per-item cost is dominated by the HTTP fetch anyway.
        """
        if not pending:
            return
        vectors = embedder.embed_images([p[1] for p in pending])
        for (item, image, decision, data, mime), vector in zip(pending, vectors):
            try:
                ext = EXTENSION[mime]
                full_key, thumb_key = object_keys(item.source, item.source_id, ext)
                storage.put_image(full_key, data, mime)
                storage.put_thumbnail(thumb_key, image)
                storage.upsert(to_row(
                    item, decision,
                    r2_key=full_key, thumb_key=thumb_key,
                    embedding=vector, embed_model=embedder.model_id,
                    width=image.width, height=image.height, mime=mime,
                    size_bytes=len(data),
                    attribution=gate.attribution_for(
                        decision.license, creator=item.creator,
                        title=item.title, source_url=item.source_url),
                ))
                stats.stored += 1
            except Exception as exc:
                stats.failed[f"store: {type(exc).__name__}"] += 1
        pending.clear()

    for item in source.harvest(limit):
        stats.harvested += 1

        # 1. Gate BEFORE fetching bytes.
        decision = gate.evaluate(
            license_url=item.license_url,
            license_text=item.license_text,
            explicit_license_id=item.explicit_license_id,
            raw=item.raw,
        )
        if not decision.allowed:
            stats.rejected[decision.reason] += 1
            continue

        stats.admitted += 1
        stats.licenses[decision.license_id] += 1
        if dry_run:
            continue

        # 2. Fetch. The adapter rate-limits its own API calls, but image bytes
        #    come from a different host (a CDN or upload server), so it pauses
        #    here too. These archives run on donations; we are a guest.
        try:
            time.sleep(FETCH_DELAY)
            response = session.get(item.file_url, timeout=60, stream=True)
            response.raise_for_status()
            mime = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if mime not in ACCEPTED_MIME:
                stats.failed[f"unsupported type: {mime or 'unknown'}"] += 1
                continue
            data = response.content
            if len(data) > MAX_BYTES:
                stats.failed["oversize"] += 1
                continue
        except Exception as exc:
            stats.failed[f"fetch: {type(exc).__name__}"] += 1
            continue

        # 3. Decode.
        image = load_image(data)
        if image is None:
            stats.failed["undecodable"] += 1
            continue

        pending.append((item, image, decision, data, mime))
        if len(pending) >= batch_size:
            flush()
            log(f"  stored {stats.stored} / admitted {stats.admitted} ...")

    flush()
    return stats


def report(stats: Stats, log: Callable[[str], None] = print) -> None:
    log("\n" + "=" * 62)
    log(f"harvested : {stats.harvested}")
    log(f"admitted  : {stats.admitted}"
        + (f"  ({stats.admitted / stats.harvested:.0%})" if stats.harvested else ""))
    log(f"stored    : {stats.stored}")

    if stats.licenses:
        log("\nadmitted by licence:")
        for name, count in stats.licenses.most_common():
            log(f"  {count:5d}  {name}")
    if stats.rejected:
        log("\nrejected by the gate:")
        for reason, count in stats.rejected.most_common(10):
            log(f"  {count:5d}  {reason[:80]}")
    if stats.failed:
        log("\nfailed after admission (not a licensing issue):")
        for reason, count in stats.failed.most_common(10):
            log(f"  {count:5d}  {reason[:80]}")
