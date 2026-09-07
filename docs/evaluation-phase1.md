# Phase 1 search quality evaluation

**Date:** 2026-09-08 · **Corpus:** 966 items · **Model:** `openai/clip-vit-base-patch32`
**Reproduce:** `python -m ingest.evaluate`

> **Re-run on an unbiased corpus.** The original evaluation below was measured on
> an alphabetically clustered sample (see "Alphabetical sampling bias"). That
> sampler is fixed and the corpus was rebuilt from scratch. Headline numbers on
> the fair draw:
>
> | | Biased corpus (1,038) | Fair corpus (966) |
> |---|---|---|
> | Mean spread over median | +0.113 | **+0.119** |
> | Weakest query | +0.062 | +0.048 |
> | Flat result sets | 0/20 | **0/20** |
> | Redundant duplicates | 28% | **4%** |
>
> **The verdict survives a fair sample, and slightly improves.** That was not the
> expected result — the old sample's heavy museum-and-manuscript skew looked like
> it should have flattered CLIP. It did not: the fair corpus separates queries
> marginally better across the board, while the weakest query gets slightly
> weaker.
>
> Retrieval on individual queries shifted with the content, as it should.
> "a cat" improved (+0.076 → +0.083) and now returns animal studies and a
> spectacled bear rather than Persian miniatures of lions. "a busy city street at
> night" fell (+0.110 → +0.085) and its top five are one near-identical cluster —
> which the API suppresses at query time but this harness does not (see Method).

This was the Phase 1 go/no-go gate: is CLIP retrieval good enough on *public
domain* material to be worth building an API, a frontend, and an SDK on top of?

## Verdict: GO

| Metric | Result |
|---|---|
| Queries with a flat result set | **0 / 20** |
| Mean spread over corpus median | **+0.113** |
| Weakest query spread | **+0.062** (still ~1.5× the flat threshold) |

Every one of 20 queries retrieved semantically relevant results, and the
weakest still separated its top hit from a random item by well over the
threshold at which ranking becomes arbitrary.

## The predicted risk did not materialise

The plan named CLIP-on-archival-material as the sleeper risk: CLIP is trained on
modern captioned web images, and public domain material skews to scans,
engravings, and century-old photographs.

**That prediction was wrong, and in the most useful direction.** The archival
queries were among the *strongest* in the set:

| Query | Spread | Top results |
|---|---|---|
| "a black and white photograph of a factory" | **+0.191** | Australian factory c.1900s; Ford Motor Factory 1967; GM Holden 1967 |
| "a handwritten manuscript page" | **+0.165** | Akbarnama folio; Hamla-yi Haidari folio; Shahnama folio |
| "an antique map" | **+0.113** | Illuminated manuscript coastline maps; Aikin (1800) county plates |

Best guess as to why: museums and libraries caption their scans richly and
photograph them consistently, and CLIP's training data contains a great deal of
museum and archive imagery. Whatever the cause, the empirical answer is that
this corpus is well within CLIP's competence.

The `fts` column stays in the schema as the hybrid-retrieval hedge, but on this
evidence it is not needed for Phase 1.

## Where it *is* weak

Abstract and emotional queries score lowest — "a feeling of celebration"
(+0.062), "something lonely" (+0.067). This is CLIP behaving as documented
rather than a corpus problem, and it matters for the eventual `auto_broll`
feature, where a script line like *"and everything fell apart"* is exactly this
kind of query. Phase 3 will likely need an LLM to rewrite abstract narration
into concrete visual descriptions before retrieval. Worth knowing now.

## Two real corpus problems found

### 1. Near-duplicate flooding — 28% of the corpus

**292 of 1,038 rows are redundant near-duplicates** (116 titles appear more than
once). Worst cases:

```
14x  "Battle Between Iranians and Turanians", Folio from a Shahnama
11x  "Bizhan Slaughters the Wild Boars of Irman", Folio from a Shahnama
10x  "Abundance" Textile
10x  "Bellini" carpet
```

This is visible directly in the results: "close up of texture" returned five
copies of the same textile, filling the entire first page with one object. A
results grid that shows the same item five times is broken as a product, no
matter how good the ranking is.

