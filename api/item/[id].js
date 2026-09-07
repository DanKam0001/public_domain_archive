/**
 * GET /api/item/{id}
 *
 * Full metadata for one item, including the source's original licence claim
 * verbatim, so a caller can audit our determination rather than trust it.
 */

import { getItem } from '../_lib/db.mjs';
import { cors, fail, json, presentItem, rateLimit } from '../_lib/http.mjs';

export default async function handler(req, res) {
  cors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'GET') return fail(res, 405, 'use GET');

  const limits = await rateLimit(req, { limit: 120, window: 60 });
  if (!limits.ok) return fail(res, 429, 'rate limit exceeded — try again shortly');

  const { id } = req.query;
  if (!isUuid(id)) return fail(res, 400, 'id must be a uuid');

  try {
    const row = await getItem(id);
    // A taken-down item is filtered by RLS and so arrives here as not found.
    // That is the intended behaviour: we do not confirm that a withdrawn item
    // ever existed.
    if (!row) return fail(res, 404, 'not found');

    res.setHeader('Cache-Control', 'public, max-age=300');
    return json(res, 200, presentItem(row, { full: true }));
  } catch (error) {
    console.error('item lookup failed:', error);
    return fail(res, 500, 'lookup failed');
  }
}

export function isUuid(value) {
  return typeof value === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
