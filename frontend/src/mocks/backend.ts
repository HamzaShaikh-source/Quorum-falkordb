import type { GraphEdge, GraphNode, Handoff, Procedure } from '../api/schemas';
import { CONTRACT_VERSION } from '../api/schemas';
import { AGENTS, CYPHER, POLICY_URL, analyst, bench, comms, refunder, root, seedGraph, seedProcedures } from './fixtures';

/*
 * In-browser stand-in for the Quorum backend. It speaks the same REST + SSE
 * shapes as docs/CONTRACT.md so every response still goes through the zod
 * schemas. The UI shows a permanent "Mock data" banner while this is active.
 */

type Role = 'human' | 'auditor' | 'agent';
type Raw = Record<string, unknown>;

export interface MockRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  token: string | null;
}
export interface MockResponse {
  status: number;
  body: unknown;
  requestId: string;
}
export interface Envelope {
  seq: number;
  ts: number;
  epoch: number;
  type: string;
  request_id?: string;
  data: Raw;
}

interface Snapshot {
  ts: number;
  seq: number;
  epoch: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
}
interface CypherEntry {
  ts: number;
  request_id?: string;
  name: string;
  cypher: string;
  ms: number;
  rows: number;
}
interface Report {
  root_id: string;
  quarantined_at: number;
  ms_to_containment: number;
  facts: number;
  conclusions: number;
  actions_cancelled: number;
  actions_compensated: number;
  actions_escalated: number;
  node_ids_by_depth: string[][];
}

const clone = <T>(v: T): T => structuredClone(v);
const err = (status: number, code: string, message: string, requestId: string, details?: Raw): MockResponse => ({
  status,
  requestId,
  body: { error: { code, message, details } },
});
const ok = (body: unknown, requestId: string, status = 200): MockResponse => ({ status, body, requestId });

let ridCounter = 0;
const newRid = () => `req_${(++ridCounter).toString(16).padStart(4, '0')}`;

class Tenant {
  nodes = new Map<string, GraphNode>();
  edges = new Map<string, GraphEdge>();
  handoffs = new Map<string, Handoff>();
  seq = 100;
  epoch = 1;
  events: Envelope[] = [];
  snapshots: Snapshot[] = [];
  statusLog: Record<string, { ts: number; status: string; reason: string }[]> = {};
  cypher: CypherEntry[] = [];
  procedures: Procedure[] = [];
  reports: Record<string, Report> = {};
  flags = { poisoned: false, ran: false, audited: false, superseded: false, restored: false };
  contextBefore: { epoch: number; items: { node_id: string; text: string; status: string }[] } | null = null;
  running: string | null = null;
  gen = 0;
  subs = new Set<(e: Envelope) => void>();

  constructor(readonly name: string, seeded: boolean) {
    this.load(seeded);
  }

  load(seeded: boolean) {
    this.nodes.clear();
    this.edges.clear();
    this.handoffs.clear();
    this.statusLog = {};
    this.cypher = [];
    this.reports = {};
    this.contextBefore = null;
    this.flags = { poisoned: false, ran: false, audited: false, superseded: false, restored: false };
    this.procedures = seeded ? seedProcedures() : [];
    if (seeded) {
      const g = seedGraph(Date.now());
      g.nodes.forEach((n) => {
        this.nodes.set(n.id, n);
        this.statusLog[n.id] = [{ ts: n.valid_from ?? Date.now(), status: 'HEALTHY', reason: 'Created' }];
      });
      g.edges.forEach((e) => this.edges.set(e.id, e));
    }
    this.snapshots = [];
    this.snap();
  }

  /* ---- plumbing ---- */
  snap() {
    const s: Snapshot = {
      ts: Date.now(),
      seq: this.seq,
      epoch: this.epoch,
      nodes: clone([...this.nodes.values()]),
      edges: clone([...this.edges.values()]),
    };
    this.snapshots = [...this.snapshots.slice(-399), s];
  }

  emit(type: string, data: Raw, rid?: string) {
    this.seq += 1;
    const env: Envelope = { seq: this.seq, ts: Date.now(), epoch: this.epoch, type, request_id: rid, data };
    this.events = [...this.events.slice(-799), env];
    this.snap();
    this.subs.forEach((cb) => cb(env));
  }

  later(ms: number, fn: () => void) {
    const g = this.gen;
    setTimeout(() => {
      if (g === this.gen) fn();
    }, ms);
  }

  log(rid: string, name: string, cypher: string, rows: number) {
    const ms = Math.max(1, (name.length % 5) + 1);
    this.cypher.push({ ts: Date.now(), request_id: rid, name, cypher, ms, rows });
  }

