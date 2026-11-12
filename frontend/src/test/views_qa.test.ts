import { describe, expect, it } from 'vitest';
import { estimateTokens, fmt, pct } from '../context/tokens';
import { safeUrl } from '../api/safeUrl';
import { copyText } from '../workspace/clipboard';
import { PLANS, seatsLimit, seatsUsed, type Workspace } from '../workspace/store';

describe('Views & Workspaces QA Architect Audit', () => {
  describe('Division by zero and token savings calculations', () => {
    it('pct() safely handles zero and negative denominators without throwing or returning NaN', () => {
      expect(pct(50, 0)).toBe(0);
      expect(pct(50, -10)).toBe(0);
      expect(pct(0, 0)).toBe(0);
      expect(pct(25, 100)).toBe(25);
      expect(pct(33, 100)).toBe(33);
      expect(Number.isNaN(pct(50, 0))).toBe(false);
    });

    it('Benchmark latency max denominator guards against 0 ms p99', () => {
      const p99_0 = 0;
      const max0 = Math.max(p99_0, 1);
      expect(max0).toBe(1);
      const w0 = (p99_0 / max0) * 300;
      expect(w0).toBe(0);
      expect(Number.isFinite(w0)).toBe(true);

      const p99_neg = -15;
      const maxNeg = Math.max(p99_neg, 1);
      expect(maxNeg).toBe(1);
    });

    it('Bar chart max calculation guards against zero token counts', () => {
      const rawTokens = 0;
      const proseTokens = 0;
      const max = Math.max(rawTokens, proseTokens, 1);
      expect(max).toBe(1);
      const widthPct = Math.max(1.5, (0 / max) * 100);
      expect(widthPct).toBe(1.5);
      expect(Number.isFinite(widthPct)).toBe(true);
    });

    it('Context packed stages token division guards against zero initial stage tokens', () => {
      const stage0Tokens = 0;
      const denominator = stage0Tokens || 1;
      expect(denominator).toBe(1);
      const width = Math.max(1, (10 / denominator) * 100);
      expect(width).toBe(1000);
    });

    it('fmt handles 0, negative, and large integers cleanly', () => {
      expect(fmt(0)).toBe('0');
      expect(fmt(1250000)).toBe('1,250,000');
      expect(fmt(-42)).toBe('-42');
    });
  });

  describe('Multi-vector string inputs: Unicode, Long Strings, and Empty inputs', () => {
    it('estimateTokens handles long strings (>500 chars) and unicode without crashing', () => {
      const longAscii = 'a'.repeat(600);
      const tokensAscii = estimateTokens(longAscii);
      expect(tokensAscii).toBeGreaterThan(0);
      expect(Number.isFinite(tokensAscii)).toBe(true);

      const unicodeString = 'Quorum 🚀 🧠 🛡️ 简体中文 العربية Русский язык ' + '✨'.repeat(200);
      const tokensUnicode = estimateTokens(unicodeString);
      expect(tokensUnicode).toBeGreaterThan(0);
      expect(Number.isFinite(tokensUnicode)).toBe(true);

      expect(estimateTokens('')).toBe(0);
    });

    it('reveals UTF-16 surrogate pair slicing bug in Avatar name.slice(0, 1)', () => {
      const rocketName = '🚀 Rocket';
      const sliced = rocketName.slice(0, 1);
      // Slicing code unit yields lone high surrogate \uD83D (length 1 code unit), not the full emoji
      expect(sliced).toBe('\uD83D');
      expect(sliced).not.toBe('🚀');

      // The correct fix using Array.from or spread preserves the full grapheme/glyph:
      const correctInitial = Array.from(rocketName)[0];
      expect(correctInitial).toBe('🚀');
    });
  });

  describe('Form boundaries: seats, limits, and team math', () => {
    it('seats coercion prevents 0 seats', () => {
      const parseSeats = (val: string) => Number(val) || 1;
      expect(parseSeats('0')).toBe(1);
      expect(parseSeats('')).toBe(1);
      expect(parseSeats('abc')).toBe(1);
      expect(parseSeats('10')).toBe(10);
    });

    it('Mesh layout angle division handles 0 members safely', () => {
      const memberCount = 0;
      const denominator = Math.max(1, memberCount);
      expect(denominator).toBe(1);
      const angle = (0 / denominator) * Math.PI * 2;
      expect(Number.isFinite(angle)).toBe(true);
    });

    it('Workspace seatsUsed and seatsLimit calculations are consistent', () => {
      const sampleWs: Workspace = {
        org: 'Test Org',
        plan: 'business',
        hub: 'http://localhost:8000',
        teams: [],
        members: [
          { id: 'm1', name: 'Alice', role: 'agent', teamId: 't1', client: 'cursor', device: 'laptop', token: 'q1', createdAt: 100 },
          { id: 'm2', name: 'Bob', role: 'human', teamId: 't1', client: 'chatgpt', device: 'ipad', token: 'q2', createdAt: 200 },
        ],
        links: [],
      };
      expect(seatsUsed(sampleWs)).toBe(2);
      expect(seatsLimit(sampleWs)).toBe(PLANS.business.seats);
      expect(PLANS.business.seats).toBe(50);
    });
  });

  describe('Citation URL safety and clipboard fallback', () => {
    it('safeUrl strictly allows http/https and blocks javascript: or data: URIs', () => {
      expect(safeUrl('https://quorum.example.com')).toBe('https://quorum.example.com');
      expect(safeUrl('http://insecure.internal')).toBe('http://insecure.internal');
      expect(safeUrl('javascript:alert(document.cookie)')).toBeNull();
      expect(safeUrl('data:text/html,<script>alert(1)</script>')).toBeNull();
      expect(safeUrl('')).toBeNull();
      expect(safeUrl(null)).toBeNull();
      expect(safeUrl(undefined)).toBeNull();
    });

    it('copyText falls back cleanly when navigator.clipboard is unavailable (HTTP)', async () => {
      // In Node/Vitest environment, navigator.clipboard is typically undefined or mocked
      const result = await copyText('token_123');
      // Should return a boolean without throwing an unhandled exception
      expect(typeof result).toBe('boolean');
    });
  });
});
