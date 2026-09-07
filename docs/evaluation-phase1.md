# Phase 1 search quality evaluation

**Date:** 2026-09-07 · **Corpus:** 1,038 items · **Model:** `openai/clip-vit-base-patch32`
**Reproduce:** `python -m ingest.evaluate`

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

The query set deliberately mixes easy concrete nouns, scenes, people, archival
subjects, and abstract prompts. A set of only easy queries would have flattered
the model and taught us nothing.
