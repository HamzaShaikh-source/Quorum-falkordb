import { describe, expect, it } from 'vitest';
import { CAPABILITIES, LEVELS, levelName, levelSummary } from './roles';

describe('permission levels', () => {
  it('has three levels with plain names, in order of power', () => {
    expect(LEVELS.map((l) => [l.role, l.name])).toEqual([['agent', 'Contributor'], ['auditor', 'Reviewer'], ['human', 'Admin']]);
    expect(LEVELS.every((l) => l.summary.length > 20)).toBe(true);
  });
  it('answers for every capability and every level, and a higher level never has less', () => {
    const rank = (v: boolean | 'limited') => (v === true ? 2 : v === 'limited' ? 1 : 0);
    for (const c of CAPABILITIES) {
      for (const l of LEVELS) expect(['boolean', 'string']).toContain(typeof c[l.role]);
      expect(rank(c.agent)).toBeLessThanOrEqual(rank(c.auditor));
      expect(rank(c.auditor)).toBeLessThanOrEqual(rank(c.human));
      if (c.agent === 'limited') expect(c.note).toBeTruthy();
    }
  });
  it('keeps the dangerous things to the higher levels', () => {
    const row = (k: string) => CAPABILITIES.find((c) => c.key === k)!;
    expect(row('quarantine').agent).toBe(false);
    expect(row('review')).toMatchObject({ agent: false, auditor: false, human: true });
    expect(row('manage')).toMatchObject({ agent: false, auditor: false, human: true });
  });
  it('falls back to the raw role for an unknown one', () => {
    expect(levelName('agent')).toBe('Contributor');
    expect(levelName('mystery')).toBe('mystery');
    expect(levelSummary('mystery')).toBe('');
  });
});
