import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Ajv2020 from 'ajv/dist/2020';
import eventsSchema from '../../../contracts/events.schema.json';
import * as S from '../api/schemas';
import { MockBackend, type Envelope } from '../mocks/backend';

const tok = (role: string) => `mock-acme-${role}`;

function call(b: MockBackend, method: string, path: string, body: unknown = null, role = 'human') {
  const [p, qs] = path.split('?');
  return b.handle({ method, path: p as string, query: new URLSearchParams(qs ?? ''), body, token: tok(role) });
}

type Parser = { safeParse: (v: unknown) => { success: boolean } };
const listOf = (item: Parser): Parser => ({
  safeParse: (v) => ({ success: Array.isArray(v) && v.every((x) => item.safeParse(x).success) }),
});

describe('mock backend honours docs/CONTRACT.md', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('every SSE event matches contracts/events.schema.json and the zod schema', () => {
    const b = new MockBackend();
    const events: Envelope[] = [];
    b.subscribe(tok('human'), 0, (e) => events.push(e));
    for (const step of ['inject_poison', 'run_agents', 'audit', 'supersede', 'restore', 'reset']) {
      call(b, 'POST', `scenario/${step}`, null, 'auditor');
      vi.advanceTimersByTime(12_000);
    }
    expect(events.length).toBeGreaterThan(50);

    const ajv = new Ajv2020({ strict: false, allErrors: true });
    const validate = ajv.compile(eventsSchema);
    const types = new Set<string>();
    for (const e of events) {
      const raw = JSON.parse(JSON.stringify(e));
      expect(validate(raw), `${e.type}: ${JSON.stringify(validate.errors)}`).toBe(true);
      expect(S.SseEvent.safeParse(raw).success, e.type).toBe(true);
      types.add(e.type);
    }
    const expected = [
      'node_added',
      'edge_added',
      'status_changed',
      'epoch_changed',
      'gate_blocked',
      'action_executed',
      'compensation_updated',
      'handoff_claimed',
      'handoff_updated',
      'invariant_status',
      'scenario_progress',
      'resync_required',
      'heartbeat',
    ];
    for (const t of expected) expect(types.has(t), `scenario never emitted ${t}`).toBe(true);

    const seqs = events.filter((e) => e.type !== 'heartbeat').map((e) => e.seq);
    expect(seqs).toEqual([...seqs].sort((a, c) => a - c));
    expect(new Set(seqs).size).toBe(seqs.length);
  });

  it('every REST response validates against its zod schema', () => {
    const b = new MockBackend();
    for (const step of ['inject_poison', 'run_agents', 'audit']) {
      call(b, 'POST', `scenario/${step}`, null, 'auditor');
      vi.advanceTimersByTime(12_000);
    }
    const ok = (schema: Parser, path: string, method = 'GET', body: unknown = null) => {
      const r = call(b, method, path, body);
      expect(r.status, `${method} ${path}`).toBeLessThan(300);
      expect(schema.safeParse(JSON.parse(JSON.stringify(r.body))).success, `${method} ${path}`).toBe(true);
      return r.body;
    };
    ok(S.Meta, 'meta');
    const g = ok(S.GraphResponse, 'graph') as { nodes: unknown[] };
    expect(g.nodes.length).toBeGreaterThan(15);
    ok(S.GraphResponse, `graph?as_of=${Date.now() - 1}`);
    ok(S.NodeDetail, 'node/fact_90d');
    ok(S.BlastPreview, 'blast/fact_90d/preview');
    ok(S.BlastReport, 'blast/fact_90d/report');
    ok(listOf(S.Handoff), 'handoffs');
    ok(listOf(S.GraphNode), 'actions');
    ok(listOf(S.Agent), 'agents');
    ok(S.Resume, 'agents/agent_refunder/resume');
    ok(listOf(S.Procedure), 'procedures');
    ok(S.PromptDiff, 'prompt-diff?agent=agent_refunder');
    ok(S.BrainAnswer, 'brain/ask', 'POST', { question: 'Who owns the refund policy?' });
    ok(S.BrainAnswer, 'brain/ask', 'POST', { question: 'What is the CEO salary?' });
    ok(S.WhoKnows, 'who-knows?topic=refund');
    ok(S.DecisionTrace, 'decisions/dec_macro/trace');
    ok(S.Invariants, 'invariants');
    ok(S.CypherLog, 'cypher-log?limit=20');
    ok(S.BenchResults, 'bench/results');
    ok(S.DeniedDemo, 'tenants/denied-demo');
  });

  it('enforces roles and tenancy with the contract error codes', () => {
    const b = new MockBackend();
    const agentRun = call(b, 'POST', 'scenario/audit', null, 'agent');
    expect(agentRun.status).toBe(403);
    expect(S.ErrorBody.parse(agentRun.body).error.code).toBe('FORBIDDEN_ROLE');

    call(b, 'POST', 'scenario/inject_poison', null, 'auditor');
    vi.advanceTimersByTime(3000);
    call(b, 'POST', 'scenario/run_agents', null, 'auditor');
    vi.advanceTimersByTime(8000);
    const review = call(b, 'POST', 'handoffs/hand_review_email/review', { decision: 'approve' }, 'agent');
    expect(S.ErrorBody.parse(review.body).error.code).toBe('FORBIDDEN_ROLE');

    const anon = b.handle({ method: 'GET', path: 'meta', query: new URLSearchParams(), body: null, token: null });
    expect(S.ErrorBody.parse(anon.body).error.code).toBe('UNAUTHENTICATED');

    const gx = b.handle({ method: 'GET', path: 'graph', query: new URLSearchParams(), body: null, token: 'mock-globex-human' });
    expect((gx.body as { nodes: unknown[] }).nodes).toHaveLength(0);
  });

  it('refuses scenario steps run out of order instead of faking progress', () => {
    const b = new MockBackend();
    const events: Envelope[] = [];
    b.subscribe(tok('human'), 0, (e) => events.push(e));
    const r = call(b, 'POST', 'scenario/audit', null, 'auditor');
    expect((r.body as { accepted: boolean }).accepted).toBe(false);
    expect(events.some((e) => e.type === 'scenario_progress' && e.data.state === 'failed')).toBe(true);
  });
});

