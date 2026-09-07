"""Ingestion CLI.

    python -m ingest.run --source wikimedia_commons --limit 50 --dry-run

``--dry-run`` harvests and runs the licence gate but writes nothing. It is the
honest way to see what a source actually offers before committing storage to it,
and it prints the rejection reasons in full — a source whose items are mostly
rejected is information, not a failure.

Storage (R2 upload, embedding, database write) lands in the next step; until
then this runs dry regardless of the flag.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

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
                        help="harvest and gate, but write nothing")
    parser.add_argument("--tiers", default=",".join(sorted(DEFAULT_ACCEPTED_TIERS)),
                        help="comma-separated licence tiers to accept "
                             "(public_domain, attribution)")
    parser.add_argument("--show-rejects", type=int, default=5,
                        help="print this many example rejections per reason")
    args = parser.parse_args(argv)

    user_agent = os.environ.get("INGEST_USER_AGENT", DEFAULT_UA)
    gate = LicenseGate(accepted_tiers=set(args.tiers.split(",")))
    source = SOURCES[args.source](user_agent=user_agent)

    print(f"harvesting up to {args.limit} from {args.source}")
    print(f"accepting tiers: {sorted(gate._accepted_tiers)}\n")

    admitted = 0
    reasons: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    licences: collections.Counter[str] = collections.Counter()

    for item in source.harvest(args.limit):
        decision = gate.evaluate(
            license_url=item.license_url,
            license_text=item.license_text,
            explicit_license_id=item.explicit_license_id,
            raw=item.raw,
        )

        if decision.allowed:
            admitted += 1
            licences[decision.license_id] += 1
        else:
            key = decision.reason.split(":")[0] + ": " + decision.reason.split(": ", 1)[-1]
            reasons[key] += 1
            if len(examples[key]) < args.show_rejects:
                examples[key].append(f"{item.source_id} — {(item.title or '')[:60]}")

    total = admitted + sum(reasons.values())
    print(f"\n{'=' * 62}")
    print(f"harvested : {total}")
    print(f"admitted  : {admitted}"
          + (f"  ({admitted / total:.0%})" if total else ""))
    print(f"rejected  : {total - admitted}")

    if licences:
        print("\nadmitted by licence:")
        for lic, count in licences.most_common():
            print(f"  {count:5d}  {lic}")

    if reasons:
        print("\nrejected by reason:")
        for reason, count in reasons.most_common():
            print(f"  {count:5d}  {reason}")
            for example in examples[reason]:
                print(f"           e.g. {example}")

    skipped = getattr(source, "skipped_restricted", 0)
    if skipped:
        print(f"\nalso skipped before gating: {skipped} item(s) flagged with "
              f"non-copyright restrictions (trademark / personality rights)")

    if not args.dry_run:
        print("\n[!] storage not implemented yet — this run wrote nothing.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