Fix is a query-time or ingest-time concern, not a model one — either group
near-identical embeddings before returning results, or de-duplicate on
`(title, creator)` at ingest. Query-time diversification is preferable: the
duplicates are often legitimately distinct items (different folios of one
manuscript), so discarding them at ingest loses real content.

### 2. Alphabetical sampling bias

The Wikimedia adapter walks a category sequentially from the start, and
MediaWiki orders category members alphabetically. The corpus is therefore
clustered near the beginning of the alphabet — a large run of Met Museum
artworks whose titles begin with a quotation mark, then A, then B.

Corpus concentration by creator shows it plainly:

```
51  Ferdowsi
32  Bernard Spragg
28  Walters Art Museum Illuminated Manuscripts
25  YellowstoneNPS
```

**This means the evaluation above is measured on an unrepresentative sample.**
The verdict holds — the queries that worked span factories, maps, mountains,
trains, and coastlines, so the model is clearly not overfitting to manuscripts
— but the corpus is not a fair draw from Commons, and a real deployment needs
randomised or sharded traversal (`gcmstartsortkey`, or sampling many categories).

Related: several queries failed for pure coverage reasons rather than retrieval
ones. "a cat" returned Persian miniatures of lions; "a busy city street at
night" returned Sydney Harbour Bridge. There are no cats and no night streets in
1,038 alphabetically-clustered items, so CLIP correctly returned the nearest
thing that exists. At this corpus size, a bad result usually means "nothing
relevant was ingested," not "retrieval is broken."

## Both problems above were subsequently fixed

### Sampling bias — fixed by prefix sharding

Three traversal strategies were measured against the live API:

| Strategy | Result |
|---|---|
| `generator=random` | Uniform, but cannot combine with a category filter — most results carry licences the gate rejects, so the requests are wasted |
| `gcmsort=timestamp` | Category membership is lumpy in time; most shards landed in sparse stretches. ~1.3 new items per shard, and a 200-item request returned 121 |
| **`gcmstartsortkeyprefix`** | **Chosen.** 20 shards returned 1,000 items with zero empty shards |

Prefix sharding also attacks the bias directly, because each prefix bucket
contributes equally regardless of how many items sit under it — so one bulk
upload owning the front of the alphabet stops mattering.

| Measured over 200 items | Sequential | Sharded |
|---|---|---|
| Top title initial | 180 (90%) | 20 (10%) |
| Distinct initials | 6 | 20 |
| Top creator | 35 (17%) | 10 (5%) |
| Redundant duplicates | 22% | 0% |

### Abstract queries — fixed by opt-in query expansion

Rewriting abstract phrasing into concrete photographable scenes before
embedding (`api/_lib/expand.mjs`):

| Query | Before | After |
|---|---|---|
| the passage of time | +0.039 | **+0.187** |
| a sense of freedom | +0.055 | **+0.151** |
| something lonely | +0.067 | **+0.160** |
| two people talking | +0.088 | **+0.156** |
| a feeling of celebration | +0.062 | **+0.102** |

Mean gain +0.089, every query improved, and expanded results now score *above*
the concrete-query average of +0.113 — the weakest category becomes among the
strongest.

Opt-in rather than automatic: measured at ~$0.005 per expansion, which at a
thousand searches a day would be ~$144/month against a $25 total bill. It is
cached indefinitely, separately rate-limited, and degrades to unexpanded results
when unavailable.

## Method and its limits

There is no labelled ground truth, so this deliberately does not claim recall or
precision. It reports:

* **spread** — a query's top score minus the median score across a 400-item
  sample. CLIP similarities are uncalibrated, so absolute values mean little;
  the gap is what shows whether ranking discriminates.
* **flat-result detection** — a top hit that barely beats the median means the
  ranking is close to arbitrary.
* **the actual titles**, printed, because at this corpus size human judgement is
  the only real ground truth.

**This harness queries the table directly, not through `search_items`.** So it
measures *raw* retrieval and does not benefit from the duplicate suppression the
API applies — which is why a near-identical cluster can occupy a whole result
list here while the live API would not show it. That is deliberate: measuring
the retrieval layer and the presentation layer separately keeps a regression in
one from being masked by the other.

The query set deliberately mixes easy concrete nouns, scenes, people, archival
subjects, and abstract prompts. A set of only easy queries would have flattered
the model and taught us nothing.
