import type { AuditEvent } from './server';

const VERBS: Record<string, [string, AuditEvent['severity']]> = {
  retrieve_context: ['looked up', 'read'],
  get_map: ['read the memory map', 'read'],
  get_node: ['opened', 'read'],
  find_path: ['traced how', 'read'],
  delta: ['checked what changed', 'read'],
  remember_fact: ['recorded a fact', 'write'],
  observe: ['captured facts from text', 'write'],
  derive: ['recorded a conclusion', 'write'],
  stage_action: ['proposed an action', 'write'],
  execute_action: ['executed an action', 'write'],
  claim_handoff: ['claimed a task', 'write'],
  quarantine: ['quarantined', 'alert'],
  restore: ['restored', 'alert'],
  explain_blast_radius: ['previewed the impact of quarantining', 'read'],
  review_handoff: ['reviewed a handoff', 'alert'],
};

const INFINITIVE: Record<string, string> = {
  'looked up': 'look up',
  opened: 'open',
  'traced how': 'trace how',
  'checked what changed': 'check what changed',
  'recorded a fact': 'record a fact',
  'captured facts from text': 'capture facts from text',
  'recorded a conclusion': 'record a conclusion',
  'proposed an action': 'propose an action',
  'executed an action': 'execute an action',
  'claimed a task': 'claim a task',
  quarantined: 'quarantine',
  restored: 'restore',
  'reviewed a handoff': 'review a handoff',
};

/** The same sentence the backend writes (GET /api/audit), for the sample build where there is no backend. */
export function sentenceFor(tool: string, summary: string, ok: boolean, sample = false): { text: string; severity: AuditEvent['severity'] } {
  const [verb, severity] = VERBS[tool] ?? [tool, 'read'];
  if (!ok) return { text: `tried to ${INFINITIVE[verb] ?? verb}${summary ? ` ${summary}` : ''}`, severity: 'failed' };
  // The sample feed's summaries are already prose ("refund window" → 9 nodes); join them with a dash rather than quoting them again.
  if (sample) return { text: summary ? `${verb} — ${summary}` : verb, severity };
  const quoted = ['retrieve_context', 'remember_fact', 'stage_action', 'get_node', 'observe'].includes(tool);
  return { text: summary ? (quoted ? `${verb} “${summary}”` : `${verb} ${summary}`) : verb, severity };
}