  /* ---- mutators that also emit ---- */
  addNode(n: GraphNode, rid: string) {
    this.nodes.set(n.id, n);
    this.statusLog[n.id] = [{ ts: Date.now(), status: n.status, reason: 'Created' }];
    this.emit('node_added', { node: clone(n) }, rid);
  }
  addEdge(source: string, target: string, type: GraphEdge['type'], rid: string) {
    const e: GraphEdge = { id: `e_${source}_${target}_${type}`.toLowerCase(), source, target, type };
    this.edges.set(e.id, e);
    this.emit('edge_added', { edge: e }, rid);
  }
  setStatus(
    id: string,
    status: 'HEALTHY' | 'QUARANTINED' | 'SUPERSEDED',
    rid: string,
    o: { depth?: number; root?: string; stale?: boolean; reason?: string } = {},
  ) {
    const n = this.nodes.get(id);
    if (!n) return;
    const was = n.status;
    n.status = status;
    if (o.stale !== undefined) n.stale = o.stale;
    if (o.depth !== undefined) n.depth = o.depth;
    const ts = Date.now();
    if (status === 'QUARANTINED') {
      n.quarantined_at = ts;
      n.released_at = null;
      n.quarantine_root = o.root ?? null;
    } else if (status === 'HEALTHY' && was === 'QUARANTINED') {
      n.released_at = ts;
      n.quarantine_root = null;
    } else if (status === 'SUPERSEDED') {
      n.valid_to = ts;
    }
    if (was !== status || o.stale !== undefined) {
      (this.statusLog[id] ??= []).push({
        ts,
        status,
        reason: o.reason ?? (o.stale ? 'Marked out of date' : `Changed from ${was}`),
      });
    }
    const data: Raw = { id, status };
    if (o.stale !== undefined) data.stale = o.stale;
    if (o.depth !== undefined) data.depth = o.depth;
    if (o.root) data.root_id = o.root;
    this.emit('status_changed', data, rid);
  }
  execute(id: string, confirmed: boolean, rid: string) {
    const n = this.nodes.get(id);
    if (!n?.action) return;
    n.action.executed = true;
    n.action.confirmed = confirmed;
    this.emit('action_executed', { action_id: id, confirmed }, rid);
  }
  compensate(id: string, status: 'PENDING_COMPENSATION' | 'DISPATCHING' | 'COMPENSATED' | 'FAILED', attempts: number, rid: string) {
    const n = this.nodes.get(id);
    if (!n?.action) return;
    n.action.compensation = { status, attempts };
    this.emit('compensation_updated', { action_id: id, status, attempts }, rid);
  }
  upsertHandoff(h: Handoff, rid: string) {
    this.handoffs.set(h.id, h);
    this.emit('handoff_updated', { handoff: clone(h) }, rid);
  }
  progress(rid: string, step: string, state: 'started' | 'finished' | 'failed', detail?: string) {
    if (state === 'started') this.running = step;
    else if (this.running === step) this.running = null;
    this.emit('scenario_progress', { run_id: rid, step, state, ...(detail ? { detail } : {}) }, rid);
  }

  /* ---- graph helpers ---- */
  outgoing(id: string, types: string[] = ['LEADS_TO', 'SUPPORTS']) {
    return [...this.edges.values()].filter((e) => e.source === id && types.includes(e.type));
  }
  incoming(id: string, types: string[] = ['LEADS_TO', 'SUPPORTS']) {
    return [...this.edges.values()].filter((e) => e.target === id && types.includes(e.type));
  }
  descendants(rootId: string): Map<string, number> {
    const depth = new Map<string, number>([[rootId, 0]]);
    const queue = [rootId];
    while (queue.length) {
      const id = queue.shift() as string;
      for (const e of this.outgoing(id)) {
        if (!depth.has(e.target)) {
          depth.set(e.target, (depth.get(id) ?? 0) + 1);
          queue.push(e.target);
        }
      }
    }
    return depth;
  }
  ancestors(id: string): string[] {
    const seen = new Set<string>();
    const stack = [id];
    while (stack.length) {
      const cur = stack.pop() as string;
      for (const e of this.incoming(cur, ['LEADS_TO', 'SUPPORTS', 'FROM_SOURCE'])) {
        if (!seen.has(e.source)) {
          seen.add(e.source);
          stack.push(e.source);
        }
      }
    }
    return [...seen];
  }
  healthyContext() {
    return [...this.nodes.values()]
      .filter((n) => (n.kind === 'fact' || n.kind === 'conclusion') && n.status === 'HEALTHY')
      .map((n) => ({ node_id: n.id, text: n.text ?? n.label, status: n.status as string }));
  }

  /* ---- scenario ---- */
  fail(rid: string, step: string, detail: string) {
    this.progress(rid, step, 'started');
    this.progress(rid, step, 'failed', detail);
    return false;
  }

  run(step: string, rid: string): boolean {
    if (this.running && step !== 'reset') return false;
    const seeded = this.nodes.has('fact_30d');
    if (step !== 'reset' && !seeded) return this.fail(rid, step, 'This tenant has no seeded scenario.');
    switch (step) {
      case 'reset':
        return this.reset(rid);
      case 'inject_poison':
        if (this.flags.poisoned) return this.fail(rid, step, 'Already injected. Reset the scenario to run it again.');
        return this.injectPoison(rid);
      case 'run_agents':
        if (!this.flags.poisoned) return this.fail(rid, step, 'Inject the poisoned webhook first.');
        if (this.flags.ran) return this.fail(rid, step, 'The agents already acted. Audit memory next.');
        return this.runAgents(rid);
      case 'audit':
        if (!this.flags.ran) return this.fail(rid, step, 'Run the agents first so there is something to audit.');
        if (this.flags.audited) return this.fail(rid, step, 'Already audited. Restore or reset to go again.');
        return this.audit(rid);
      case 'supersede':
        if (this.flags.superseded) return this.fail(rid, step, 'The policy was already replaced.');
        return this.supersede(rid);
      case 'restore':
        if (!this.flags.audited) return this.fail(rid, step, 'Nothing is quarantined yet.');
        if (this.flags.restored) return this.fail(rid, step, 'Already restored.');
        return this.restore(rid);
      default:
        return this.fail(rid, step, 'Unknown scenario step.');
    }
  }

