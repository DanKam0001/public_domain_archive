"""Ingestion CLI.

    python -m ingest.run --source wikimedia_commons --limit 500
    python -m ingest.run --source openverse --limit 200 --dry-run

``--dry-run`` harvests and gates but downloads, embeds, and stores nothing. It
is the honest way to see what a source actually offers before spending
bandwidth on it, and it needs no credentials.

A real run needs ``.env`` (see ``.env.example``). Credentials are write-scoped
and never leave this machine.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from .licensing.gate import DEFAULT_ACCEPTED_TIERS, LicenseGate
from .sources import SOURCES

DEFAULT_UA = (
    "public_domain_archive/0.1 "
    "(+https://github.com/DanKam0001/public_domain_archive)"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest.run")
    parser.add_argument("--source", required=True, choices=sorted(SOURCES))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true",
                        help="harvest and gate only; no download, embed, or write")
    parser.add_argument("--tiers", default=",".join(sorted(DEFAULT_ACCEPTED_TIERS)),
                        help="licence tiers to accept: public_domain, attribution")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args(argv)

    load_dotenv()

    # Imported after load_dotenv so the pinned model id is read from .env.
    from .pipeline.runner import report, run

    gate = LicenseGate(accepted_tiers=set(args.tiers.split(",")))
    source = SOURCES[args.source](
        user_agent=os.environ.get("INGEST_USER_AGENT", DEFAULT_UA)
    )

    print(f"source        : {args.source}")
    print(f"limit         : {args.limit}")
    print(f"accepted tiers: {sorted(gate._accepted_tiers)}")
    print(f"mode          : {'DRY RUN (no writes)' if args.dry_run else 'live'}\n")

    stats = run(source, gate, args.limit, dry_run=args.dry_run,
                batch_size=args.batch_size)
    report(stats)

    skipped = getattr(source, "skipped_restricted", 0)
    if skipped:
        print(f"\nalso skipped before gating: {skipped} item(s) flagged with "
              f"non-copyright restrictions (trademark / personality rights)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
