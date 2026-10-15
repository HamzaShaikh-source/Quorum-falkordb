import { describe, expect, it } from 'vitest';
import { actionDisplayState } from './actionState';
import type { GraphNode } from '../api/schemas';

type A = NonNullable<GraphNode['action']>;
const mk = (status: GraphNode['status'], a: Partial<A>): Pick<GraphNode, 'status' | 'action'> => ({
  status,
  action: {
    type: 'refund',
    reversibility: 'COMPENSABLE',
    executed: false,
    confirmed: false,
    gate: 'PASSED',
    ...a,
  },
});

describe('actionDisplayState (CONTRACT.md section 2 table)', () => {
  it('Staged', () => expect(actionDisplayState(mk('HEALTHY', {}))).toBe('staged'));
  it('Held for review', () => expect(actionDisplayState(mk('HEALTHY', { gate: 'BLOCKED' }))).toBe('held'));
  it('Running', () => expect(actionDisplayState(mk('HEALTHY', { executed: true }))).toBe('running'));
  it('Ran', () => expect(actionDisplayState(mk('HEALTHY', { executed: true, confirmed: true }))).toBe('ran'));
  it('Cancelled', () => expect(actionDisplayState(mk('QUARANTINED', {}))).toBe('cancelled'));
  it('Rolling back (pending)', () =>
    expect(
      actionDisplayState(mk('QUARANTINED', { executed: true, compensation: { status: 'PENDING_COMPENSATION', attempts: 0 } })),
    ).toBe('rollingBack'));
  it('Rolling back (dispatching)', () =>
    expect(
      actionDisplayState(mk('QUARANTINED', { executed: true, compensation: { status: 'DISPATCHING', attempts: 1 } })),
    ).toBe('rollingBack'));
  it('Rolled back', () =>
    expect(
      actionDisplayState(mk('QUARANTINED', { executed: true, compensation: { status: 'COMPENSATED', attempts: 1 } })),
    ).toBe('rolledBack'));
  it('Rollback failed', () =>
    expect(
      actionDisplayState(mk('QUARANTINED', { executed: true, compensation: { status: 'FAILED', attempts: 5 } })),
    ).toBe('rollbackFailed'));
  it('Needs a person', () =>
    expect(actionDisplayState(mk('QUARANTINED', { executed: true, reversibility: 'IRREVERSIBLE' }))).toBe('needsPerson'));
  it('renders unknown states neutrally', () => {
    expect(actionDisplayState({ status: 'UNKNOWN', action: undefined })).toBe('unknown');
    expect(actionDisplayState(mk('UNKNOWN', {}))).toBe('unknown');
  });
});
