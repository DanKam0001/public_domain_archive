# public_domain_archive

**Free, open, programmable search over media that is genuinely in the public domain.**

Paid stock sites resell public domain material. They add no rights you don't
already have — what they actually sell is *findability*: a search index, a clean
download, an API. The content is free; the convenience is not.

This project removes that gap. Everything here is open source, self-hosted, and
funded by donations.

**Live: https://public-domain-archive.vercel.app**

> **Status: Phase 1 complete.** 1,038 images ingested, search API and site live,
> Python SDK working. This README describes what exists today, not an
> aspiration. The roadmap is at the bottom; the reasoning behind the
> architecture is in [docs/](docs/).

```bash
pip install -e sdk/python
pda search "snow covered mountains" --download ./footage
```

```python
from public_domain_archive import Client

archive = Client()
for item in archive.search("an antique map", limit=5):
    print(item.title, item.license.id, item.source_url)
```

---

## The thing that makes this different

**Every item is licence-verified per item, automatically, before it enters the
catalog — and anything unverifiable is excluded.**

That sounds like a detail. It is the entire product. The reason is who uses this:
not primarily a human browsing for a photo, but *automated pipelines* — AI video
channels, content tooling, scripts calling an API with no human reviewing the
results. Those callers cannot check rights per item, and they monetise. If a
catalog serves them something restrictive while implying it's cleared, the
catalog is the party vouching for a false claim.

So the rule is mechanical and has no discretionary branch:

| Signal | Outcome |
|---|---|
| Positively matched allowlisted licence (CC0, Public Domain Mark, US federal work) | **admitted** |
| Licence missing | **rejected** |
| Licence unrecognised | **rejected** |
| Licence restrictive (NC / ND / SA / in-copyright) | **rejected** |
| Signals conflict | **rejected** |

**Why this isn't paranoia.** While planning this project we sampled
archive.org's `nasa` collection — 188,766 images, and about as safe an
assumption of "public domain" as exists. Of 12 items sampled at random, **11 had
no licence metadata of any kind**. The one that did was **CC BY-NC 2.0 —
NonCommercial** — a mirrored Flickr stream sitting inside the collection
everyone assumes is uniformly free.

Collection membership is not evidence of a licence. Only a per-item, positively
matched licence is. That single finding shaped the whole architecture.

## What we do *not* claim

We are an index pointing at a claim, not a warrantor of that claim. The UI says
*"Source states: CC0 — verify at [link]"*, never *"this is public domain."* Every
item links back to its origin so you can verify it yourself, we store the
source's original claim verbatim as evidence, and there is a takedown path for
when a source turns out to be wrong.

Public domain is also **jurisdiction-specific**. US public domain is not EU
public domain. Our determinations are US-based. See
[docs/licensing-policy.md](docs/licensing-policy.md).

*This is not legal advice.*

---

## Architecture

```
  ingest/          runs LOCALLY, holds write credentials
    sources  →  LICENCE GATE  →  CLIP image embedding  →  R2 + Postgres
                (fail closed)
                                       │
        ┌──────────────────────────────┴──────────────┐
        ▼                                             ▼
  Supabase Postgres + pgvector                  Cloudflare R2
  metadata, embeddings, RLS                     files, zero egress fees
        │                                             │
        └──────────────► Vercel ◄─────────────────────┘
                    api/ (read-only, anon key)
                    web/ (search UI)
                          │
                          ▼
                    sdk/python  →  search() · download() · CLI
```

**The credential rule that shapes everything:** the deployed, public surface
never holds a privileged credential. Writes happen only during local ingestion.
The API gets a read-only key constrained by row-level security.

**Why R2 and not S3:** R2 charges **zero egress**. For a free service whose whole
job is serving media downloads, that is the difference between viable and not —
a popular 100 MB clip downloaded 10,000 times costs ~$90 on S3 and $0 here.

**Why the embedding model is pinned:** text queries and image content only share
a vector space if they came from the *same* CLIP model. Mixing models doesn't
error — it silently returns confident nonsense. `tests/test_model_parity.py`
asserts the Python and JavaScript sides agree to a cosine similarity > 0.999.

## Layout

| Path | What it is |
|---|---|
| `ingest/` | Python pipeline: sources → licence gate → embeddings → storage |
| `ingest/licensing/` | **The gate.** `licenses.yaml` is the allowlist; `gate.py` is the logic |
| `api/` | Vercel serverless routes |
| `public/` | Frontend (Vercel's static root) |
| `sdk/python/` | `pip`-installable client + `pda` CLI |
| `db/migrations/` | Postgres + pgvector schema |
| `docs/` | Architecture, licensing policy, costs |

## Setup

```bash
git clone https://github.com/DanKam0001/public_domain_archive
cd public_domain_archive

python -m venv .venv
source .venv/Scripts/activate      # Windows; use .venv/bin/activate elsewhere
pip install -r ingest/requirements.txt

cp .env.example .env               # then fill it in
pytest tests/ -q
```

You do **not** need credentials to run the licence-gate tests — they are pure
logic and are the best place to start reading.

## Roadmap

| Phase | Scope |
|---|---|
| **1** *(current)* | Images. Ingest → search API → web UI → SDK skeleton |
| 2 | Video, with shot detection and clip-level retrieval |
| 3 | `auto_broll(script)` — hand it a narration script, get back matched clips |
| 4 | Audio, books, newspapers |
| 5 | Scale, caching, donations |

Phase 3 is the point of the whole thing. Phases 1 and 2 build the substrate that
makes it possible.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Changes to `ingest/licensing/` are
security-relevant and reviewed accordingly.

## Licence

[AGPL-3.0](LICENSE). Chosen deliberately: a permissive licence would allow
exactly the thing this project exists to oppose — someone running a hosted copy
and charging for access without contributing back. AGPL closes the
network-service loophole.
