import type { View } from '../state/store';

export interface Term {
  term: string;
  plain: string;
  /** a screen where the user can see this for real */
  see?: View;
}

/** Every word the console uses, in plain English, sorted A to Z. Keep each definition to what a new team member needs. */
export const GLOSSARY: Term[] = [
  { term: 'Action', plain: 'Something an AI wants to do in the outside world, such as issuing a refund. It is staged first, then executed only if every belief behind it is still healthy.', see: 'console' },
  { term: 'Agent', plain: 'An AI assistant (Claude, Cursor, a support bot) connected to the shared memory. Each agent has its own token so you can see what it did.', see: 'team' },
  { term: 'Audit log', plain: 'The plain-English record of every lookup, save and refusal. It can be exported as a spreadsheet.', see: 'activity' },
  { term: 'Auditor', plain: 'A read-only person or AI that can inspect memory and quarantine bad beliefs, but cannot add facts.', see: 'team' },
  { term: 'Belief', plain: 'Anything the memory holds as true: a source, a fact, a conclusion or an action. If one is wrong, everything built on it is suspect.', see: 'console' },
  { term: 'Blast radius', plain: 'Everything that would be affected if one belief were quarantined. Preview it before you act.', see: 'console' },
  { term: 'Compensation', plain: 'The automatic undo for an action that already happened, such as reversing a refund that was based on a poisoned fact.' },
  { term: 'Conclusion', plain: 'Something an agent worked out from facts, such as "this order is eligible for a refund". It inherits trouble from the facts it used.', see: 'console' },
  { term: 'Connect', plain: 'The screen that gives you a snippet to paste into an AI tool so it can use the shared memory.', see: 'connect' },
  { term: 'Context', plain: 'The small set of beliefs sent to an agent for one question. Quarantined beliefs are never included.', see: 'context' },
  { term: 'Corroboration', plain: 'Two or more independent sources saying the same thing. The gate wants this before it lets a fact drive an action.' },
  { term: 'Descendant', plain: 'A belief that was built from another one. Quarantine follows these links downward.', see: 'console' },
  { term: 'Execute guard', plain: 'The last check before an action runs. If anything behind the action was quarantined a moment ago, the action is refused.' },
  { term: 'Fact', plain: 'A single statement recorded from a source, for example "the refund window is 30 days".', see: 'console' },
  { term: 'FalkorDB', plain: 'The graph database that stores the memory and the links between beliefs.' },
  { term: 'Gate (Quorum gate)', plain: 'The check that stops an unsupported fact from being used for an action. It wants a trusted root or two agreeing sources.' },
  { term: 'Handoff', plain: 'A task passed from one agent to another, or to a person for review. Only one claimer can win it.', see: 'console' },
  { term: 'Healthy', plain: 'A belief that nobody has flagged and that nothing newer has replaced. Healthy beliefs are what agents see.' },
  { term: 'Hub', plain: 'The Quorum server that agents talk to. The Overview page tells you whether it is running.', see: 'overview' },
  { term: 'Idempotency key', plain: 'A stable label on a request so that retrying it never does the same thing twice.' },
  { term: 'MCP', plain: 'Model Context Protocol, the standard way AI tools connect to outside services. Quorum speaks it.', see: 'connect' },
  { term: 'Memory version', plain: 'A counter that goes up whenever something is quarantined, restored or replaced. When it changes, agents rebuild their context before acting.' },
  { term: 'Node', plain: 'One box on the graph: a source, fact, conclusion or action.', see: 'console' },
  { term: 'Outbox worker', plain: 'The background job that carries out reversals reliably, retrying until the outside system confirms.' },
  { term: 'Poisoned', plain: 'A belief that is wrong or malicious, such as a fake webhook claiming refunds last 90 days.' },
  { term: 'Provenance', plain: 'Where a belief came from. Click any node and the inspector shows the chain back to its source.', see: 'console' },
  { term: 'Quarantine', plain: 'Marking a belief and everything built on it as unusable. Agents stop seeing it until a person restores it.', see: 'console' },
  { term: 'Restore', plain: 'Bringing a quarantined belief back. It needs a person with permission.', see: 'console' },
  { term: 'Retrieval', plain: 'An agent asking memory for what it needs. Each one appears in the activity log as "looked up".', see: 'activity' },
  { term: 'Review', plain: 'A handoff that waits for a person to approve before an agent can continue.', see: 'overview' },
  { term: 'Safety checks', plain: 'Automatic tests that the memory is consistent, for example that nothing healthy sits underneath something quarantined.' },
  { term: 'Source', plain: 'The document, webhook or chat that a fact came from.', see: 'console' },
  { term: 'Staged action', plain: 'An action that is prepared but not yet run. It waits for the execute guard.' },
  { term: 'Supersede', plain: 'Replace an old belief with a newer one, for example a new policy. The old one stays in history but is no longer used.', see: 'console' },
  { term: 'Tenant', plain: 'One company or workspace with its own private memory. Switching tenant never mixes data.', see: 'tenants' },
  { term: 'Token', plain: 'Two meanings. A login key given to a person or agent, and a small unit of text that AI models are billed on.', see: 'team' },
  { term: 'Tokens saved', plain: 'How much text Quorum did not have to send to the AI compared with a typical search tool. Less text is cheaper and clearer.', see: 'context' },
  { term: 'Tombstone', plain: 'A marker left in the outside system so that a reversed action can never be replayed.' },
];

export const GLOSSARY_SORTED = [...GLOSSARY].sort((a, b) => a.term.localeCompare(b.term));