  reset(rid: string) {
    this.gen += 1;
    this.running = null;
    this.load(true);
    this.epoch += 1;
    this.progress(rid, 'reset', 'started');
    this.emit('resync_required', {}, rid);
    this.progress(rid, 'reset', 'finished');
    return true;
  }

  injectPoison(rid: string) {
    this.flags.poisoned = true;
    this.progress(rid, 'inject_poison', 'started', 'Partner feed pushes a new refund window.');
    this.later(350, () =>
      this.addNode(
        {
          id: 'src_webhook',
          kind: 'source',
          label: 'Partner feed update',
          status: 'HEALTHY',
          stale: false,
          origin: 'webhook:partner-feed',
          owner_team: 'Partner Integrations',
          valid_from: Date.now(),
          source: { kind: 'WEBHOOK', title: 'Partner feed update', url: 'https://partners.example/feed/771', version: '1' },
        },
        rid,
      ),
    );
    this.later(750, () => {
      this.addNode(
        {
          id: 'fact_90d',
          kind: 'fact',
          label: 'Refund window is 90 days',
          text: 'Refunds are allowed within 90 days of purchase.',
          key: 'refund_window_days',
          value: '90',
          status: 'HEALTHY',
          stale: false,
          valid_from: Date.now(),
          valid_to: null,
          source_ref: 'webhook:partner-feed#771',
          quote: 'refund_window_days: 90',
          origin: 'webhook:partner-feed',
          asserted_by: { id: 'agent_scout', name: 'Scout', tier: 'new' },
        },
        rid,
      );
      this.addEdge('src_webhook', 'fact_90d', 'FROM_SOURCE', rid);
      this.log(rid, 'remember_fact', 'CREATE (f:Fact:MemoryNode {id:$id, key:$key, value:$value, status:\'HEALTHY\'})', 1);
    });
    this.later(1100, () => this.progress(rid, 'inject_poison', 'finished'));
    return true;
  }

  runAgents(rid: string) {
    this.flags.ran = true;
    this.progress(rid, 'run_agents', 'started', 'Analyst, Refunder and Comms act on what memory says.');
    const act = (id: string, label: string, type: string, rev: 'COMPENSABLE' | 'IRREVERSIBLE', parents: string[], proc?: string) => {
      this.addNode(
        {
          id,
          kind: 'action',
          label,
          status: 'HEALTHY',
          stale: false,
          valid_from: Date.now(),
          asserted_by: type === 'email' ? comms : refunder,
          action: { type, reversibility: rev, executed: false, confirmed: false, gate: 'PASSED', procedure_id: proc ?? null, compensation: null },
        },
        rid,
      );
      parents.forEach((p) => this.addEdge(p, id, 'LEADS_TO', rid));
      this.log(rid, 'stage_action', CYPHER.stage, parents.length);
    };
    this.later(400, () => {
      this.addNode(
        {
          id: 'conc_window',
          kind: 'conclusion',
          label: 'Orders up to 90 days old qualify',
          text: 'Orders up to 90 days old qualify for a refund.',
          status: 'HEALTHY',
          stale: false,
          valid_from: Date.now(),
          asserted_by: analyst,
        },
        rid,
      );
      this.addEdge('fact_90d', 'conc_window', 'LEADS_TO', rid);
      this.log(rid, 'derive', CYPHER.derive, 1);
    });
    this.later(850, () => {
      this.addNode(
        {
          id: 'dec_refund',
          kind: 'conclusion',
          is_decision: true,
          label: 'Approve refunds up to 90 days',
          text: 'Approve refunds for orders up to 90 days old.',
          status: 'HEALTHY',
          stale: false,
          owner_team: 'Support Ops',
          valid_from: Date.now(),
          asserted_by: analyst,
        },
        rid,
      );
      this.addEdge('conc_window', 'dec_refund', 'LEADS_TO', rid);
      this.log(rid, 'derive', CYPHER.derive, 1);
    });
    this.later(1350, () => act('act_refund_1042', 'Refund order 1042', 'refund', 'COMPENSABLE', ['dec_refund', 'fact_t1042'], 'proc_refund_feed'));
    this.later(1650, () => act('act_refund_1043', 'Refund order 1043', 'refund', 'COMPENSABLE', ['dec_refund', 'fact_t1043'], 'proc_refund_feed'));
    this.later(1950, () => act('act_refund_1044', 'Refund order 1044', 'refund', 'COMPENSABLE', ['dec_refund', 'fact_t1044'], 'proc_refund_feed'));
    this.later(2250, () => {
      act('act_email_cust', 'Email customer: refund approved', 'SEND_CUSTOMER_EMAIL', 'IRREVERSIBLE', ['dec_refund']);
      const emailCust = this.nodes.get('act_email_cust');
      if (emailCust?.action) {
        emailCust.action.executed = true;
        emailCust.action.confirmed = true;
        emailCust.action.gate = 'PASSED';
      }
      act('act_email', 'Email customers: 90-day window', 'email', 'IRREVERSIBLE', ['dec_refund']);
    });
    this.later(2600, () => {
      this.execute('act_refund_1042', false, rid);
      this.log(rid, 'execute_guard', CYPHER.execute, 1);
    });
    this.later(2900, () => this.execute('act_refund_1042', true, rid));
    this.later(3000, () => {
      this.execute('act_refund_1043', false, rid);
      this.log(rid, 'execute_guard', CYPHER.execute, 1);
    });
    this.later(3300, () => this.execute('act_refund_1043', true, rid));
    this.later(3600, () => {
      const email = this.nodes.get('act_email');
      if (email?.action) email.action.gate = 'BLOCKED';
      const h: Handoff = {
        id: 'hand_review_email',
        kind: 'REVIEW',
        task: 'Review: emailing customers a 90-day window',
        status: 'PENDING',
        assigned_to: 'Support Ops',
        context_root_id: 'act_email',
      };
      this.handoffs.set(h.id, h);
      this.emit(
        'gate_blocked',
        { action_id: 'act_email', handoff_id: h.id, reason: 'The 90-day fact has one source and no corroboration.' },
        rid,
      );
      this.emit('handoff_updated', { handoff: clone(h) }, rid);
    });
    this.later(4100, () =>
      this.upsertHandoff(
        { id: 'hand_task_verify', kind: 'TASK', task: 'Verify order 1044 against policy', status: 'PENDING', context_root_id: 'fact_30d' },
        rid,
      ),
    );
    this.later(4700, () => {
      const lease = Date.now() + 60_000;
      const h = this.handoffs.get('hand_task_verify');
      if (h) {
        h.status = 'IN_PROGRESS';
        h.claimed_by = 'agent_refunder';
        h.claimed_at = Date.now();
        h.lease_until = lease;
      }
      this.log(rid, 'claim_handoff', CYPHER.claim, 1);
      this.emit('handoff_claimed', { handoff_id: 'hand_task_verify', winner: 'agent_refunder', losers: ['agent_analyst'], lease_until: lease }, rid);
      if (h) this.emit('handoff_updated', { handoff: clone(h) }, rid);
    });
    this.later(5000, () => {
      this.emit('invariant_status', { ok: true, violations: 0 }, rid);
      this.progress(rid, 'run_agents', 'finished');
    });
    return true;
  }

