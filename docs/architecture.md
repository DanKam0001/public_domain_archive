# Architecture

## Shape

```
ingest/           runs LOCALLY, holds the only write credentials
  sources  →  LICENCE GATE  →  fetch  →  CLIP image tower  →  R2 + Postgres
              (fail closed)
                                    │
      ┌─────────────────────────────┴──────────────┐
      ▼                                            ▼
Supabase Postgres + pgvector                 Cloudflare R2
metadata · embeddings · RLS                  files · zero egress
      │                                            │
      └──────────────► Vercel ◄────────────────────┘
                  api/  read-only, anon key
                  web/  search UI
                        │
                        ▼
                  sdk/python
```

## The decisions that actually constrain everything

### Privilege is split by location, not by convention

Ingestion runs on a maintainer's machine and holds the service-role key. The
deployed API gets the anon key and read-only row-level security. This is why the
schema puts the takedown filter *inside* the `search_items` function rather than
leaving it to callers — the deployed surface should not be able to make a
mistake that matters.

Consequence: a credential leaked from the public repo, a bad PR, or a
compromised Vercel environment yields read access to data that is already public
by definition. There is nothing else to steal.

### The embedding model is pinned, and the pin is load-bearing

Text queries and image content share a vector space **only** when both come from
the same CLIP checkpoint. Mixing checkpoints raises no error — it returns
confident, plausible, wrong results.

This is the project's most dangerous silent failure mode, so it is defended three
ways:

1. `EMBED_MODEL` is set in one place and read by both sides.
2. `items.embed_model` is stored **per row**, so a model change becomes a
   detectable, migratable state rather than invisible corruption.
3. `tests/test_model_parity.py` asserts the Python and JavaScript encoders agree
   to cosine > 0.999 on identical text.

The same reasoning rules out every generic text-embedding API — OpenAI's
`text-embedding-3`, Cloudflare's `bge-*`, Cohere. They are cheaper and easier,
and they would silently destroy retrieval, because they are not in CLIP's space.

### Query-time embeddings run inside the Vercel function

Only CLIP's *text tower* is needed at query time (~63M params). The image tower
is ingest-only. A quantised ONNX text tower via Transformers.js fits inside
Vercel's 250MB unzipped limit, needs no additional vendor, account, or
credential, and costs nothing.

The tradeoff is a 1–3s cold start against ~30ms warm. Query→embedding results
are cached in Redis, so repeat queries never touch the model. If cold starts
prove unacceptable in practice, the same ONNX model lifts into a small always-on
service without an interface change.

### R2 rather than S3

R2 charges **zero egress**. For a free service whose entire function is serving
media downloads, this is the difference between viable and not. A popular 100 MB
clip downloaded 10,000 times costs roughly $90 on S3 and $0 here. That single
line determines whether Phase 2's video vertical can exist at all.

### Gate before fetch

The pipeline decides whether it may hold an item *before* downloading it. Cheaper,
and the correct posture: we should never possess a copy of something we were not
entitled to index.

### Idempotency everywhere

Object keys derive deterministically from `(source, source_id)`; the database
write upserts on the same natural key. A harvest that dies at item 700 of 1,000
is simply re-run. There is no dedupe pass, because there is nothing to dedupe.

## Hedge against the one unknown engineering can't fix

CLIP was trained on modern captioned web images. Public domain material is
disproportionately archival scans, engravings, and century-old photographs,
where CLIP degrades materially.

`items.fts` — a generated `tsvector` over title and description — exists from the
first migration for this reason. Archival material tends to carry unusually rich
catalogue text, precisely because librarians described it. If the evaluation in
`ingest/evaluate.py` shows vector search underperforming, hybrid retrieval is a
query change rather than a migration.

## What runs where

| Component | Runs | Credentials |
|---|---|---|
| `ingest/` | Local machine | Service role + R2 write |
| `api/` | Vercel serverless | Anon key, read-only |
| `web/` | Vercel static | None |
| `sdk/python/` | User's machine | Optional API key |

Vercel Hobby caps at 12 serverless functions per deployment. Phase 1 uses 4.
Worth knowing before it becomes a deploy error.

## Deployment notes (learned the hard way)

- **`vercel.json` rejects unknown keys.** No `_comment` fields — the deploy
  fails with `should NOT have additional property`. Config commentary belongs
  here instead.
- **Hobby plan caps function memory at 2048 MB.** Anything higher is rejected at
  deploy time with `invalid_function_memory`. `api/search.js` sits at the
  ceiling because a cold container loads the fp16 CLIP text tower.
- **New Vercel projects default to SSO deployment protection ON**
  (`ssoProtection: {"deploymentType": "all_except_custom_domains"}`), which puts
  a login wall in front of every `.vercel.app` URL. This project is a public
  archive with no auth wall by design, so it is disabled deliberately.
- **`rootDirectory` must match where the app lives.** It is correctly `null`
  here because the app is at the repo root; on a subfolder app, leaving it null
  makes deploys report READY in seconds having built nothing at all.
