import { describe, expect, it } from 'vitest';
import { GLOSSARY, GLOSSARY_SORTED } from './glossary';
import { copy } from './index';

describe('glossary', () => {
  it('is sorted A to Z', () => {
    const names = GLOSSARY_SORTED.map((t) => t.term);
    expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b)));
  });

  it('has no duplicate terms and no empty definitions', () => {
    const names = GLOSSARY.map((t) => t.term.toLowerCase());
    expect(new Set(names).size).toBe(names.length);
    for (const t of GLOSSARY) expect(t.plain.length).toBeGreaterThan(20);
  });

  it('only links to screens that have a nav label', () => {
    for (const t of GLOSSARY) if (t.see) expect(copy.nav[t.see]).toBeTruthy();
  });

  it('defines every word the header and feed use', () => {
    const names = GLOSSARY.map((t) => t.term.toLowerCase());
    for (const w of ['quarantine', 'memory version', 'safety checks', 'tokens saved', 'handoff', 'tenant']) expect(names).toContain(w);
  });
});