  audit(rid: string) {
    this.flags.audited = true;
    this.progress(rid, 'audit', 'started', 'Auditor compares the 90-day claim with the root policy.');
    this.later(600, () => {
      this.addEdge('fact_90d', 'fact_30d', 'CONTRADICTS', rid);
      this.log(rid, 'detect_contradiction', 'MATCH (a:Fact),(b:Fact) WHERE a.key=b.key AND a.value<>b.value RETURN a.id, b.id', 1);
    });
    this.later(1400, () => {
      const before = this.healthyContext();
      this.contextBefore = { epoch: this.epoch, items: before };
      const t0 = performance.now();
      const depths = [...this.descendants('fact_90d').entries()].sort((a, b) => a[1] - b[1]);
      for (const [id, d] of depths) this.setStatus(id, 'QUARANTINED', rid, { depth: d, root: 'fact_90d', reason: 'Descends from fact_90d' });
      this.epoch += 1;
      this.emit('epoch_changed', { epoch: this.epoch, reason: 'quarantine' }, rid);
      const ms = Math.max(1, Math.round(performance.now() - t0));
      this.log(rid, 'cascade', CYPHER.cascade, depths.length);
      this.log(rid, 'bump_epoch', CYPHER.epoch, 1);
      const byDepth: string[][] = [];
      depths.forEach(([id, d]) => (byDepth[d] ??= []).push(id));
      const q = depths.map(([id]) => this.nodes.get(id) as GraphNode);
      const executed = q.filter((n) => n.kind === 'action' && n.action?.executed);
      this.reports.fact_90d = {
        root_id: 'fact_90d',
        quarantined_at: Date.now(),
        ms_to_containment: ms,
        facts: q.filter((n) => n.kind === 'fact').length,
        conclusions: q.filter((n) => n.kind === 'conclusion').length,
        actions_cancelled: q.filter((n) => n.kind === 'action' && !n.action?.executed).length,
        actions_compensated: executed.filter((n) => n.action?.reversibility !== 'IRREVERSIBLE').length,
        actions_escalated: executed.filter((n) => n.action?.reversibility === 'IRREVERSIBLE').length,
        node_ids_by_depth: byDepth,
      };
      const feed = this.procedures.find((p) => p.id === 'proc_refund_feed');
      if (feed) {
        feed.uses += 2;
        feed.failures += 2;
        feed.rank_score = (feed.successes + 1) / (feed.uses + 2);
        feed.seeded = false;
      }
      for (const n of executed) this.compensate(n.id, 'PENDING_COMPENSATION', 0, rid);
      this.log(rid, 'create_compensations', CYPHER.compensation, executed.length);
    });
    this.later(1900, () =>
      this.progress(
        rid,
        'audit',
        'started',
        'Refused by the execute guard: Refund order 1044 (fact_90d is quarantined).',
      ),
    );
    this.later(2200, () => {
      const email = this.nodes.get('act_email_cust') ?? this.nodes.get('act_email');
      const actionId = email?.id ?? 'act_email_cust';
      const notice = {
        target_action_id: actionId,
        recipient: 'customer@acme.com',
        original_subject: 'Update: Your Refund Request is Approved',
        original_statement: 'Your refund of $150 has been approved under our new 90-day policy.',
        correction_subject: 'CORRECTION: Important update regarding your refund request',
        drafted_body:
          'Dear Customer,\n\nWe recently sent an email stating that your refund request was approved under a 90-day refund policy. That communication was sent in error due to an unverified policy update.\n\nOur official refund policy remains 30 days (per Finance Ops Policy Doc v3.0 (30-day window)). We sincerely apologize for any confusion. Please find our verified policy details attached.\n\nSincerely,\nAcme Support Operations',
        verified_policy_citation: 'Finance Ops Policy Doc v3.0 (30-day window)',
        status: 'READY_FOR_HUMAN_APPROVAL',
      };
      if (email?.action) {
        email.action.correction_notice = notice;
      }
      const h: Handoff = {
        id: 'hand_escl_email',
        kind: 'ESCALATION',
        task: `Irreversible action '${actionId}' quarantined! Automated damage control retraction notice drafted.`,
        status: 'PENDING',
        assigned_to: 'Support Ops',
        context_root_id: actionId,
        correction_notice: notice,
      };
      this.handoffs.set(h.id, h);
      this.emit('handoff_updated', { handoff: clone(h) }, rid);

      const hReview = this.handoffs.get('hand_review_email');
      if (hReview) {
        hReview.status = 'COMPLETED';
        this.emit('handoff_updated', { handoff: clone(hReview) }, rid);
      }
    });
    this.later(2600, () => ['act_refund_1042', 'act_refund_1043'].forEach((id) => this.compensate(id, 'DISPATCHING', 1, rid)));
    this.later(3400, () => this.compensate('act_refund_1042', 'COMPENSATED', 1, rid));
    this.later(3900, () => this.compensate('act_refund_1043', 'COMPENSATED', 1, rid));
    this.later(4200, () => {
      this.emit('invariant_status', { ok: true, violations: 0 }, rid);
      this.progress(rid, 'audit', 'finished');
    });
    return true;
  }

