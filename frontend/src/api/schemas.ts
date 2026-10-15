import { z } from 'zod';
import { devLog } from './devlog';

export const CONTRACT_VERSION = '0.1.0';

/** Accepts unknown enum values and maps them to UNKNOWN, logging once. */
export function lenientEnum<const T extends readonly [string, ...string[]]>(name: string, values: T) {
  return z.string().transform((v): T[number] | 'UNKNOWN' => {
    if ((values as readonly string[]).includes(v)) return v as T[number];
    devLog('warn', `Unknown ${name} value "${v}" rendered neutrally`, true);
    return 'UNKNOWN';
  });
}

const Ms = z.number();

export const Status = lenientEnum('status', ['HEALTHY', 'SUPERSEDED', 'QUARANTINED'] as const);
export const NodeKind = lenientEnum('node kind', ['source', 'fact', 'conclusion', 'action'] as const);
export const EdgeType = lenientEnum('edge type', [
  'LEADS_TO',
  'SUPPORTS',
  'CONTRADICTS',
  'SUPERSEDES',
  'FROM_SOURCE',
] as const);
export const Tier = lenientEnum('tier', ['root', 'established', 'new'] as const);
export const CompStatus = lenientEnum('compensation status', [
  'PENDING_COMPENSATION',
  'DISPATCHING',
  'COMPENSATED',
  'FAILED',
] as const);

export const CorrectionNotice = z
  .object({
    target_action_id: z.string(),
    recipient: z.string().nullish().transform((v) => v ?? "Human input required"),
    original_subject: z.string().nullish().transform((v) => v ?? "Not recorded"),
    original_statement: z.string(),
    correction_subject: z.string(),
    drafted_body: z.string(),
    verified_policy_citation: z.string(),
    status: z.string(),
  })
  .passthrough();
export type CorrectionNotice = z.infer<typeof CorrectionNotice>;

export const ActionInfo = z
  .object({
    type: z.string(),
    reversibility: lenientEnum('reversibility', ['REVERSIBLE', 'COMPENSABLE', 'IRREVERSIBLE'] as const),
    executed: z.boolean(),
    confirmed: z.boolean(),
    gate: lenientEnum('gate', ['PASSED', 'BLOCKED'] as const),
    procedure_id: z.string().nullish(),
    compensation: z
      .object({ status: CompStatus, attempts: z.number() })
      .passthrough()
      .nullish(),
    correction_notice: CorrectionNotice.nullish(),
  })
  .passthrough();

export const GraphNode = z
  .object({
    id: z.string(),
    kind: NodeKind,
    is_decision: z.boolean().nullish().transform((v) => v ?? undefined),
    label: z.string(),
    text: z.string().nullish().transform((v) => v ?? undefined),
    key: z.string().nullish().transform((v) => v ?? undefined),
    value: z.string().nullish().transform((v) => v ?? undefined),
    status: Status,
    stale: z.boolean(),
    valid_from: Ms.nullish().transform((v) => v ?? undefined),
    valid_to: Ms.nullish(),
    quarantined_at: Ms.nullish(),
    released_at: Ms.nullish(),
    quarantine_root: z.string().nullish(),
    depth: z.number().nullish(),
    source_ref: z.string().nullish().transform((v) => v ?? undefined),
    quote: z.string().nullish().transform((v) => v ?? undefined),
    origin: z.string().nullish().transform((v) => v ?? undefined),
    asserted_by: z.object({ id: z.string(), name: z.string(), tier: Tier }).passthrough().nullish().transform((v) => v ?? undefined),
    source: z
      .object({
        kind: lenientEnum('source kind', ['DOC', 'TICKET', 'CHAT', 'WEBHOOK'] as const),
        title: z.string(),
        url: z.string(),
        version: z.string(),
      })
      .passthrough()
      .nullish().transform((v) => v ?? undefined),
    owner_team: z.string().nullish().transform((v) => v ?? undefined),
    action: ActionInfo.nullish().transform((v) => v ?? undefined),
  })
  .passthrough();
export type GraphNode = z.infer<typeof GraphNode>;

export const GraphEdge = z
  .object({ id: z.string(), source: z.string(), target: z.string(), type: EdgeType })
  .passthrough();
