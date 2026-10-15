import type { GraphEdge, GraphNode, Handoff, SseEvent } from '../api/schemas';
import { copy } from '../copy';

export type Tone = 'neutral' | 'bad' | 'good' | 'review' | 'warn';
export type FeedGroup = 'graph' | 'actions' | 'handoffs' | 'system';

export interface FeedItem {
  id: string;
  seq: number;
  ts: number;
  group: FeedGroup;
  tone: Tone;
  text: string;
  nodeId?: string;
}

export interface Cascade {
  rootId: string;
  ids: string[];
  startedTs: number;
}

export interface ClaimRace {
  handoffId: string;
  winner: string;
  losers: string[];
  ts: number;
}

export interface CoreState {
  nodes: Record<string, GraphNode>;
  edges: Record<string, GraphEdge>;
  seq: number;
  epoch: number;
  feed: FeedItem[];
  invariant: { ok: boolean; violations: number } | null;
  scenario: { active: string | null; lastStep: string | null; lastState: string | null; detail: string | null };
  cascade: Cascade | null;
  nodeRequest: Record<string, string>;
  claimRace: ClaimRace | null;
  handoffs: Record<string, Handoff>;
  eventLog: { seq: number; ts: number; type: string; reason?: string; step?: string; state?: string }[];
}

export type Effect =
  | { kind: 'toast'; text: string }
  | { kind: 'invalidate'; keys: string[] }
  | { kind: 'report'; rootId: string }
  | { kind: 'delta'; ids: string[]; edgeIds: string[]; delays: Record<string, number> }
  | { kind: 'claim'; race: ClaimRace }
  | { kind: 'resync' };

export const emptyCore = (): CoreState => ({
  nodes: {},
  edges: {},
  seq: 0,
  epoch: 0,
  feed: [],
  invariant: null,
  scenario: { active: null, lastStep: null, lastState: null, detail: null },
  cascade: null,
  nodeRequest: {},
  claimRace: null,
  handoffs: {},
  eventLog: [],
});

export const WAVE_MS_PER_HOP = 120;
const FEED_MAX = 300;
const CASCADE_WINDOW_MS = 8000;

const lbl = (s: CoreState, id: string) => s.nodes[id]?.label ?? id;

export function snapshotToCore(
  prev: CoreState,
  g: { nodes: GraphNode[]; edges: GraphEdge[]; seq: number; epoch: number },
): CoreState {
  return {
    ...prev,
    nodes: Object.fromEntries(g.nodes.map((n) => [n.id, n])),
    edges: Object.fromEntries(g.edges.map((e) => [e.id, e])),
    seq: g.seq,
    epoch: g.epoch,
    cascade: null,
  };
}

export interface PlanResult {
  apply: SseEvent[];
  gap: boolean;
}

/** Orders events by seq, drops duplicates, and flags a gap. heartbeat and resync_required are not sequenced. */
export function planEvents(lastSeq: number, incoming: SseEvent[]): PlanResult {
  const unsequenced = incoming.filter((e) => e.type === 'heartbeat');
  const resync = incoming.some((e) => e.type === 'resync_required');
  if (resync) return { apply: [], gap: true };
  const seqd = incoming
    .filter((e) => e.type !== 'heartbeat' && e.seq > lastSeq)
    .sort((a, b) => a.seq - b.seq)
    .filter((e, i, arr) => i === 0 || e.seq !== arr[i - 1]?.seq);
  const apply: SseEvent[] = [];
  let expect = lastSeq + 1;
  for (const e of seqd) {
    if (e.seq !== expect) return { apply, gap: true };
    apply.push(e);
    expect = e.seq + 1;
  }
  void unsequenced;
  return { apply, gap: false };
}

function pushFeed(s: CoreState, ev: SseEvent, group: FeedGroup, tone: Tone, text: string, nodeId?: string): CoreState {
  const item: FeedItem = { id: `${ev.seq}-${s.feed.length}`, seq: ev.seq, ts: ev.ts, group, tone, text, nodeId };
  const feed = [item, ...s.feed].slice(0, FEED_MAX);
  return { ...s, feed };
}

function touch(s: CoreState, ev: SseEvent, id: string): CoreState {
  if (!ev.request_id) return s;
  return { ...s, nodeRequest: { ...s.nodeRequest, [id]: ev.request_id } };
}

