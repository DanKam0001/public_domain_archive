/**
 * Shared HTTP concerns: CORS, responses, rate limiting, and the shape in which
 * items are presented to callers.
 */

const R2_PUBLIC_BASE = (process.env.R2_PUBLIC_BASE || '').replace(/\/$/, '');

const REDIS_URL = process.env.UPSTASH_REDIS_REST_URL;
const REDIS_TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN;

export function cors(res) {
  // The API is public and unauthenticated for reads; a browser-based client
  // should be able to call it directly.
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-API-Key');
}

export function json(res, status, body) {
  res.status(status).setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify(body));
}

export function fail(res, status, message, extra = {}) {
  json(res, status, { error: message, ...extra });
}

/**
 * Present a stored row to a caller.
 *
 * Two things here are licensing policy expressed in code, not cosmetics:
 *
 *  - `license.statement` is phrased as a report of the SOURCE's claim, never as
 *    our own assertion. We are an index pointing at a claim, not a warrantor of
 *    it. See docs/licensing-policy.md.
 *  - `attribution` is null rather than an empty string when none is required,
 *    so an automated consumer can tell "no credit needed" from "credit unknown".
 */
export function presentItem(row, { full = false } = {}) {
  const out = {
    id: row.id,
    title: row.title,
    creator: row.creator,
    source: row.source,
    source_url: row.source_url,
    license: {
      id: row.license_id,
      url: row.license_url,
      tier: row.license_tier,
      requires_attribution: row.license_tier === 'attribution',
      statement: `Source states: ${row.license_id}. Verify at ${row.source_url}`,
    },
    attribution: row.attribution ?? null,
    width: row.width,
    height: row.height,
    thumbnail_url: row.thumb_key ? `${R2_PUBLIC_BASE}/${row.thumb_key}` : null,
    image_url: row.r2_key ? `${R2_PUBLIC_BASE}/${row.r2_key}` : null,
  };
  if (row.similarity !== undefined) out.similarity = row.similarity;

  if (full) {
    out.description = row.description;
    out.mime = row.mime;
    out.bytes = row.bytes;
    out.embed_model = row.embed_model;
    out.ingested_at = row.ingested_at;
    // The source's original claim, verbatim. Exposed so a caller can audit our
    // licence determination rather than having to trust it.
    out.raw_license_meta = row.raw_license_meta;
    out.download_url = `/api/item/${row.id}/download`;
    out.similar_url = `/api/item/${row.id}/similar`;
  }
  return out;
}

/**
 * Fixed-window rate limit backed by Upstash.
 *
 * Fails OPEN. If Redis is unreachable the request is served: this is a free
 * public archive, and a cache outage degrading into "no limit" is a much better
 * failure than one that takes search down entirely.
 */
export async function rateLimit(req, { limit = 60, window = 60 } = {}) {
  if (!REDIS_URL || !REDIS_TOKEN) return { ok: true, remaining: limit };

  const key = `rl:${clientKey(req)}:${Math.floor(Date.now() / 1000 / window)}`;
  try {
    const response = await fetch(`${REDIS_URL}/pipeline`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${REDIS_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify([['INCR', key], ['EXPIRE', key, window]]),
    });
    if (!response.ok) return { ok: true, remaining: limit };
    const [{ result: count }] = await response.json();
    return { ok: count <= limit, remaining: Math.max(0, limit - count) };
  } catch {
    return { ok: true, remaining: limit };
  }
}

function clientKey(req) {
  const apiKey = req.headers['x-api-key'];
  if (apiKey) return `k:${apiKey.slice(0, 24)}`;
  const forwarded = req.headers['x-forwarded-for'] || '';
  return `ip:${forwarded.split(',')[0].trim() || 'unknown'}`;
}

/** Cache query -> embedding, so repeat searches skip the model entirely. */
export async function cached(key, compute) {
  if (!REDIS_URL || !REDIS_TOKEN) return compute();
  const redisKey = `emb:${key}`;
  try {
    const hit = await fetch(`${REDIS_URL}/get/${encodeURIComponent(redisKey)}`, {
      headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
    });
    if (hit.ok) {
      const { result } = await hit.json();
      if (result) return JSON.parse(result);
    }
  } catch {
    // fall through to compute
  }

  const value = await compute();
  try {
    await fetch(`${REDIS_URL}/setex/${encodeURIComponent(redisKey)}/86400`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
      body: JSON.stringify(value),
    });
  } catch {
    // caching is best-effort
  }
  return value;
}
