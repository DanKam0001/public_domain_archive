/**
 * Database access via PostgREST, as the `anon` role.
 *
 * Deliberately not a direct Postgres connection. A direct connection
 * authenticates as `postgres`, which bypasses row level security — the takedown
 * filter and the api_keys protection would then hold only by convention rather
 * than by the database enforcing them. Going through PostgREST means every
 * request really does execute as `anon`, and serverless connection pooling
 * stops being a problem because this is plain HTTP.
 */

const SUPABASE_URL = process.env.SUPABASE_URL;
const ANON_KEY = process.env.SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !ANON_KEY) {
  // Fail at module load rather than per request, so a misconfigured deploy is
  // obvious immediately instead of returning empty results that look like
  // "no matches".
  throw new Error('SUPABASE_URL and SUPABASE_ANON_KEY must be set');
}

const headers = {
  apikey: ANON_KEY,
  Authorization: `Bearer ${ANON_KEY}`,
  'Content-Type': 'application/json',
};

async function request(path, options = {}) {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    ...options,
    headers: { ...headers, ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`postgrest ${response.status}: ${body.slice(0, 300)}`);
  }
  return response.json();
}

/** Vector search with duplicate suppression. See db/migrations/002. */
export function searchItems({ embedding, limit = 24, tier = null }) {
  return request('rpc/search_items', {
    method: 'POST',
    body: JSON.stringify({
      query_embedding: embedding,
      match_limit: limit,
      filter_tier: tier,
    }),
  });
}

const ITEM_FIELDS = [
  'id', 'source', 'source_id', 'source_url', 'title', 'description', 'creator',
  'license_id', 'license_url', 'license_tier', 'attribution',
  'raw_license_meta', 'r2_key', 'thumb_key', 'width', 'height', 'mime',
  'bytes', 'embed_model', 'ingested_at',
].join(',');

export async function getItem(id) {
  // RLS already excludes taken-down rows; the filter is not repeated here,
  // because duplicating a security rule in application code is how the two
  // drift apart.
  const rows = await request(
    `items?id=eq.${encodeURIComponent(id)}&select=${ITEM_FIELDS}&limit=1`,
  );
  return rows[0] || null;
}

/** An item's own embedding, for "more like this". */
export async function getItemEmbedding(id) {
  const rows = await request(
    `items?id=eq.${encodeURIComponent(id)}&select=embedding&limit=1`,
  );
  return rows[0]?.embedding || null;
}