  supersede(rid: string) {
    this.flags.superseded = true;
    this.progress(rid, 'supersede', 'started', 'Support Ops publishes policy v4: the window is now 45 days.');
    this.later(400, () =>
      this.addNode(
        {
          id: 'src_policy_v4',
          kind: 'source',
          label: 'Refund policy v4',
          status: 'HEALTHY',
          stale: false,
          origin: 'doc:policy-portal',
          owner_team: 'Support Ops',
          valid_from: Date.now(),
          source: { kind: 'DOC', title: 'Refund policy', url: POLICY_URL, version: '4' },
        },
        rid,
      ),
    );
    this.later(800, () => {
      this.addNode(
        {
          id: 'fact_45d',
          kind: 'fact',
          label: 'Refund window is 45 days',
          text: 'Refunds are allowed within 45 days of purchase.',
          key: 'refund_window_days',
          value: '45',
          status: 'HEALTHY',
          stale: false,
          valid_from: Date.now(),
          valid_to: null,
          source_ref: 'doc:policy-portal#refunds',
          quote: 'Refunds are allowed within 45 days.',
          origin: 'doc:policy-portal',
          asserted_by: root,
        },
        rid,
      );
      this.addEdge('src_policy_v4', 'fact_45d', 'FROM_SOURCE', rid);
      this.addEdge('fact_45d', 'fact_30d', 'SUPERSEDES', rid);
      this.log(rid, 'supersede', CYPHER.supersede, 1);
    });
    this.later(1300, () => {
      this.setStatus('fact_30d', 'SUPERSEDED', rid, { reason: 'Replaced by fact_45d' });
      const stale = [...this.descendants('fact_30d').keys()].filter(
        (id) => id !== 'fact_30d' && this.nodes.get(id)?.kind === 'conclusion' && this.nodes.get(id)?.status === 'HEALTHY',
      );
      stale.forEach((id) => this.setStatus(id, 'HEALTHY', rid, { stale: true, reason: 'Built on a replaced fact' }));
      this.log(rid, 'stale_propagation', CYPHER.stale, stale.length);
      this.epoch += 1;
      this.emit('epoch_changed', { epoch: this.epoch, reason: 'supersede' }, rid);
    });
    this.later(1700, () =>
      this.upsertHandoff(
        {
          id: 'hand_review_macro',
          kind: 'REVIEW',
          task: 'Review: support macro still quotes 30 days',
          status: 'PENDING',
          assigned_to: 'Support Ops',
          context_root_id: 'dec_macro',
        },
        rid,
      ),
    );
    this.later(2000, () => this.progress(rid, 'supersede', 'finished'));
    return true;
  }

  restore(rid: string) {
    this.flags.restored = true;
    this.progress(rid, 'restore', 'started', 'Restoring what is safe. Executed actions stay rolled back.');
    this.later(500, () => {
      const restored: string[] = [];
      let changed = true;
      let depth = 0;
      while (changed) {
        changed = false;
        for (const n of this.nodes.values()) {
          if (n.status !== 'QUARANTINED') continue;
          if (n.kind === 'action' && n.action?.executed) continue;
          const parentsOk = this.incoming(n.id).every((e) => this.nodes.get(e.source)?.status === 'HEALTHY');
          if (parentsOk) {
            this.setStatus(n.id, 'HEALTHY', rid, { depth, root: 'fact_90d', reason: 'Restored' });
            restored.push(n.id);
            changed = true;
          }
        }
        depth += 1;
      }
      this.log(rid, 'restore', CYPHER.restore, restored.length);
      this.epoch += 1;
      this.emit('epoch_changed', { epoch: this.epoch, reason: 'restore' }, rid);
    });
    this.later(1100, () => {
      this.emit('invariant_status', { ok: true, violations: 0 }, rid);
      this.progress(rid, 'restore', 'finished');
    });
    return true;
  }
}

export class MockBackend {
  private tenants = new Map<string, Tenant>([
    ['acme', new Tenant('acme', true)],
    ['globex', new Tenant('globex', false)],
  ]);

  principal(token: string | null): { tenant: string; role: Role } | null {
    const m = /^mock-(acme|globex)-(human|auditor|agent)$/.exec(token ?? '');
    return m ? { tenant: m[1] as string, role: m[2] as Role } : null;
  }

