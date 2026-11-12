import { describe, expect, it } from 'vitest';
import { buildMap, pack, proseHistory, ragBaseline, ragTopK, rawDump, savings } from './packer';
import { synthGraph } from './synth';
import { estimateTokens } from './tokens';
import parity from '../../../backend/tests/fixtures/token_parity.json';

describe('estimateTokens', () => {
  it('is zero for empty text and grows with length', () => {
    expect(estimateTokens('')).toBe(0);
    expect(estimateTokens('refund window is 30 days')).toBeLessThan(estimateTokens('refund window is 30 days for every region in the world'));
  });
  it('counts punctuation-heavy JSON higher than the same words as prose', () => {
    const prose = 'refund window thirty days';
    expect(estimateTokens(JSON.stringify({ refund: { window: 'thirty', days: 30 } }))).toBeGreaterThan(estimateTokens(prose));
  });
});

describe('estimator parity with the backend', () => {
  it('counts the same tokens as backend/app/services/context_pack.py on every shared case', () => {
    expect(parity.length).toBeGreaterThanOrEqual(20);
    for (const c of parity) expect(estimateTokens(c.text), c.text.slice(0, 40)).toBe(c.tokens);
  });
});

describe('pack', () => {
  const g = synthGraph(300);
  const meta = { tenant: 'acme', epoch: 4 };

  it('is deterministic for the same input', () => {
    expect(pack(g, { query: 'refund window', budget: 400 }).text).toBe(pack(g, { query: 'refund window', budget: 400 }).text);
  });

  it('never returns quarantined or superseded nodes', () => {
    const bad = g.nodes.filter((n) => n.kind === 'fact' && n.label.startsWith('refund window')).map((n) => n.id);
    const poisoned = { ...g, nodes: g.nodes.map((n) => (bad.includes(n.id) ? { ...n, status: 'QUARANTINED' as const } : n)) };
    const p = pack(poisoned, { query: 'refund window days', budget: 600 });
    expect(p.included.some((id) => bad.includes(id))).toBe(false);
  });

  it('stays within the token budget', () => {
    const p = pack(g, { query: 'warranty length claims', budget: 120 });
    expect(p.tokens).toBeLessThanOrEqual(150);
    expect(p.truncated).toBe(true);
  });

  it('includes the ancestors that prove where an answer came from', () => {
    const p = pack(g, { query: 'standard shipments rule', budget: 800 });
    expect(p.included.some((id) => id.startsWith('s_'))).toBe(true);
  });

  it('returns a delta when the agent already holds some nodes', () => {
    const full = pack(g, { query: 'retention period', budget: 800 });
    const half = full.included.slice(0, Math.ceil(full.included.length / 2));
    const d = pack(g, { query: 'retention period', budget: 800, have: { epoch: 3, ids: half } });
    expect(d.delta?.added).toBe(full.included.length - half.length);
    expect(d.tokens).toBeLessThan(full.tokens);
  });

  it('records a monotonic story of shrinking tokens', () => {
    const p = pack(g, { query: 'plan price quotes', budget: 500 });
    const t = p.stages.map((s) => s.tokens);
    expect(t[0]).toBeGreaterThanOrEqual(t[1]!);
    expect(t[1]).toBeGreaterThan(t[t.length - 1]!);
  });

  it('saves a large share against both baselines on a few hundred nodes', () => {
    const p = pack(g, { query: 'refund window', budget: 500 });
    const s = savings(g, p, meta);
    expect(s.vsRaw / s.rawTokens).toBeGreaterThan(0.8);
    expect(s.vsProse / s.proseTokens).toBeGreaterThan(0.5);
    expect(estimateTokens(buildMap(g, meta))).toBeLessThan(estimateTokens(proseHistory(g)) / 4);
    expect(rawDump(g).length).toBeGreaterThan(0);
  });

  it('beats a top-8 RAG lookup on tokens, and RAG cannot tell a quarantined fact from a healthy one', () => {
    const bad = g.nodes.filter((n) => n.kind === 'fact' && n.label.startsWith('refund window')).map((n) => n.id);
    const poisoned = { ...g, nodes: g.nodes.map((n) => (bad.includes(n.id) ? { ...n, status: 'QUARANTINED' as const } : n)) };
    const p = pack(poisoned, { query: 'refund window days', budget: 500 });
    const s = savings(poisoned, p, meta, 'refund window days');
    expect(s.ragTokens).toBeGreaterThan(p.tokens);
    expect(ragBaseline(poisoned, 'refund window days')).toBe(s.ragTokens);
    expect(ragTopK(poisoned, 'refund window days').some((n) => n.status === 'QUARANTINED')).toBe(true);
  });
});
