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

    const rows = await searchItems({ embedding, limit, tier });

    return json(res, 200, {
      query,
      count: rows.length,
      results: rows.map((row) => presentItem(row)),
      // Stated per response rather than assumed: a caller comparing results
      // over time needs to know if the embedding model changed underneath them.
      model: MODEL_ID,
    });
  } catch (error) {
    console.error('search failed:', error);
    return fail(res, 500, 'search failed');
  }
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
