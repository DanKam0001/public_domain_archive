/**
 * GET /api/item/{id}/similar
 *
 * Visual nearest neighbours, using the item's own stored embedding rather than
 * re-encoding anything — this is why the vector lives in the row.
 */

import { getItemEmbedding, searchItems } from '../../_lib/db.mjs';
import { cors, fail, json, presentItem, rateLimit } from '../../_lib/http.mjs';

export default async function handler(req, res) {
  cors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'GET') return fail(res, 405, 'use GET');

  const limits = await rateLimit(req, { limit: 60, window: 60 });
  if (!limits.ok) return fail(res, 429, 'rate limit exceeded — try again shortly');

  const { id } = req.query;
  if (!/^[0-9a-f-]{36}$/i.test(id || '')) return fail(res, 400, 'id must be a uuid');

  const limit = Math.min(Math.max(parseInt(req.query.limit, 10) || 12, 1), 50);

  try {
    const embedding = await getItemEmbedding(id);
    if (!embedding) return fail(res, 404, 'not found');

    // Ask for one extra: the item itself is its own nearest neighbour and is
    // dropped below.
    const rows = await searchItems({ embedding, limit: limit + 1 });
    const results = rows.filter((row) => row.id !== id).slice(0, limit);

    res.setHeader('Cache-Control', 'public, max-age=300');
    return json(res, 200, {
      id,
      count: results.length,
      results: results.map((row) => presentItem(row)),
    });
  } catch (error) {
    console.error('similar lookup failed:', error);
    return fail(res, 500, 'lookup failed');
  }
}