export function applyEvent(prev: CoreState, ev: SseEvent): { state: CoreState; effects: Effect[] } {
  let s: CoreState = {
    ...prev,
    seq: Math.max(prev.seq, ev.seq),
    epoch: Math.max(prev.epoch, ev.epoch),
    eventLog: [
      ...prev.eventLog,
      {
        seq: ev.seq,
        ts: ev.ts,
        type: ev.type,
        reason: ev.type === 'epoch_changed' ? String(ev.data.reason) : undefined,
        step: ev.type === 'scenario_progress' ? ev.data.step : undefined,
        state: ev.type === 'scenario_progress' ? String(ev.data.state) : undefined,
      },
    ].slice(-600),
  };
  const effects: Effect[] = [];

  switch (ev.type) {
    case 'node_added': {
      const n = ev.data.node;
      s = { ...s, nodes: { ...s.nodes, [n.id]: n } };
      s = touch(s, ev, n.id);
      const group: FeedGroup = n.kind === 'action' ? 'actions' : 'graph';
      s = pushFeed(
        s,
        ev,
        group,
        'neutral',
        copy.history.added((n.is_decision ? copy.kind.decision : copy.kind[n.kind]) ?? n.kind, n.label),
        n.id,
      );
      effects.push({ kind: 'delta', ids: [n.id], edgeIds: [], delays: {} });
      break;
    }
    case 'edge_added': {
      const e = ev.data.edge;
      s = { ...s, edges: { ...s.edges, [e.id]: e } };
      if (e.type === 'CONTRADICTS') {
        s = pushFeed(s, ev, 'graph', 'bad', copy.history.contradiction(lbl(s, e.source), lbl(s, e.target)), e.source);
      }
      effects.push({ kind: 'delta', ids: [], edgeIds: [e.id], delays: {} });
      break;
    }
    case 'status_changed': {
      const d = ev.data;
      const n = s.nodes[d.id];
      if (!n) break;
      const upd: GraphNode = { ...n, status: d.status };
      if (d.stale !== undefined) upd.stale = d.stale;
      if (d.depth !== undefined) upd.depth = d.depth;
      if (d.status === 'QUARANTINED') {
        upd.quarantined_at = ev.ts;
        upd.released_at = null;
        upd.quarantine_root = d.root_id ?? upd.quarantine_root;
      } else if (d.status === 'HEALTHY' && n.status === 'QUARANTINED') {
        upd.released_at = ev.ts;
        upd.quarantine_root = null;
      } else if (d.status === 'SUPERSEDED') {
        upd.valid_to = ev.ts;
      }
      s = { ...s, nodes: { ...s.nodes, [d.id]: upd } };
      s = touch(s, ev, d.id);
      const delays: Record<string, number> = {};
      if (d.depth !== undefined && (d.status === 'QUARANTINED' || n.status === 'QUARANTINED')) {
        delays[d.id] = d.depth * WAVE_MS_PER_HOP;
      }
      if (d.status === 'QUARANTINED' && d.root_id) {
        const c = s.cascade;
        const fresh = !c || c.rootId !== d.root_id || ev.ts - c.startedTs > CASCADE_WINDOW_MS;
        s = {
          ...s,
          cascade: fresh
            ? { rootId: d.root_id, ids: [d.id], startedTs: ev.ts }
            : { ...c, ids: c.ids.includes(d.id) ? c.ids : [...c.ids, d.id] },
        };
      } else if (d.status === 'HEALTHY' && n.status === 'QUARANTINED') {
        const c = s.cascade;
        s = { ...s, cascade: c ? { ...c, ids: [...c.ids, d.id] } : { rootId: d.id, ids: [d.id], startedTs: ev.ts } };
      } else if (d.status === 'SUPERSEDED') {
        s = pushFeed(s, ev, 'graph', 'warn', copy.history.superseded(n.label), d.id);
      } else if (d.stale === true && !n.stale) {
        s = pushFeed(s, ev, 'graph', 'warn', copy.history.stale(n.label), d.id);
      }
      effects.push({ kind: 'delta', ids: [d.id], edgeIds: [], delays });
      effects.push({ kind: 'invalidate', keys: ['actions', 'node'] });
      break;
    }
    case 'epoch_changed': {
      const reason = String(ev.data.reason);
      s = { ...s, epoch: ev.data.epoch };
      if (reason === 'reset') {
        s = { ...s, cascade: null };
        effects.push({ kind: 'resync' });
        break;
      }
      const c = s.cascade;
      if (reason === 'quarantine' && c) {
        const actions = c.ids.filter((id) => s.nodes[id]?.kind === 'action' && s.nodes[id]?.action?.executed).length;
        s = pushFeed(s, ev, 'graph', 'bad', copy.history.quarantined(lbl(s, c.rootId), c.ids.length, actions), c.rootId);
        effects.push({ kind: 'report', rootId: c.rootId });
      } else if (reason === 'restore' && c) {
        s = pushFeed(s, ev, 'graph', 'good', copy.history.restored(lbl(s, c.rootId), c.ids.length), c.rootId);
      }
      s = pushFeed(s, ev, 'system', 'neutral', copy.history.epoch(ev.data.epoch, reason));
      effects.push({ kind: 'toast', text: copy.toasts.epoch(reason) });
      effects.push({ kind: 'invalidate', keys: ['prompt-diff', 'procedures', 'handoffs', 'agents', 'resume', 'actions', 'node'] });
      break;
    }
    case 'gate_blocked': {
      const d = ev.data;
      const n = s.nodes[d.action_id];
      if (n?.action) {
        s = { ...s, nodes: { ...s.nodes, [n.id]: { ...n, action: { ...n.action, gate: 'BLOCKED' } } } };
        effects.push({ kind: 'delta', ids: [n.id], edgeIds: [], delays: {} });
      }
      s = pushFeed(s, ev, 'actions', 'review', copy.history.gate(lbl(s, d.action_id), d.reason), d.action_id);
      effects.push({ kind: 'invalidate', keys: ['handoffs', 'actions'] });
      break;
    }
    case 'action_executed': {
      const d = ev.data;
      const n = s.nodes[d.action_id];
      if (n?.action) {
        s = {
          ...s,
          nodes: { ...s.nodes, [n.id]: { ...n, action: { ...n.action, executed: true, confirmed: d.confirmed } } },
        };
        s = touch(s, ev, n.id);
        effects.push({ kind: 'delta', ids: [n.id], edgeIds: [], delays: {} });
      }
      s = pushFeed(s, ev, 'actions', d.confirmed ? 'good' : 'warn', copy.history.executed(lbl(s, d.action_id), d.confirmed), d.action_id);
      effects.push({ kind: 'invalidate', keys: ['actions', 'node'] });
      break;
    }
    case 'compensation_updated': {
      const d = ev.data;
      const n = s.nodes[d.action_id];
      if (n?.action) {
        s = {
          ...s,
          nodes: {
            ...s.nodes,
            [n.id]: { ...n, action: { ...n.action, compensation: { status: d.status, attempts: d.attempts } } },
          },
        };
        effects.push({ kind: 'delta', ids: [n.id], edgeIds: [], delays: {} });
      }
      s = pushFeed(
        s,
        ev,
        'actions',
        d.status === 'COMPENSATED' ? 'good' : d.status === 'FAILED' ? 'bad' : 'warn',
        copy.history.compensation(lbl(s, d.action_id), d.status, d.attempts),
        d.action_id,
      );
      effects.push({ kind: 'invalidate', keys: ['actions', 'node'] });
      break;
    }
    case 'handoff_claimed': {
      const d = ev.data;
      const race: ClaimRace = { handoffId: d.handoff_id, winner: d.winner, losers: d.losers, ts: ev.ts };
      s = { ...s, claimRace: race };
      s = pushFeed(s, ev, 'handoffs', 'review', copy.history.claimed(d.winner, d.losers, d.handoff_id));
      effects.push({ kind: 'claim', race });
      effects.push({ kind: 'invalidate', keys: ['handoffs'] });
      break;
    }
    case 'handoff_updated': {
      const h = ev.data.handoff;
      const known = s.handoffs[h.id];
      s = { ...s, handoffs: { ...s.handoffs, [h.id]: h } };
      if (!known || known.status !== h.status) {
        s = pushFeed(s, ev, 'handoffs', h.kind === 'REVIEW' ? 'review' : 'neutral', copy.history.handoff(h.task, h.status), h.context_root_id);
      }
      effects.push({ kind: 'invalidate', keys: ['handoffs'] });
      break;
    }
    case 'invariant_status': {
      s = { ...s, invariant: { ok: ev.data.ok, violations: ev.data.violations } };
      if (!ev.data.ok) s = pushFeed(s, ev, 'system', 'bad', copy.history.invariantBad(ev.data.violations));
      effects.push({ kind: 'invalidate', keys: ['invariants'] });
      break;
    }
    case 'scenario_progress': {
      const d = ev.data;
      s = {
        ...s,
        scenario: {
          active: d.state === 'started' ? d.step : s.scenario.active === d.step ? null : s.scenario.active,
          lastStep: d.step,
          lastState: d.state,
          detail: d.detail ?? null,
        },
      };
      if (d.step === 'reset' && d.state === 'finished') {
        s = { ...s, feed: [], eventLog: [], cascade: null, claimRace: null, handoffs: {}, invariant: null };
        effects.push({ kind: 'invalidate', keys: ['prompt-diff', 'procedures', 'handoffs', 'agents', 'resume', 'actions', 'node'] });
        break;
      }
      const tone: Tone = d.state === 'failed' ? 'bad' : d.detail ? 'warn' : 'neutral';
      if (d.detail || d.state === 'failed') {
        s = pushFeed(s, ev, 'system', tone, d.detail ?? copy.history.scenario(d.step, d.state));
      }
      break;
    }
    case 'resync_required':
      effects.push({ kind: 'resync' });
      break;
    case 'workspace_changed':
    case 'heartbeat':
    case 'unknown':
      break;
    case 'mcp_call': {
      const c = ev.data.call;
      const saved = c.baseline > 0 ? ` (${c.baseline - c.tokens >= 0 ? '−' : '+'}${Math.abs(Math.round(((c.baseline - c.tokens) / c.baseline) * 100))}% vs a top-8 lookup)` : '';
      s = pushFeed(s, ev, 'system', c.ok ? 'neutral' : 'warn', `${c.agent_id ?? 'An agent'} called ${c.tool}${c.summary ? ` “${c.summary}”` : ''}: ${c.tokens} tokens${saved}`);
      break;
    }
  }
  return { state: s, effects };
}
