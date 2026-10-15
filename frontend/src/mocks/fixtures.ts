import type { Agent, BenchResults, GraphEdge, GraphNode, Procedure } from '../api/schemas';

/* Everything here is synthetic scripted data for mock mode. It never ships as real output. */

export const POLICY_URL = 'https://acme.example/policies/refunds';

export const root = { id: 'agent_root', name: 'Policy owner', tier: 'root' as const };
export const analyst = { id: 'agent_analyst', name: 'Analyst', tier: 'established' as const };
export const refunder = { id: 'agent_refunder', name: 'Refunder', tier: 'established' as const };
export const comms = { id: 'agent_comms', name: 'Comms', tier: 'established' as const };

export const AGENTS: Agent[] = [
  { id: 'agent_root', name: 'Policy owner', role: 'Owns the refund policy', tier: 'root' },
  { id: 'agent_scout', name: 'Scout', role: 'Reads feeds and tickets', tier: 'new' },
  { id: 'agent_analyst', name: 'Analyst', role: 'Draws conclusions from facts', tier: 'established' },
  { id: 'agent_refunder', name: 'Refunder', role: 'Issues refunds', tier: 'established' },
  { id: 'agent_comms', name: 'Comms', role: 'Emails customers', tier: 'established' },
];

export function seedGraph(now: number): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const t = now - 86_400_000;
  const nodes: GraphNode[] = [
    {
      id: 'src_policy_v3',
      kind: 'source',
      label: 'Refund policy v3',
      status: 'HEALTHY',
      stale: false,
      origin: 'doc:policy-portal',
      owner_team: 'Support Ops',
      valid_from: t,
      source: { kind: 'DOC', title: 'Refund policy', url: POLICY_URL, version: '3' },
    },
    {
      id: 'fact_30d',
      kind: 'fact',
      label: 'Refund window is 30 days',
      text: 'Refunds are allowed within 30 days of purchase.',
      key: 'refund_window_days',
      value: '30',
      status: 'HEALTHY',
      stale: false,
      valid_from: t,
      valid_to: null,
      source_ref: 'doc:policy-portal#refunds',
      quote: 'Refunds are allowed within 30 days.',
      origin: 'doc:policy-portal',
      asserted_by: root,
    },
    {
      id: 'conc_policy_summary',
      kind: 'conclusion',
      label: 'Standard refunds close at 30 days',
      text: 'Standard refunds close at 30 days.',
      status: 'HEALTHY',
      stale: false,
      valid_from: t,
    },
    {
      id: 'dec_macro',
      kind: 'conclusion',
      is_decision: true,
      label: 'Support macro quotes the 30-day window',
      text: 'The support macro tells customers refunds close at 30 days.',
      status: 'HEALTHY',
      stale: false,
      owner_team: 'Support Ops',
      valid_from: t,
    },
    ...[
      ['1042', 75],
      ['1043', 82],
      ['1044', 88],
    ].flatMap(([id, age]) => [
      {
        id: `src_ticket_${id}`,
        kind: 'source' as const,
        label: `Ticket ${id}`,
        status: 'HEALTHY' as const,
        stale: false,
        origin: 'ticket:helpdesk',
        owner_team: 'Support Ops',
        valid_from: t,
        source: { kind: 'TICKET' as const, title: `Ticket ${id}`, url: `https://acme.example/tickets/${id}`, version: '1' },
      },
      {
        id: `fact_t${id}`,
        kind: 'fact' as const,
        label: `Order ${id} is ${age} days old`,
        text: `Order ${id} was placed ${age} days ago.`,
        key: `order_${id}_age_days`,
        value: String(age),
        status: 'HEALTHY' as const,
        stale: false,
        valid_from: t,
        valid_to: null,
        source_ref: `ticket:${id}`,
        quote: `Customer bought ${age} days ago and asks for a refund.`,
        origin: 'ticket:helpdesk',
        asserted_by: { id: 'agent_scout', name: 'Scout', tier: 'new' as const },
      },
    ]),
  ];
  const edges: GraphEdge[] = [
    { id: 'e_pol_30', source: 'src_policy_v3', target: 'fact_30d', type: 'FROM_SOURCE' },
    { id: 'e_30_sum', source: 'fact_30d', target: 'conc_policy_summary', type: 'LEADS_TO' },
    { id: 'e_sum_macro', source: 'conc_policy_summary', target: 'dec_macro', type: 'LEADS_TO' },
    ...['1042', '1043', '1044'].map((id) => ({
      id: `e_tk_${id}`,
      source: `src_ticket_${id}`,
      target: `fact_t${id}`,
      type: 'FROM_SOURCE' as const,
    })),
  ];
  return { nodes, edges };
}

export function seedProcedures(): Procedure[] {
  const rank = (s: number, u: number) => (s + 1) / (u + 2);
  return [
    {
      id: 'proc_refund_feed',
      task_type: 'refund',
      steps: ['Read the latest partner feed', 'Check order age against the feed window', 'Issue the refund'],
      uses: 14,
      successes: 11,
      failures: 3,
      rank_score: rank(11, 14),
      seeded: true,
    },
    {
      id: 'proc_verify_policy',
      task_type: 'refund',
      steps: ['Read the root policy document', 'Check order age against the policy window', 'Issue the refund'],
      uses: 6,
      successes: 5,
      failures: 1,
      rank_score: rank(5, 6),
      seeded: true,
    },
  ];
}

