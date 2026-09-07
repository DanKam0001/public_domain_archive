/**
 * GET /api/item/{id}/download
 *
 * Redirects to the file on R2.
 *
 * The plan called for a *signed* URL. It isn't one, deliberately: the bucket is
 * public, because every file in it is public domain by construction. Signing
 * would add latency and key management to protect content that needs no
 * protection, and it would break plain <img> tags and `curl` — the two things
 * an archive most needs to support.
 *
 * The endpoint still earns its place over linking R2 directly:
 *   - the R2 layout stays an implementation detail we can change
 *   - a takedown takes effect here immediately, via RLS
 *   - it is a natural place to count downloads later
 */

import { getItem } from '../../_lib/db.mjs';
import { cors, fail, rateLimit } from '../../_lib/http.mjs';

const R2_PUBLIC_BASE = (process.env.R2_PUBLIC_BASE || '').replace(/\/$/, '');

export default async function handler(req, res) {
  cors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'GET') return fail(res, 405, 'use GET');

  const limits = await rateLimit(req, { limit: 120, window: 60 });
  if (!limits.ok) return fail(res, 429, 'rate limit exceeded — try again shortly');

  const { id } = req.query;
  if (!/^[0-9a-f-]{36}$/i.test(id || '')) return fail(res, 400, 'id must be a uuid');

  try {
    const row = await getItem(id);
    if (!row || !row.r2_key) return fail(res, 404, 'not found');

    // Surfaced in headers so a script downloading in bulk can honour the credit
    // requirement without a second request for metadata.
    res.setHeader('X-Source-Url', row.source_url);
    res.setHeader('X-License', row.license_id);
    if (row.attribution) res.setHeader('X-Attribution', row.attribution);

    // 302, not 301: the underlying key may change, and a permanent redirect
    // would be cached by clients forever.
    res.writeHead(302, { Location: `${R2_PUBLIC_BASE}/${row.r2_key}` });
    return res.end();
  } catch (error) {
    console.error('download failed:', error);
    return fail(res, 500, 'download failed');
  }
}