export type GraphEdge = z.infer<typeof GraphEdge>;

export const GraphResponse = z
  .object({
    contract_version: z.string(),
    tenant: z.string(),
    epoch: z.number(),
    seq: z.number(),
    as_of: Ms.nullable(),
    generated_at: Ms,
    truncated: z.boolean(),
    nodes: z.array(GraphNode),
    edges: z.array(GraphEdge),
  })
  .passthrough();
export type GraphResponse = z.infer<typeof GraphResponse>;

export const Handoff = z
  .object({
    id: z.string(),
    kind: lenientEnum('handoff kind', ['TASK', 'REVIEW', 'ESCALATION'] as const),
    task: z.string(),
    status: lenientEnum('handoff status', ['PENDING', 'IN_PROGRESS', 'COMPLETED', 'TIMED_OUT'] as const),
    assigned_to: z.string().nullish().transform((v) => v ?? undefined),
    claimed_by: z.string().nullish().transform((v) => v ?? undefined),
    claimed_at: Ms.nullish().transform((v) => v ?? undefined),
    lease_until: Ms.nullish().transform((v) => v ?? undefined),
    context_root_id: z.string().nullish().transform((v) => v ?? undefined),
    correction_notice: CorrectionNotice.nullish(),
  })
  .passthrough();
export type Handoff = z.infer<typeof Handoff>;

export const Meta = z
  .object({
    contract_version: z.string(),
    mode: lenientEnum('mode', ['replay', 'live'] as const),
    tenant: z.string(),
    role: z.string(),
    epoch: z.number(),
    seq: z.number(),
    replay_available: z.boolean(),
  })
  .passthrough();
export type Meta = z.infer<typeof Meta>;

export const NodeDetail = z
  .object({
    node: GraphNode,
    path: z.array(GraphNode),
    descendant_count: z.number(),
    status_history: z.array(z.object({ ts: Ms, status: Status, reason: z.string() }).passthrough()),
  })
  .passthrough();
export type NodeDetail = z.infer<typeof NodeDetail>;

export const ScenarioAck = z.object({ run_id: z.string(), accepted: z.boolean() }).passthrough();

export const BlastPreview = z
  .object({
    root_id: z.string(),
    node_ids: z.array(z.string()),
    facts: z.number(),
    conclusions: z.number(),
    actions: z
      .object({
        staged: z.number(),
        executed_reversible: z.number(),
        executed_compensable: z.number(),
        executed_irreversible: z.number(),
      })
      .passthrough(),
  })
  .passthrough();
export type BlastPreview = z.infer<typeof BlastPreview>;

export const BlastReport = z
  .object({
    root_id: z.string(),
    quarantined_at: Ms,
    ms_to_containment: z.number(),
    facts: z.number(),
    conclusions: z.number(),
    actions_cancelled: z.number(),
    actions_compensated: z.number(),
    actions_escalated: z.number(),
    node_ids_by_depth: z.array(z.array(z.string())),
  })
  .passthrough();
export type BlastReport = z.infer<typeof BlastReport>;

export const Agent = z
  .object({ id: z.string(), name: z.string(), role: z.string(), tier: Tier, last_active: Ms.nullish().transform((v) => v ?? undefined) })
  .passthrough();
export type Agent = z.infer<typeof Agent>;

export const Resume = z
  .object({
    session_id: z.string(),
    summary: z.string(),
    memories: z.array(
      z.object({ fact_id: z.string(), text: z.string(), source_ref: z.string(), quote: z.string() }).passthrough(),
    ),
  })
  .passthrough();
export type Resume = z.infer<typeof Resume>;

export const Procedure = z
  .object({
    id: z.string(),
    task_type: z.string(),
    steps: z.array(z.string()),
    uses: z.number(),
    successes: z.number(),
    failures: z.number(),
    rank_score: z.number(),
    seeded: z.boolean(),
  })
  .passthrough();
export type Procedure = z.infer<typeof Procedure>;

const DiffItem = z.object({ node_id: z.string(), text: z.string(), status: Status }).passthrough();
export const PromptDiff = z
  .object({
    epoch_before: z.number(),
    epoch_after: z.number(),
    before: z.array(DiffItem),
    after: z.array(DiffItem),
    removed_ids: z.array(z.string()),
  })
  .passthrough();
