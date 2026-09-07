"""Search quality evaluation — the Phase 1 go/no-go gate.

    python -m ingest.evaluate

Why this exists before the API
------------------------------
CLIP was trained on modern captioned web images. Public domain material is
disproportionately *not* that: archival scans, engravings, microfilm,
century-old photographs. Retrieval quality on this corpus is the single
assumption in the whole project that engineering cannot fix downstream, and it
is unknown until measured.

So it gets measured here, on the real ingested corpus, before an API, a
frontend, and an SDK are built on top of it. If quality is poor, the cheap
response is hybrid retrieval — the ``fts`` column already exists in the schema
for exactly this — and it is far cheaper to learn that now than after five
things depend on the current approach.

What it reports
---------------
There is no labelled ground truth, so this does not pretend to compute recall.
It reports what can be measured honestly:

* **score distribution** — CLIP cosine similarities are not calibrated, so the
  absolute number means little; the *spread* between a query's best and median
  match is what indicates whether the ranking is discriminating at all.
* **a flat-result warning** — if the top hit barely beats the median, the model
  is not distinguishing anything and the ranking is close to arbitrary.
* **the actual top hits per query**, printed, so a human can judge relevance.
  For a corpus this size that human judgement is the ground truth.
"""

from __future__ import annotations

import os
import statistics

import psycopg
from dotenv import load_dotenv

#: Deliberately mixed. Concrete nouns are CLIP's best case; abstract and
#: compositional queries are where it degrades, and archival subject matter is
#: where this specific corpus is most likely to disappoint. A query set of only
#: easy cases would flatter the model and teach us nothing.
EVAL_QUERIES = [
    # concrete objects — the easy case
    "a red flower", "a wooden bridge", "a steam locomotive", "a cat",
    # scenes
    "snow covered mountains", "a busy city street at night", "a sandy desert",
    "waves crashing on rocks",
    # people
    "a portrait of an old man", "children playing outdoors",
    # archival / historical — the hard case for CLIP
    "a black and white photograph of a factory", "an antique map",
    "a handwritten manuscript page", "a vintage advertisement poster",
    "an old newspaper front page",
    # abstract / compositional
    "something lonely", "a feeling of celebration", "aerial view of farmland",
    "close up of texture", "two people talking",
]

TOP_K = 5
#: Below this ratio of (top score - median score), the ranking is not really
#: discriminating between relevant and irrelevant items.
FLAT_THRESHOLD = 0.04


def main() -> int:
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

    from .pipeline.embed import Embedder

    embedder = Embedder()
    dsn = os.environ["POSTGRES_DSN"]

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from items where embedding is not null")
        total = cur.fetchone()[0]
        print(f"corpus: {total} embedded items")
        print(f"model : {embedder.model_id}")
        print(f"queries: {len(EVAL_QUERIES)}\n")

        spreads: list[float] = []
        flat: list[str] = []

        for query in EVAL_QUERIES:
            vector = embedder.embed_text([query])[0]
            cur.execute(
                """
                select title, source, license_id,
                       1 - (embedding <=> %s::vector) as similarity
                from items
                where takedown = false and embedding is not null
                order by embedding <=> %s::vector
                limit %s
                """,
                (str(vector), str(vector), TOP_K),
            )
            rows = cur.fetchall()

            # Median across a sample of the corpus, as the "random item" baseline.
            cur.execute(
                """
                select 1 - (embedding <=> %s::vector)
                from items where embedding is not null limit 400
                """,
                (str(vector),),
            )
            baseline = statistics.median([r[0] for r in cur.fetchall()])

            top = rows[0][3] if rows else 0.0
            spread = top - baseline
            spreads.append(spread)
            marker = ""
            if spread < FLAT_THRESHOLD:
                flat.append(query)
                marker = "   <-- FLAT: barely beats a random item"

            print(f'"{query}"')
            print(f"  top {top:.3f} | corpus median {baseline:.3f} | "
                  f"spread {spread:+.3f}{marker}")
            for title, source, license_id, similarity in rows:
                label = (title or "(untitled)")[:64]
                print(f"    {similarity:.3f}  {label}  [{source}/{license_id}]")
            print()

        print("=" * 62)
        print(f"mean spread over corpus median : {statistics.mean(spreads):+.3f}")
        print(f"weakest query spread           : {min(spreads):+.3f}")
        print(f"queries with a flat result set : {len(flat)}/{len(EVAL_QUERIES)}")
        if flat:
            print("  " + "; ".join(flat))

        print("\nRead the titles above, not just the numbers. CLIP similarity is "
              "uncalibrated,\nso a high score on an irrelevant image is exactly "
              "the failure mode to look for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