  tenantFor(token: string | null): Tenant | null {
    const p = this.principal(token);
    return p ? (this.tenants.get(p.tenant) ?? null) : null;
  }

  subscribe(token: string | null, lastSeq: number, cb: (e: Envelope) => void): () => void {
    const t = this.tenantFor(token);
    if (!t) return () => undefined;
    const oldest = t.events[0]?.seq ?? t.seq;
    if (lastSeq > 0 && lastSeq + 1 < oldest) {
      queueMicrotask(() => cb({ seq: t.seq, ts: Date.now(), epoch: t.epoch, type: 'resync_required', data: {} }));
    } else {
      t.events.filter((e) => e.seq > lastSeq).forEach((e) => queueMicrotask(() => cb(e)));
    }
    t.subs.add(cb);
    const hb = setInterval(
      () => cb({ seq: t.seq, ts: Date.now(), epoch: t.epoch, type: 'heartbeat', data: {} }),
      10_000,
    );
    return () => {
      t.subs.delete(cb);
      clearInterval(hb);
    };
  }

  handle(req: MockRequest): MockResponse {
    const rid = newRid();
    const p = this.principal(req.token);
    const t = this.tenantFor(req.token);
    if (!p || !t) return err(401, 'UNAUTHENTICATED', 'Missing or invalid token.', rid);
    const path = req.path.replace(/^\/+/, '');
    const m = (re: RegExp) => re.exec(path);
    const get = req.method === 'GET';
    const post = req.method === 'POST';
    let x: RegExpExecArray | null;

    if (get && path === 'meta') {
      return ok(
        { contract_version: CONTRACT_VERSION, mode: 'replay', tenant: t.name, role: p.role, epoch: t.epoch, seq: t.seq, replay_available: true },
        rid,
      );
    }
    if (get && path === 'graph') {
      const asOfRaw = req.query.get('as_of');
      const asOf = asOfRaw ? Number(asOfRaw) : null;
      let s: Snapshot;
      if (asOf === null) {
        s = { ts: Date.now(), seq: t.seq, epoch: t.epoch, nodes: clone([...t.nodes.values()]), edges: clone([...t.edges.values()]) };
      } else {
        s = [...t.snapshots].reverse().find((x2) => x2.ts <= asOf) ?? (t.snapshots[0] as Snapshot);
        s = clone(s);
      }
      return ok(
        {
          contract_version: CONTRACT_VERSION,
          tenant: t.name,
          epoch: s.epoch,
          seq: s.seq,
          as_of: asOf,
          generated_at: Date.now(),
          truncated: false,
          nodes: s.nodes,
          edges: s.edges,
        },
        rid,
      );
    }
    if (get && (x = m(/^node\/([^/]+)$/))) {
      const id = decodeURIComponent(x[1] as string);
      const n = t.nodes.get(id);
      if (!n) return err(404, 'NOT_FOUND', `No node ${id}.`, rid);
      const rank: Record<string, number> = { source: 0, fact: 1, conclusion: 2, action: 3 };
      const order = [...t.nodes.keys()];
      const path2 = [...t.ancestors(id), id]
        .map((a) => t.nodes.get(a) as GraphNode)
        .sort((a, b) => (rank[a.kind] ?? 9) - (rank[b.kind] ?? 9) || order.indexOf(a.id) - order.indexOf(b.id));
      return ok(
        {
          node: clone(n),
          path: clone(path2),
          descendant_count: t.descendants(id).size - 1,
          status_history: t.statusLog[id] ?? [],
        },
        rid,
      );
    }
    if (req.method === 'POST' && (x = m(/^scenario\/([a-z_]+)$/))) {
      if (p.role === 'agent') return err(403, 'FORBIDDEN_ROLE', 'Agent tokens cannot run scenario steps.', rid);
      const step = x[1] as string;
      if (!['inject_poison', 'run_agents', 'audit', 'supersede', 'restore', 'reset'].includes(step)) {
        return err(422, 'VALIDATION', `Unknown step ${step}.`, rid);
      }
      if (t.running && step !== 'reset') return ok({ run_id: rid, accepted: false }, rid, 202);
      const accepted = t.run(step, rid);
      return ok({ run_id: rid, accepted }, rid, 202);
    }
    if (get && (x = m(/^blast\/([^/]+)\/preview$/))) {
      const id = decodeURIComponent(x[1] as string);
      if (!t.nodes.has(id)) return err(404, 'NOT_FOUND', `No node ${id}.`, rid);
      const ids = [...t.descendants(id).keys()];
      const ns = ids.map((i) => t.nodes.get(i) as GraphNode);
      const acts = ns.filter((n) => n.kind === 'action');
      const exec = acts.filter((n) => n.action?.executed);
      t.log(rid, 'blast_preview', 'MATCH (r {id:$id})-[:LEADS_TO|SUPPORTS*0..]->(d) RETURN DISTINCT d.id', ids.length);
      return ok(
        {
          root_id: id,
          node_ids: ids,
          facts: ns.filter((n) => n.kind === 'fact').length,
          conclusions: ns.filter((n) => n.kind === 'conclusion').length,
          actions: {
            staged: acts.filter((n) => !n.action?.executed).length,
            executed_reversible: exec.filter((n) => n.action?.reversibility === 'REVERSIBLE').length,
            executed_compensable: exec.filter((n) => n.action?.reversibility === 'COMPENSABLE').length,
            executed_irreversible: exec.filter((n) => n.action?.reversibility === 'IRREVERSIBLE').length,
          },
        },
        rid,
      );
    }
    if (get && (x = m(/^blast\/([^/]+)\/report$/))) {
      const r = t.reports[decodeURIComponent(x[1] as string)];
      return r ? ok(r, rid) : err(404, 'NOT_FOUND', 'No containment report for that node.', rid);
    }
    if (get && path === 'handoffs') return ok(clone([...t.handoffs.values()]), rid);
    if (req.method === 'POST' && (x = m(/^handoffs\/([^/]+)\/claim$/))) {
      const h = t.handoffs.get(decodeURIComponent(x[1] as string));
      if (!h) return err(404, 'NOT_FOUND', 'No such handoff.', rid);
      if (p.role === 'agent' && h.kind !== 'TASK') return err(403, 'FORBIDDEN_ROLE', 'Only a person can claim this.', rid);
      if (h.status !== 'PENDING') return err(409, 'CONFLICT', 'Someone else already claimed it.', rid);
      h.status = 'IN_PROGRESS';
      h.claimed_by = `${p.role}_${p.tenant}`;
      h.claimed_at = Date.now();
      h.lease_until = Date.now() + 60_000;
      t.emit('handoff_updated', { handoff: clone(h) }, rid);
      return ok(clone(h), rid);
    }
    if (req.method === 'POST' && (x = m(/^handoffs\/([^/]+)\/review$/))) {
      if (p.role !== 'human') return err(403, 'FORBIDDEN_ROLE', 'Only a human may complete a REVIEW handoff.', rid);
      const h = t.handoffs.get(decodeURIComponent(x[1] as string));
      if (!h) return err(404, 'NOT_FOUND', 'No such handoff.', rid);
      const body = req.body as { decision?: string } | null;
      if (body?.decision !== 'approve' && body?.decision !== 'reject') return err(422, 'VALIDATION', 'decision must be approve or reject.', rid);
      if (h.status === 'COMPLETED') return err(409, 'CONFLICT', 'Already decided.', rid);
      h.status = 'COMPLETED';
      const anchor = h.context_root_id ? t.nodes.get(h.context_root_id) : undefined;
      if (body.decision === 'approve' && anchor?.action && anchor.status === 'HEALTHY') anchor.action.gate = 'PASSED';
      t.emit('handoff_updated', { handoff: clone(h) }, rid);
      return ok(clone(h), rid);
    }
    if (get && path === 'actions') {
      const acts = [...t.nodes.values()].filter((n) => n.kind === 'action').reverse();
      return ok(clone(acts), rid);
    }
    if (post && (x = m(/^actions\/([^/]+)\/approve-correction$/))) {
      const id = decodeURIComponent(x[1] as string);
      const node = t.nodes.get(id);
      if (!node) return err(404, 'NOT_FOUND', `Action ${id} not found`, rid);
      if (node.action) {
        if (!node.action.correction_notice) {
          node.action.correction_notice = {
            target_action_id: id,
            recipient: 'customer@acme.com',
            original_subject: 'Update: Your Refund Request is Approved',
            original_statement: 'Your refund of $150 has been approved under our new 90-day policy.',
            correction_subject: 'CORRECTION: Important update regarding your refund request',
            drafted_body:
              'Dear Customer,\n\nWe recently sent an email stating that your refund request was approved under a 90-day refund policy. That communication was sent in error due to an unverified policy update.\n\nOur official refund policy remains 30 days (per Finance Ops Policy Doc v3.0 (30-day window)). We sincerely apologize for any confusion. Please find our verified policy details attached.\n\nSincerely,\nAcme Support Operations',
            verified_policy_citation: 'Finance Ops Policy Doc v3.0 (30-day window)',
            status: 'APPROVED_PENDING_DELIVERY',
          };
        } else {
          node.action.correction_notice.status = 'APPROVED_PENDING_DELIVERY';
        }
      }
      for (const h of t.handoffs.values()) {
        if (h.context_root_id === id || (h.correction_notice && h.correction_notice.target_action_id === id)) {
          h.status = 'COMPLETED';
          if (h.correction_notice) h.correction_notice.status = 'APPROVED_PENDING_DELIVERY';
          t.emit('handoff_updated', { handoff: clone(h) }, rid);
        }
      }
      t.emit('correction_approved', { action_id: id, status: 'APPROVED_PENDING_DELIVERY', notice: node.action?.correction_notice }, rid);
      return ok({ status: 'APPROVED_PENDING_DELIVERY', action_id: id, notice: node.action?.correction_notice }, rid);
    }
    if (get && path === 'agents') {
      return ok(t.nodes.has('fact_30d') ? AGENTS.map((a) => ({ ...a, last_active: Date.now() - 60_000 })) : [], rid);
    }
    if (get && (x = m(/^agents\/([^/]+)\/resume$/))) {
      const id = decodeURIComponent(x[1] as string);
      const a = AGENTS.find((q) => q.id === id);
      if (!a) return err(404, 'NOT_FOUND', 'No such agent.', rid);
      const mem = [...t.nodes.values()]
        .filter((n) => n.kind === 'fact' && n.status === 'HEALTHY')
        .map((n) => ({ fact_id: n.id, text: n.text ?? n.label, source_ref: n.source_ref ?? 'unknown', quote: n.quote ?? '' }));
      return ok(
        {
          session_id: `sess_${a.id.replace('agent_', '')}_${t.epoch}`,
          summary: `${a.name} resumed from its private history. ${mem.length} facts survive the current epoch; quarantined beliefs are excluded.`,
          memories: mem,
        },
        rid,
      );
    }
    if (get && path === 'procedures') return ok(clone(t.procedures), rid);
    if (get && path === 'prompt-diff') {
      const agent = req.query.get('agent');
      if (agent && !AGENTS.some((a) => a.id === agent)) return err(404, 'NOT_FOUND', 'No such agent.', rid);
      const after = t.healthyContext();
      const before = t.contextBefore?.items ?? after;
      const afterIds = new Set(after.map((i) => i.node_id));
      return ok(
        {
          epoch_before: t.contextBefore?.epoch ?? t.epoch,
          epoch_after: t.epoch,
          before,
          after,
          removed_ids: before.filter((i) => !afterIds.has(i.node_id)).map((i) => i.node_id),
        },
        rid,
      );
    }
    if (req.method === 'POST' && path === 'brain/ask') return ok(this.ask(t, (req.body as { question?: string } | null)?.question ?? ''), rid);
    if (get && path === 'who-knows') {
      const topic = (req.query.get('topic') ?? '').toLowerCase();
      if (!t.nodes.has('fact_30d') || !/refund|polic/.test(topic)) return ok([], rid);
      return ok(
        [
          { person: 'Priya Nair', team: 'Support Ops', evidence_count: 4, source_ids: ['src_policy_v3', 'src_ticket_1042', 'src_ticket_1043', 'src_ticket_1044'] },
          { person: 'Marcus Lee', team: 'Billing', evidence_count: 2, source_ids: ['src_policy_v3', 'src_ticket_1044'] },
          { person: 'Dana Ortiz', team: 'Support Ops', evidence_count: 1, source_ids: ['src_ticket_1042'] },
        ],
        rid,
      );
    }
    if (get && (x = m(/^decisions\/([^/]+)\/trace$/))) {
      const id = decodeURIComponent(x[1] as string);
      const n = t.nodes.get(id);
      if (!n) return err(404, 'NOT_FOUND', 'No such decision.', rid);
      const rows = t
        .ancestors(id)
        .map((a) => t.nodes.get(a) as GraphNode)
        .filter((f) => f.kind === 'fact')
        .flatMap((f) => {
          const src = t.incoming(f.id, ['FROM_SOURCE']).map((e) => t.nodes.get(e.source)).find(Boolean);
          if (!src?.source) return [];
          return [
            {
              claim: f.text ?? f.label,
              quote: f.quote ?? '',
              source_title: src.source.title,
              url: src.source.url,
              version: src.source.version,
              owner: src.owner_team ?? 'Unowned',
            },
          ];
        });
      return ok(rows, rid);
    }
    if (get && path === 'invariants') {
      const viol = [...t.nodes.values()].filter(
        (n) => n.status === 'HEALTHY' && t.incoming(n.id).some((e) => t.nodes.get(e.source)?.status === 'QUARANTINED'),
      ).length;
      return ok(
        {
          ok: viol === 0,
          checked_at: Date.now(),
          checks: [
            { name: 'No healthy node under a quarantined parent', ok: viol === 0, value: viol },
            { name: 'Every node has a status', ok: true, value: 0 },
            { name: 'No cycles in the causal graph', ok: true, value: 0 },
          ],
        },
        rid,
      );
    }
    if (get && path === 'cypher-log') {
      const want = req.query.get('request_id');
      const limit = Number(req.query.get('limit') ?? 50);
      const rows = t.cypher.filter((c) => !want || c.request_id === want).slice(-limit);
      return ok(clone(rows), rid);
    }
    if (get && path === 'bench/results') return ok(bench, rid);
    if (get && path === 'tenants/denied-demo') {
      const other = t.name === 'acme' ? 'globex' : 'acme';
      return ok(
        {
          tenant: t.name,
          attempted_graph: `tenant_${other}:commons`,
          result: 'DENIED',
          detail: `NOPERM this user has no permissions to access ~tenant_${other}:commons`,
          visible_graph_names: [`tenant_${t.name}:commons`, `tenant_${t.name}:agent_refunder:raw`],
        },
        rid,
      );
    }
    return err(404, 'NOT_FOUND', `No route ${req.method} /${path}.`, rid);
  }

