/**
 * POST /api/search
 *
 *   { "query": "snow covered mountains", "limit": 24, "tier": "public_domain" }
 *
 * Text query -> CLIP text embedding -> pgvector similarity -> ranked results
 * with duplicate suppression (see db/migrations/002).
 */

import { embedText, toVectorLiteral, MODEL_ID } from './_lib/embed.mjs';
import { searchItems } from './_lib/db.mjs';
import { expandQuery, expansionAvailable } from './_lib/expand.mjs';
import { cached, cors, fail, json, presentItem, rateLimit } from './_lib/http.mjs';

const MAX_LIMIT = 100;
const MAX_QUERY_CHARS = 300;
const VALID_TIERS = new Set(['public_domain', 'attribution']);

export default async function handler(req, res) {
  cors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return fail(res, 405, 'use POST');

  const limits = await rateLimit(req, { limit: 60, window: 60 });
  res.setHeader('X-RateLimit-Remaining', String(limits.remaining));
  if (!limits.ok) return fail(res, 429, 'rate limit exceeded — try again shortly');

  const body = typeof req.body === 'string' ? safeParse(req.body) : req.body || {};
  const query = (body.query || '').trim();

  if (!query) return fail(res, 400, 'query is required');
  if (query.length > MAX_QUERY_CHARS) {
    return fail(res, 400, `query must be under ${MAX_QUERY_CHARS} characters`);
  }

  const limit = clamp(parseInt(body.limit, 10) || 24, 1, MAX_LIMIT);
  const tier = body.tier ?? null;
  // Off by default: expansion costs a model call (~$0.005). Callers feeding
  // narration lines want it; a casual web search does not. See _lib/expand.mjs.
  const expand = body.expand === true;
  if (tier !== null && !VALID_TIERS.has(tier)) {
    return fail(res, 400, `tier must be one of: ${[...VALID_TIERS].join(', ')}`);
  }

  try {
    // Cached on the normalised query, so a repeated search never loads the
    // model — which is what keeps the cold-start cost from mattering in
    // practice.
    const embedding = await cached(
      `${MODEL_ID}:${query.toLowerCase()}`,
      async () => toVectorLiteral((await embedText([query]))[0]),
    );

    let rows = await searchItems({ embedding, limit, tier });
    let variants = [];

    if (expand && expansionAvailable()) {
      // Expansion is the only operation here that spends real money per call,
      // on a public unauthenticated endpoint. The ordinary 60/min search limit
      // would permit ~$430/day of model spend from one IP. This bucket is
      // separate and far tighter, and a caller who exceeds it still gets
      // results — just unexpanded — because degrading is better than failing.
      //
      // The proper fix is to gate expansion behind an issued API key once key
      // issuance exists; this bounds the exposure until then.
      const budget = await rateLimit(req, { limit: 10, window: 3600 });
      if (budget.ok) {
        variants = await expandQuery(query);
        if (variants.length) {
          rows = await searchExpanded({ query, embedding, variants, limit, tier });
        }
      }
    }

    return json(res, 200, {
      query,
      count: rows.length,
      results: rows.map((row) => presentItem(row)),
      // Returned so a caller can see what was actually searched. Expansion
      // changes the results materially; hiding it would make them
      // inexplicable.
      expanded: variants.length ? variants : undefined,
      // Stated per response rather than assumed: a caller comparing results
      // over time needs to know if the embedding model changed underneath them.
      model: MODEL_ID,
    });
  } catch (error) {
    console.error('search failed:', error);
    return fail(res, 500, 'search failed');
  }
}

/**
 * Search the original query plus every rewrite, and keep each item's BEST score.
 *
 * Max-similarity rather than rank fusion, deliberately. Reciprocal rank fusion
 * rewards items that place moderately well across many variants, which for five
 * descriptions of the same idea surfaces bland middle-ranking images. Taking the
 * maximum keeps the item that one variant matched *strongly* — the whole point
 * of generating varied concrete scenes — and keeps `similarity` meaning the same
 * thing it does on an unexpanded search.
 */
async function searchExpanded({ query, embedding, variants, limit, tier }) {
  const embeddings = await embedText(variants);
  const searches = [
    searchItems({ embedding, limit, tier }),
    ...embeddings.map((vector) =>
      searchItems({ embedding: toVectorLiteral(vector), limit, tier })),
  ];

  const best = new Map();
  for (const rows of await Promise.all(searches)) {
    for (const row of rows) {
      const existing = best.get(row.id);
      if (!existing || row.similarity > existing.similarity) best.set(row.id, row);
    }
  }
  return [...best.values()]
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, limit);
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function safeParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}