describe('zod boundary', () => {
  it('maps unknown enum values to UNKNOWN instead of failing', () => {
    const n = S.GraphNode.parse({ id: 'x', kind: 'hologram', label: 'x', status: 'ARCHIVED', stale: false });
    expect(n.kind).toBe('UNKNOWN');
    expect(n.status).toBe('UNKNOWN');
  });
  it('keeps unknown extra fields', () => {
    const n = S.GraphNode.parse({ id: 'x', kind: 'fact', label: 'x', status: 'HEALTHY', stale: false, brand_new_field: 7 });
    expect((n as Record<string, unknown>).brand_new_field).toBe(7);
  });
  it('rejects a node with no status instead of rendering it', () => {
    expect(S.GraphNode.safeParse({ id: 'x', kind: 'fact', label: 'x', stale: false }).success).toBe(false);
  });
});

describe('real backend shapes', () => {
  it('accepts explicit nulls for optional node fields, as the FastAPI backend sends them', () => {
    const node = {
      id: 'src_policy_v3',
      kind: 'source',
      is_decision: null,
      label: 'Refund policy v3',
      text: null,
      key: null,
      value: null,
      status: 'HEALTHY',
      stale: false,
      valid_from: 1,
      valid_to: null,
      source_ref: null,
      quote: null,
      origin: null,
      asserted_by: null,
      source: { kind: 'DOC', title: 'Refund policy', url: 'https://acme.example/policy', version: '3' },
      owner_team: 'Support Ops',
      action: null,
      correction_notice: null,
    };
    const out = S.GraphNode.parse(node);
    expect(out.text).toBeUndefined();
    expect(out.asserted_by).toBeUndefined();
    expect(out.source?.title).toBe('Refund policy');
  });
});
