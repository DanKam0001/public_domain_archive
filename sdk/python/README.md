# public-domain-archive

Python client for the [Public Domain Archive](https://public-domain-archive.vercel.app) —
search and download media that is genuinely in the public domain.

```bash
pip install public-domain-archive
```

No API key needed to search or download. One only raises rate limits.

## Why you might want this

Stock sites resell public domain material and charge for the search index. This
is that index, free and scriptable.

The part that matters if you are automating: **every item was licence-verified
individually before it entered the catalog.** Missing, ambiguous, or restrictive
licensing means the item is excluded, not flagged. So a script can use what it
gets back without checking rights per item — which is exactly what a script
cannot do.

## Use

```python
from public_domain_archive import Client

archive = Client()

for item in archive.search("snow covered mountains", limit=5):
    print(item.title, item.license.id, item.source_url)

# one call: search, download, and write a credits file
pairs = archive.search_and_download("a steam locomotive", "./footage", limit=5)

# visually similar
for item in archive.similar(pairs[0][0]):
    print(item.title)
```

### Attribution

Attribution travels with the item, because a script cannot notice a credit it
forgot to render:

```python
item.requires_attribution   # bool
item.credit()               # the exact line to reproduce, or None
Client.write_credits(items, "./footage/CREDITS.txt")
```

`credit()` returns `None` — not `""` — when no credit is required, so "no
attribution needed" is distinguishable from "attribution unknown".

Want zero obligations at all? Filter to the public domain tier:

```python
archive.search("a red flower", tier="public_domain")
```

## CLI

```bash
pda search "snow covered mountains"
pda search "a steam locomotive" --download ./footage --limit 5
pda get <item-id>
pda similar <item-id>
pda search "an antique map" --json      # for scripts
```

## Notes

**No runtime dependencies.** Stdlib only, so dropping it into an existing media
pipeline cannot conflict with whatever that pipeline already pins.

**The first call may take ~15 seconds.** The search model loads on a cold
container; subsequent calls are fast. The default timeout is 60s for this
reason. 429s and 5xx are retried automatically with backoff.

**We are an index, not a warrantor.** `license.statement` reports what the
source claimed — "Source states: CC0-1.0. Verify at …" — and every item carries
`source_url` so you can check independently. Public domain status is
jurisdiction-specific; determinations here are US-based.

Found something that shouldn't be in the catalog? Please
[report it](https://github.com/DanKam0001/public_domain_archive/issues/new).

## Licence

AGPL-3.0-or-later.