export const bench: BenchResults = {
  generated_at: Date.UTC(2026, 9, 11, 9, 30),
  params: {
    source: 'mock fixture, not a real run',
    agents: 30,
    read_write_mix: '70/30',
    poison_trials_per_depth: 20,
    seeds: '11, 22, 33',
    baselines: 'vector store with TTL; delete only the poisoned fact',
  },
  concurrency: { agents: 30, ops_per_s: 4210, p50_ms: 4.1, p95_ms: 11.8, p99_ms: 19.6 },
  handoff_race: { handoffs: 100, claimers: 30, single_winner_rate: 1 },
  invalidation_under_load: { violations: 0 },
  poison: [1, 2, 3].map((depth) => ({
    depth,
    systems: [
      { name: 'Quorum cascade', leaked_rate: 0, wrongly_quarantined: 0, ms_to_containment: 6 + depth * 2 },
      { name: 'Delete only the fact', leaked_rate: [0.31, 0.58, 0.8][depth - 1] ?? 0.5, wrongly_quarantined: 0, ms_to_containment: 3 },
      { name: 'Vector store with TTL', leaked_rate: 1, wrongly_quarantined: 0, ms_to_containment: 60_000 },
    ],
  })),
};

export const CYPHER = {
  derive: `MATCH (p:MemoryNode) WHERE p.id IN $parent_ids
WITH collect(p) AS parents
WHERE size(parents) = size($parent_ids) AND ALL(x IN parents WHERE x.status = 'HEALTHY')
CREATE (c:Conclusion:MemoryNode {id:$id, text:$text, status:'HEALTHY', stale:false, created_at:timestamp()})
WITH c, parents
UNWIND parents AS p
CREATE (p)-[:LEADS_TO]->(c)
RETURN c.id`,
  stage: `MATCH (leaf:Fact)-[:LEADS_TO*]->(a:Action {id:$id})
WHERE NOT (()-[:LEADS_TO]->(leaf))
RETURN DISTINCT leaf`,
  execute: `MATCH (a:Action {id:$id})
WHERE a.status='HEALTHY' AND a.executed=false AND a.gate='PASSED'
OPTIONAL MATCH (x:MemoryNode)-[:LEADS_TO|SUPPORTS*]->(a)
WHERE coalesce(x.status,'MISSING') <> 'HEALTHY'
WITH a, count(DISTINCT x) AS bad
WHERE bad = 0
SET a.executed = true, a.executed_at = timestamp(), a.confirmed = false
RETURN a.id`,
  cascade: `MATCH (r:Fact {id:$root_id})
MATCH p=(r)-[:LEADS_TO|SUPPORTS*0..]->(d:MemoryNode)
WITH d, min(length(p)) AS depth
SET d.status='QUARANTINED', d.quarantined_at=timestamp(), d.quarantine_root=$root_id
RETURN d.id, depth ORDER BY depth`,
  epoch: `MATCH (m:Meta) SET m.epoch = m.epoch + 1 RETURN m.epoch`,
  compensation: `MATCH (a:Action {quarantine_root:$root_id}) WHERE a.executed = true
MERGE (c:Compensation {id:'comp_' + a.id})
ON CREATE SET c.status='PENDING_COMPENSATION', c.attempts=0, c.target_action_id=a.id
RETURN c.id`,
  supersede: `MATCH (old:Fact {id:$old_id}) WHERE old.status='HEALTHY' AND old.valid_to IS NULL
SET old.valid_to=timestamp(), old.status='SUPERSEDED'
CREATE (new:Fact:MemoryNode {id:$new_id, key:old.key, value:$value, text:$text, status:'HEALTHY',
        embedding:vecf32($embedding), valid_from:timestamp()})
CREATE (new)-[:SUPERSEDES]->(old)
RETURN new.id`,
  stale: `MATCH (old:Fact {id:$old_id})-[:LEADS_TO*]->(d:Conclusion)
WHERE d.status='HEALTHY' SET d.stale = true RETURN d.id`,
  restore: `MATCH (n:MemoryNode {status:'QUARANTINED'})
WHERE NOT (n:Action AND n.executed = true)
  AND ALL(p IN [(q)-[:LEADS_TO|SUPPORTS]->(n) | q] WHERE p.status='HEALTHY')
SET n.status='HEALTHY', n.released_at=timestamp()
RETURN n.id`,
  claim: `MATCH (h:Handoff {id:$id})
OPTIONAL MATCH (h)-[:CONTEXT_ROOT]->(anchor:MemoryNode)
WITH h, anchor
WHERE (h.status='PENDING' OR (h.status='IN_PROGRESS' AND h.lease_until < timestamp()))
  AND (h.kind <> 'TASK' OR anchor.status='HEALTHY')
SET h.status='IN_PROGRESS', h.claimed_by=$agent_id, h.claimed_at=timestamp(), h.lease_until=timestamp()+60000
RETURN h`,
};
