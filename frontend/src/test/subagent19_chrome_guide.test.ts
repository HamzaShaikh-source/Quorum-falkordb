import { describe, expect, it } from 'vitest';
import { GLOSSARY, GLOSSARY_SORTED } from '../copy/glossary';
import { visibleViews, viewKey, viewForKey } from '../state/views';
import { useStore } from '../state/store';

describe('Subagent 19: Top Chrome, Navigation, Guide & Glossary QA Suite', () => {
  describe('1. Glossary Filtering & Regex Boundary Stress Tests', () => {
    // Simulating Glossary search filtering logic from Guide.tsx:53-56
    function filterGlossary(q: string) {
      const n = q.trim().toLowerCase();
      return n
        ? GLOSSARY_SORTED.filter(
            (t) => t.term.toLowerCase().includes(n) || t.plain.toLowerCase().includes(n)
          )
        : GLOSSARY_SORTED;
    }

    it('handles regex special characters without throwing SyntaxError', () => {
      const specialCharacters = ['[', ']', '(', ')', '*', '+', '?', '\\', '^', '$', '.', '{', '}', '|'];
      for (const char of specialCharacters) {
        expect(() => filterGlossary(char)).not.toThrow();
        const results = filterGlossary(char);
        expect(Array.isArray(results)).toBe(true);
      }
    });

    it('matches terms containing parentheses literally', () => {
      // "Gate (Quorum gate)" contains literal "(" and ")"
      const matchOpenParen = filterGlossary('(');
      expect(matchOpenParen.some((t) => t.term.includes('('))).toBe(true);

      const matchGate = filterGlossary('(quorum gate)');
      expect(matchGate.length).toBeGreaterThanOrEqual(1);
      expect(matchGate[0]?.term).toBe('Gate (Quorum gate)');
    });

    it('handles arbitrary malicious regex injection payloads safely', () => {
      const maliciousQueries = [
        '(((((((((',
        '[a-z]+',
        '.*',
        '\\d+\\.\\d+',
        '(?=.*[a-z])',
        '\\x00',
        '[[[[]]]]',
      ];
      for (const q of maliciousQueries) {
        expect(() => filterGlossary(q)).not.toThrow();
      }
    });

    it('returns empty list for non-matching queries and full list for empty queries', () => {
      expect(filterGlossary('')).toHaveLength(GLOSSARY.length);
      expect(filterGlossary('   ')).toHaveLength(GLOSSARY.length);
      expect(filterGlossary('xyznonexistentword12345')).toHaveLength(0);
    });

    it('is case-insensitive for search', () => {
      const lower = filterGlossary('quarantine');
      const upper = filterGlossary('QUARANTINE');
      const mixed = filterGlossary('QuArAnTiNe');
      expect(lower).toEqual(upper);
      expect(lower).toEqual(mixed);
      expect(lower.length).toBeGreaterThanOrEqual(1);
    });

    it('verifies all glossary terms have valid first characters for A-Z indexing', () => {
      for (const t of GLOSSARY) {
        expect(t.term.length).toBeGreaterThan(0);
        const first = t.term[0]!.toUpperCase();
        expect(first >= 'A' && first <= 'Z').toBe(true);
      }
    });
  });

  describe('2. Navigation Tabs & Views Upstream Contract', () => {
    it('returns 6 tabs in clean mode and 10 tabs in demo mode', () => {
      const clean = visibleViews(false);
      const demo = visibleViews(true);
      expect(clean).toEqual(['overview', 'console', 'context', 'connect', 'team', 'activity']);
      expect(demo).toEqual([
        'overview', 'console', 'memory', 'brain', 'tenants',
        'benchmark', 'context', 'connect', 'team', 'activity'
      ]);
    });

    it('maps numerical shortcut keys consistently', () => {
      // In demo mode: overview=0, console=1, ..., activity=9
      expect(viewKey('overview', true)).toBe('0');
      expect(viewKey('console', true)).toBe('1');
      expect(viewKey('activity', true)).toBe('9');

      // viewForKey bidirectional mapping
      expect(viewForKey('0', true)).toBe('overview');
      expect(viewForKey('1', true)).toBe('console');
      expect(viewForKey('9', true)).toBe('activity');
      expect(viewForKey('x', true)).toBeUndefined();
    });

    it('checks for unhandled shortcuts in keybindings', () => {
      // Key 'g' or 'G' should not be mapped to any standard view number key
      expect(viewForKey('g', true)).toBeUndefined();
      expect(viewForKey('G', true)).toBeUndefined();
      expect(viewForKey('g', false)).toBeUndefined();
    });
  });

  describe('3. Agent Activity Time Formatting Logic', () => {
    // Mirrors ago() function from AgentStrip.tsx:9-15
    function ago(ts: number, now: number): string {
      const s = Math.max(0, Math.round((now - ts) / 1000));
      if (s < 5) return 'just now';
      if (s < 60) return `${s}s ago`;
      if (s < 3600) return `${Math.floor(s / 60)} min ago`;
      return `${Math.floor(s / 3600)} h ago`;
    }

    it('formats timestamps across boundary intervals accurately', () => {
      const now = 1000000000;
      expect(ago(now, now)).toBe('just now');
      expect(ago(now - 4000, now)).toBe('just now');
      expect(ago(now - 5000, now)).toBe('5s ago');
      expect(ago(now - 59000, now)).toBe('59s ago');
      expect(ago(now - 60000, now)).toBe('1 min ago');
      expect(ago(now - 3599000, now)).toBe('59 min ago');
      expect(ago(now - 3600000, now)).toBe('1 h ago');
      expect(ago(now - 7200000, now)).toBe('2 h ago');
    });

    it('clamps future timestamps safely to "just now"', () => {
      const now = 1000000000;
      expect(ago(now + 60000, now)).toBe('just now');
    });
  });

  describe('4. Store State Invariants for Help Drawer & Chrome', () => {
    it('manages helpOpen state deterministically', () => {
      const { set } = useStore.getState();
      set({ helpOpen: false });
      expect(useStore.getState().helpOpen).toBe(false);

      set({ helpOpen: true });
      expect(useStore.getState().helpOpen).toBe(true);

      set({ helpOpen: false });
      expect(useStore.getState().helpOpen).toBe(false);
    });

    it('preserves presentation and devOpen states independently of guide', () => {
      const { set } = useStore.getState();
      set({ presentation: true, devOpen: true, helpOpen: true });
      expect(useStore.getState().presentation).toBe(true);
      expect(useStore.getState().devOpen).toBe(true);
      expect(useStore.getState().helpOpen).toBe(true);

      set({ helpOpen: false });
      expect(useStore.getState().presentation).toBe(true);
      expect(useStore.getState().devOpen).toBe(true);
      expect(useStore.getState().helpOpen).toBe(false);
    });
  });
});