export type PromptDiff = z.infer<typeof PromptDiff>;

export const BrainAnswer = z
  .object({
    refused: z.boolean(),
    reason: z.string().nullish().transform((v) => v ?? undefined),
    answer: z.string().nullish().transform((v) => v ?? undefined),
    citations: z.array(
      z
        .object({
          source_title: z.string(),
          url: z.string(),
          version: z.string(),
          quote: z.string(),
          owner_team: z.string(),
          valid_from: Ms,
          stale: z.boolean(),
        })
        .passthrough(),
    ),
  })
  .passthrough();
export type BrainAnswer = z.infer<typeof BrainAnswer>;

export const WhoKnows = z.array(
  z
    .object({ person: z.string(), team: z.string(), evidence_count: z.number(), source_ids: z.array(z.string()) })
    .passthrough(),
);

export const DecisionTrace = z.array(
  z
    .object({
      claim: z.string(),
      quote: z.string(),
      source_title: z.string(),
      url: z.string(),
      version: z.string(),
      owner: z.string(),
    })
    .passthrough(),
);

export const Invariants = z
  .object({
    ok: z.boolean(),
    checked_at: Ms,
    checks: z.array(z.object({ name: z.string(), ok: z.boolean(), value: z.number() }).passthrough()),
  })
  .passthrough();
export type Invariants = z.infer<typeof Invariants>;

export const CypherLog = z.array(
  z
    .object({
      ts: Ms,
      request_id: z.string().nullish().transform((v) => v ?? undefined),
      name: z.string(),
      cypher: z.string(),
      origin: z.enum(['falkordb', 'memory', 'illustrative']).catch('illustrative'),
      outcome: z.enum(['success', 'failure']).catch('success'),
      timing_boundary: z.string().optional(),
      ms: z.number(),
      rows: z.number(),
    })
    .passthrough(),
);
export type CypherLogEntry = z.infer<typeof CypherLog>[number];

export const BenchResults = z
  .object({
    generated_at: Ms,
    params: z.record(z.union([z.string(), z.number()])),
    concurrency: z
      .object({ agents: z.number(), ops_per_s: z.number(), p50_ms: z.number(), p95_ms: z.number(), p99_ms: z.number() })
      .passthrough(),
    handoff_race: z
      .object({ handoffs: z.number(), claimers: z.number(), single_winner_rate: z.number() })
      .passthrough(),
    invalidation_under_load: z.object({ violations: z.number() }).passthrough(),
    poison: z.array(
      z
        .object({
          depth: z.number(),
          systems: z.array(
            z
              .object({
                name: z.string(),
                leaked_rate: z.number(),
                wrongly_quarantined: z.number(),
                ms_to_containment: z.number().nullish().transform((v) => v ?? undefined),
              })
              .passthrough(),
          ),
        })
        .passthrough(),
    ),
  })
  .passthrough();
export type BenchResults = z.infer<typeof BenchResults>;

export const DeniedDemo = z
  .object({
    tenant: z.string(),
    attempted_graph: z.string(),
    result: lenientEnum('denied-demo result', ['DENIED', 'ALLOWED'] as const),
    detail: z.string(),
    visible_graph_names: z.array(z.string()),
  })
  .passthrough();
export type DeniedDemo = z.infer<typeof DeniedDemo>;

export const ErrorBody = z
  .object({
    error: z
      .object({ code: z.string(), message: z.string(), details: z.record(z.unknown()).nullish().transform((v) => v ?? undefined) })
      .passthrough(),
  })
  .passthrough();

/** One MCP tool call, as the backend records it. Presence, the feed, chats and token savings are all derived from these. */
export const McpCall = z
  .object({
    id: z.number(),
    ts: Ms,
    tool: z.string(),
    agent_id: z.string().nullish().transform((v) => v ?? undefined),
    member_id: z.string().nullish().transform((v) => v ?? undefined),
    role: z.string().nullish().transform((v) => v ?? undefined),
    summary: z.string().nullish().transform((v) => v ?? ''),
    tokens: z.number(),
    baseline: z.number(),
    ms: z.number().nullish().transform((v) => v ?? 0),
    ok: z.boolean(),
    error: z.string().nullish().transform((v) => v ?? undefined),
  })
  .passthrough();
