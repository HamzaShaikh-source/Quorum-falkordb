import type { GraphNode } from '../api/schemas';

export type ActionDisplayKey =
  | 'staged'
  | 'held'
  | 'running'
  | 'ran'
  | 'cancelled'
  | 'rollingBack'
  | 'rolledBack'
  | 'rollbackFailed'
  | 'needsPerson'
  | 'unknown';

/** The single mapping from graph fields to what a person reads. See CONTRACT.md section 2. */
export function actionDisplayState(node: Pick<GraphNode, 'status' | 'action'>): ActionDisplayKey {
  const a = node.action;
  if (!a) return 'unknown';
  if (node.status === 'QUARANTINED') {
    if (!a.executed) return 'cancelled';
    if (a.reversibility === 'IRREVERSIBLE') return 'needsPerson';
    const c = a.compensation?.status;
    if (c === 'COMPENSATED') return 'rolledBack';
    if (c === 'FAILED') return 'rollbackFailed';
    return 'rollingBack';
  }
  if (node.status === 'HEALTHY') {
    if (!a.executed) return a.gate === 'BLOCKED' ? 'held' : 'staged';
    return a.confirmed ? 'ran' : 'running';
  }
  return 'unknown';
}

export type ActionTone = 'good' | 'bad' | 'review' | 'neutral' | 'warn';

export function actionTone(key: ActionDisplayKey): ActionTone {
  switch (key) {
    case 'ran':
      return 'good';
    case 'cancelled':
    case 'rolledBack':
    case 'rollingBack':
    case 'rollbackFailed':
    case 'needsPerson':
      return 'bad';
    case 'held':
      return 'review';
    case 'running':
      return 'warn';
    default:
      return 'neutral';
  }
}
