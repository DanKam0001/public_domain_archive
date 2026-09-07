/**
 * Query expansion: abstract phrasing -> concrete visual descriptions.
 *
 * Why this exists
 * ---------------
 * CLIP matches literal visual content — objects, scenes, lighting. It cannot
 * match emotions, abstractions, or metaphor. The Phase 1 evaluation measured
 * exactly that: concrete queries averaged +0.113 spread over the corpus median,
 * while abstract ones collapsed ("the passage of time" +0.039, "a sense of
 * freedom" +0.055).
 *
 * That matters far beyond a search box. `auto_broll(script)` will be fed
 * narration lines, and narration is written in exactly this register — "and
 * everything fell apart", "it was a simpler time". Those are the queries the
 * flagship feature depends on, and they are the ones CLIP is worst at.
 *
 * Rewriting them into concrete scenes before embedding, measured on the real
 * corpus:
 *
 *     the passage of time       +0.039 -> +0.187
 *     a sense of freedom        +0.055 -> +0.151
 *     something lonely          +0.067 -> +0.160
 *     two people talking        +0.088 -> +0.156
 *     a feeling of celebration  +0.062 -> +0.102
 *
 * Mean gain +0.089, every query improved, and the expanded results now score
 * ABOVE the concrete-query average — the weakest category becomes among the
 * strongest.
 *
 * Why it is opt-in
 * ----------------
 * Measured at ~$0.005 per expansion. At a thousand searches a day that is
 * ~$144/month against a total infrastructure bill of $25, on a donation-funded
 * archive. So expansion is off by default, cached indefinitely (a rewrite of a
 * fixed phrase never goes stale), and turned on by callers who need it — which
 * is precisely the automated-pipeline path, not the casual web search.
 */

import Anthropic from '@anthropic-ai/sdk';

export const EXPANSION_MODEL = 'claude-opus-5';
const VARIANTS = 5;

const REDIS_URL = process.env.UPSTASH_REDIS_REST_URL;
const REDIS_TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN;

const SYSTEM = `You turn a narration line or abstract phrase into concrete VISUAL descriptions for searching a stock image archive.

The archive is searched with CLIP, which matches literal visual content: objects, scenes, lighting, composition. It cannot match emotions, abstractions, or metaphors.

Given a phrase, output ${VARIANTS} short concrete visual descriptions of scenes that would convey it. Each must describe things a camera could photograph. No emotions, no abstractions, no metaphor.

Return ONLY a JSON array of ${VARIANTS} strings.`;

let client = null;

function getClient() {
  if (!process.env.ANTHROPIC_API_KEY) return null;
  if (!client) client = new Anthropic();
  return client;
}

/** True when expansion is configured and therefore possible at all. */
export function expansionAvailable() {
  return Boolean(process.env.ANTHROPIC_API_KEY);
}

/**
 * Rewrite one query into concrete visual descriptions.
 *
 * Returns `[]` if expansion is unavailable or fails — never throws. A search
 * that cannot be expanded should still return unexpanded results rather than
 * failing outright.
 */
export async function expandQuery(query) {
  const anthropic = getClient();
  if (!anthropic) return [];

  const cacheKey = `expand:${EXPANSION_MODEL}:${query.toLowerCase()}`;
  const cached = await cacheGet(cacheKey);
  if (cached) return cached;

  try {
    const message = await anthropic.messages.create({
      model: EXPANSION_MODEL,
      max_tokens: 1000,
      system: SYSTEM,
      messages: [{ role: 'user', content: query }],
    });

    // Guard before reading content: a refusal is an HTTP 200 with no usable text.
    if (message.stop_reason === 'refusal') return [];

    const text = message.content
      .filter((block) => block.type === 'text')
      .map((block) => block.text)
      .join('')
      .trim();

    const start = text.indexOf('[');
    const end = text.lastIndexOf(']');
    if (start === -1 || end === -1) return [];

    const variants = JSON.parse(text.slice(start, end + 1))
      .filter((v) => typeof v === 'string' && v.trim())
      .slice(0, VARIANTS);

    // Cached with no expiry: the rewrite of a fixed phrase does not go stale,
    // and this cache is the main thing keeping the cost bounded.
    await cacheSet(cacheKey, variants);
    return variants;
  } catch (error) {
    console.error('query expansion failed:', error);
    return [];
  }
}

async function cacheGet(key) {
  if (!REDIS_URL || !REDIS_TOKEN) return null;
  try {
    const response = await fetch(`${REDIS_URL}/get/${encodeURIComponent(key)}`, {
      headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
    });
    if (!response.ok) return null;
    const { result } = await response.json();
    return result ? JSON.parse(result) : null;
  } catch {
    return null;
  }
}

async function cacheSet(key, value) {
  if (!REDIS_URL || !REDIS_TOKEN) return;
  try {
    await fetch(`${REDIS_URL}/set/${encodeURIComponent(key)}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
      body: JSON.stringify(value),
    });
  } catch {
    // best effort
  }
}
