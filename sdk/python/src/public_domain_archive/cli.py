"""Command line interface.

    pda search "snow covered mountains"
    pda search "a steam locomotive" --download ./footage --limit 5
    pda get <item-id>
    pda similar <item-id>

Output is human-readable by default and JSON with ``--json``, so the same
command works when you are exploring the catalog and when a script is consuming
it.
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import Client
from .errors import ArchiveError
from .models import Item


def main(argv: list[str] | None = None) -> int:
    # Archive titles carry arbitrary Unicode and Windows consoles default to
    # cp1252, which otherwise raises partway through printing results.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="pda",
        description="Search and download genuinely public domain media.",
    )
    parser.add_argument("--base-url", help="override the archive URL")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="search by description")
    search.add_argument("query")
    search.add_argument("-n", "--limit", type=int, default=10)
    search.add_argument(
        "--tier", choices=["public_domain", "attribution"],
        help="public_domain returns only items with no conditions on reuse",
    )
    search.add_argument("-d", "--download", metavar="DIR",
                        help="download results here and write CREDITS.txt")
    search.add_argument("-x", "--expand", action="store_true",
                        help="rewrite abstract phrasing into concrete visual "
                             "descriptions first -- use for narration lines")

    get = sub.add_parser("get", help="full metadata for one item")
    get.add_argument("item_id")

    similar = sub.add_parser("similar", help="visually similar items")
    similar.add_argument("item_id")
    similar.add_argument("-n", "--limit", type=int, default=10)

    args = parser.parse_args(argv)
    client = Client(base_url=args.base_url)

    try:
        if args.command == "search":
            return _search(client, args)
        if args.command == "get":
            item = client.get(args.item_id)
            return _emit([item], args.json, detailed=True)
        if args.command == "similar":
            return _emit(client.similar(args.item_id, limit=args.limit), args.json)
    except ArchiveError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


def _search(client: Client, args) -> int:
    if args.download:
        pairs = client.search_and_download(
            args.query, args.download, limit=args.limit, tier=args.tier,
            expand=args.expand,
        )
        if args.json:
            print(json.dumps(
                [{"item": item.raw, "path": str(path)} for item, path in pairs], indent=2))
        else:
            for item, path in pairs:
                print(f"  {path}  {item}")
            print(f"\n{len(pairs)} file(s) -> {args.download}")
            print(f"credits written to {args.download}/CREDITS.txt")
        return 0

    return _emit(client.search(args.query, limit=args.limit, tier=args.tier,
                               expand=args.expand), args.json)


def _emit(items: list[Item], as_json: bool, detailed: bool = False) -> int:
    if as_json:
        print(json.dumps([item.raw for item in items], indent=2))
        return 0

    if not items:
        print("no results")
        return 0

    for item in items:
        score = f"{item.similarity:.3f}  " if item.similarity is not None else ""
        print(f"{score}{item.title or '(untitled)'}")
        print(f"        id      : {item.id}")
        print(f"        licence : {item.license.id}"
              + ("  (credit required)" if item.requires_attribution else "  (no conditions)"))
        # Printed on every result, not hidden behind a flag: the whole point is
        # that a claim is verifiable, and a link nobody sees is not.
        print(f"        source  : {item.source_url}")
        if detailed:
            if item.attribution:
                print(f"        credit  : {item.attribution}")
            print(f"        {item.license.statement}")
            if item.width:
                print(f"        size    : {item.width} x {item.height}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
