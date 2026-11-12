/**
 * Token estimator. This is a heuristic, not a real tokenizer: word runs cost about one token per 4.5 characters,
 * every punctuation mark costs about one token. It tracks GPT/Claude-style BPE counts within roughly 15% on English
 * plus JSON, which is enough to compare formats against each other. The UI labels every number as an estimate.
 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  let tokens = 0;
  const re = /[A-Za-z0-9À-ɏ]+|\s+|[^\sA-Za-z0-9À-ɏ]/gu; // u: an emoji is one symbol, as in Python
  for (const m of text.matchAll(re)) {
    const s = m[0];
    if (/^\s+$/.test(s)) {
      if (s.includes('\n')) tokens += 0.5;
      continue;
    }
    tokens += /^[A-Za-z0-9À-ɏ]+$/.test(s) ? Math.max(1, Math.ceil(s.length / 4.5)) : 0.9;
  }
  return Math.round(tokens);
}

export const fmt = (n: number) => n.toLocaleString('en-US');
export const pct = (saved: number, base: number) => (base > 0 ? Math.round((saved / base) * 100) : 0);
