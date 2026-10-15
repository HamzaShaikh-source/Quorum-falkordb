import { expect, it } from 'vitest';
import { queryTiming } from './queryTiming';

it('labels execution origin and failure without inventing a database measurement', () => {
  expect(queryTiming({ ms: 0.4, origin: 'falkordb' })).toBe('Executed in <1ms on FalkorDB');
  expect(queryTiming({ ms: 12.345, origin: 'falkordb' })).toBe('Executed in 12.3ms on FalkorDB');
  expect(queryTiming({ ms: 0, origin: 'falkordb' })).not.toContain('<1ms');
  expect(queryTiming({ ms: 0.4, origin: 'memory' })).toContain('In-memory operation');
  expect(queryTiming({ ms: 0.4 })).toContain('not executed');
  expect(queryTiming({ ms: 0.4, origin: 'falkordb', outcome: 'failure' })).toBe('Failed after 0.4ms');
});
