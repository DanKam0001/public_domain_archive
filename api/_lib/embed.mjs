/**
 * Query-time text embeddings.
 *
 * Only CLIP's TEXT tower runs here. The image tower runs at ingest, in Python.
 * Both must come from the same checkpoint or the vectors are not in a shared
 * space and similarity is meaningless — this does not error, it silently
 * returns confident nonsense, which is why tests/test_model_parity.py asserts
 * the two implementations agree.
 *
 * `Xenova/clip-vit-base-patch32` is the ONNX export of the same
 * `openai/clip-vit-base-patch32` weights the ingest pipeline pins.
 */

import { AutoTokenizer, CLIPTextModelWithProjection, env } from '@huggingface/transformers';

export const MODEL_ID = 'Xenova/clip-vit-base-patch32';
export const EMBED_DIM = 512;

// Transformers.js defaults to caching model files inside node_modules, which is
// READ-ONLY on Vercel:
//   ENOENT: mkdir '/var/task/node_modules/@huggingface/transformers/.cache'
// /tmp is the only writable path in a serverless container. It persists for the
// life of a warm container, so the model downloads once per cold start and is
// reused by every subsequent request that container serves.
env.cacheDir = '/tmp/.transformers-cache';
// No browser present; the browser-cache path only produces noisy warnings here.
env.useBrowserCache = false;

// Loaded once per warm container. The first request pays 1-3s; every
// subsequent one is ~30ms, which is why the search route also caches
// query -> vector in Redis.
let loading = null;

async function getModel() {
  if (!loading) {
    loading = (async () => {
      const tokenizer = await AutoTokenizer.from_pretrained(MODEL_ID);
      const model = await CLIPTextModelWithProjection.from_pretrained(MODEL_ID, {
        // fp16, and this is measured rather than assumed. Cosine similarity of
        // this encoder's output against the Python image-side encoder, on
        // identical text:
        //
        //   fp32  1.000000   ~250MB
        //   fp16  1.000000   ~125MB   <- identical parity, half the download
        //   q8    0.724198   ~65MB    <- BROKEN
        //
        // q8 is the trap. It raises no error and returns plausible-looking
        // results, but its vectors are not in the same space as the stored
        // image embeddings, so search would be quietly and substantially wrong.
        // Anyone optimising cold-start size will be tempted by it. Do not use
        // it without re-running tests/test_model_parity.py.
        dtype: 'fp16',
      });
      return { tokenizer, model };
    })();
  }
  return loading;
}

/**
 * Embed one or more texts. Returns L2-normalised vectors, so cosine similarity
 * is a dot product and pgvector's `<=>` behaves consistently.
 */
export async function embedText(texts) {
  const { tokenizer, model } = await getModel();
  const inputs = await tokenizer(texts, { padding: true, truncation: true });
  const { text_embeds } = await model(inputs);

  const vectors = text_embeds.tolist().map((v) => {
    const norm = Math.sqrt(v.reduce((acc, x) => acc + x * x, 0));
    return v.map((x) => x / norm);
  });

  for (const v of vectors) {
    if (v.length !== EMBED_DIM) {
      throw new Error(
        `expected ${EMBED_DIM}-d embedding, got ${v.length}-d — wrong model or wrong output tensor`,
      );
    }
  }
  return vectors;
}

/** pgvector accepts the '[1,2,3]' literal form. */
export function toVectorLiteral(vector) {
  return `[${vector.join(',')}]`;
}