export type McpCall = z.infer<typeof McpCall>;

export const TokenStats = z
  .object({
    calls: z.number(),
    measured_calls: z.number(),
    tokens: z.number(),
    baseline: z.number(),
    saved: z.number(),
    saved_pct: z.number(),
    by_tool: z.array(z.object({ name: z.string(), calls: z.number(), tokens: z.number(), baseline: z.number(), saved: z.number() }).passthrough()),
    by_agent: z.array(z.object({ name: z.string(), calls: z.number(), tokens: z.number(), baseline: z.number(), saved: z.number() }).passthrough()),
    method: z.string(),
  })
  .passthrough();
export type TokenStats = z.infer<typeof TokenStats>;

export const ContextPreview = z
  .object({
    nodes: z.number(),
    healthy: z.number(),
    query: z.string(),
    budget: z.number(),
    raw_json_tokens: z.number(),
    prose_tokens: z.number(),
    top8_tokens: z.number(),
    packed_tokens: z.number(),
    map_tokens: z.number(),
    packed: z.string(),
    map: z.string(),
    included: z.number(),
    dropped: z.number(),
    stages: z.array(z.object({ label: z.string(), tokens: z.number(), note: z.string() })),
  })
  .passthrough();
export type ContextPreview = z.infer<typeof ContextPreview>;

/* SSE envelope + typed events */
const base = { seq: z.number(), ts: Ms, epoch: z.number(), request_id: z.string().nullish().transform((v) => v ?? undefined) };
const ev = <T extends string, D extends z.ZodTypeAny>(type: T, data: D) =>
  z.object({ ...base, type: z.literal(type), data }).passthrough();

export const SseEvent = z.discriminatedUnion('type', [
  ev('node_added', z.object({ node: GraphNode }).passthrough()),
  ev('edge_added', z.object({ edge: GraphEdge }).passthrough()),
  ev(
    'status_changed',
    z
      .object({
        id: z.string(),
        status: Status,
        stale: z.boolean().nullish().transform((v) => v ?? undefined),
        depth: z.number().nullish().transform((v) => v ?? undefined),
        root_id: z.string().nullish().transform((v) => v ?? undefined),
      })
      .passthrough(),
  ),
  ev(
    'epoch_changed',
    z
      .object({ epoch: z.number(), reason: lenientEnum('epoch reason', ['quarantine', 'restore', 'supersede', 'reset'] as const) })
      .passthrough(),
  ),
  ev('gate_blocked', z.object({ action_id: z.string(), handoff_id: z.string(), reason: z.string() }).passthrough()),
  ev('action_executed', z.object({ action_id: z.string(), confirmed: z.boolean() }).passthrough()),
  ev(
    'compensation_updated',
    z.object({ action_id: z.string(), status: CompStatus, attempts: z.number() }).passthrough(),
  ),
  ev(
    'handoff_claimed',
    z
      .object({ handoff_id: z.string(), winner: z.string(), losers: z.array(z.string()), lease_until: Ms })
      .passthrough(),
  ),
  ev('handoff_updated', z.object({ handoff: Handoff }).passthrough()),
  ev('invariant_status', z.object({ ok: z.boolean(), violations: z.number() }).passthrough()),
  ev(
    'scenario_progress',
    z
      .object({
        run_id: z.string(),
        step: z.string(),
        state: lenientEnum('scenario state', ['started', 'finished', 'failed'] as const),
        detail: z.string().nullish().transform((v) => v ?? undefined),
      })
      .passthrough(),
  ),
  ev('resync_required', z.object({}).passthrough()),
  ev('heartbeat', z.object({}).passthrough()),
  ev('mcp_call', z.object({ call: McpCall }).passthrough()),
  ev('workspace_changed', z.object({}).passthrough()),
  // A sequenced event this console does not know yet. It keeps seq contiguous so one new backend event type cannot stall the stream.
  ev('unknown', z.object({ original_type: z.string() }).passthrough()),
]);
export type SseEvent = z.infer<typeof SseEvent>;
