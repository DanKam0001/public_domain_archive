/**
 * CLI shim so the Python parity test can call the JavaScript encoder.
 *
 *   node tests/embed_text.mjs "a red flower" "snow covered mountains"
 *
 * Prints {"vectors": [[...], ...]} as JSON on stdout.
 */
import { embedText } from '../api/_lib/embed.mjs';

const texts = process.argv.slice(2);
if (texts.length === 0) {
  console.error('usage: node tests/embed_text.mjs "text" ["text" ...]');
  process.exit(2);
}
console.log(JSON.stringify({ vectors: await embedText(texts) }));