  private ask(t: Tenant, q: string) {
    const lower = q.toLowerCase();
    const current = t.nodes.get('fact_45d') ?? t.nodes.get('fact_30d');
    const old = t.nodes.get('fact_30d');
    if (!current || !t.nodes.has('fact_30d') || !/refund|polic|window|own/.test(lower)) {
      return {
        refused: true,
        reason: 'No source in the graph supports an answer, so Quorum will not guess.',
        citations: [],
      };
    }
    const cite = (n: GraphNode, stale: boolean) => {
      const src = [...t.edges.values()].filter((e) => e.target === n.id && e.type === 'FROM_SOURCE').map((e) => t.nodes.get(e.source)).find(Boolean);
      return {
        source_title: src?.source?.title ?? 'Unknown',
        url: src?.source?.url ?? '',
        version: src?.source?.version ?? '',
        quote: n.quote ?? '',
        owner_team: src?.owner_team ?? 'Support Ops',
        valid_from: n.valid_from ?? 0,
        stale,
      };
    };
    const citations = [cite(current, false)];
    if (old && current.id !== old.id) citations.push(cite(old, true));
    const own = /own/.test(lower);
    return {
      refused: false,
      answer: own
        ? `Support Ops owns the refund policy. The current window is ${current.value} days.`
        : `The refund window is ${current.value} days.`,
      citations,
    };
  }
}

export const mockBackend = new MockBackend();
export const mockAgents = { analyst, comms, refunder };
