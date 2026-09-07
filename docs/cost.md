# What this costs to run

Written from measured numbers, updated as we learn. A donation-funded project
should be able to state its own running cost precisely.

## Phase 1 (~1,000 images)

| Service | Usage | Cost |
|---|---|---|
| Supabase | 1,000 rows. 512-d vectors are 2KB each ≈ **2MB** of vector data | Free tier covers it |
| Cloudflare R2 | ~250MB of renditions + thumbnails (10GB free) · **zero egress** | $0 |
| Vercel | Hobby. 4 serverless functions of 12 allowed | $0 |
| Embeddings | Image tower local, text tower inside the API function | $0 |
| Upstash Redis | Query cache + rate limiting, free tier | $0 |

The database and storage are effectively free at this scale. The only reason
this project has a bill at all is the Supabase plan chosen for project-slot
reasons, not for anything Phase 1 technically needs.

## Why storage is small: we store renditions, not originals

Measured on a real Wikimedia sample: originals average **2.7MB** and reach 9MB+,
projecting to ~1.9GB for a 700-item harvest. We fetch 1280px renditions instead
— **254MB for the same 700 items, 7.4× less** — because CLIP resizes everything
to 224px before looking at it, so the extra pixels buy nothing for retrieval.

This is a product tradeoff, not a free win: the file we serve is
web-resolution, and a user wanting true full resolution follows `source_url` to
the original. Revisit it if full-res download becomes part of the value; the
cost then lands in R2 *storage*, which is cheap, rather than egress, which on
most providers is not.

## Phase 2 is where cost becomes real

Video changes the arithmetic completely.

- R2 storage past the 10GB free tier: **$0.015/GB/month**. 1TB ≈ $15/month.
- R2 egress: **$0**, unmetered.

That second line is the entire reason this project is financially viable. A
popular 100MB clip downloaded 10,000 times is 1TB of egress:

| Provider | Egress cost for that one clip |
|---|---|
| **Cloudflare R2** | **$0** |
| AWS S3 | ~$90 |
| Google Cloud Storage | ~$120 |

A free service with no revenue scaling alongside usage cannot absorb per-GB
egress. Every other architectural choice here is negotiable; this one is not.

## Things that would change the picture

- **Vercel Hobby is non-commercial per their ToS.** A donation-funded open
  source project is a grey area they have flagged for others before. Pro is
  $20/month if it becomes an issue — worth resolving before promoting the site
  widely rather than after.
- **Supabase free-tier projects pause after 7 days idle.** Fine for a busy
  site, awkward during development.
- **pgvector at scale.** Exact scan is instant at 1,000 rows. HNSW index build
  memory becomes the real constraint somewhere past ~1M vectors, which is a
  compute-tier decision rather than an architecture change.
- **Openverse anonymous API limits** are 200 requests/day and cap page size at
  20. Fine for Phase 1; a free registered key lifts both before any large
  harvest.
