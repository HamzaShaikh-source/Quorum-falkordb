import { create } from 'zustand';
import { API_MODE, tokenFor } from '../api/client';
import { buildMap, pack, ragBaseline, shortIds, type Graph } from '../context/packer';
import { synthGraph } from '../context/synth';
import { estimateTokens } from '../context/tokens';
import { MCP_TOOLS, probeMcp } from './mcp';
import { meshGroups, useWorkspace, type Member, type Presence } from './store';

export interface Activity {
  id: number;
  ts: number;
  memberId: string;
  member: string;
  tool: string;
  summary: string;
  /** tokens the agent actually received */
  used: number;
  /** tokens a plain notes memory would have sent for the same call; 0 for writes */
  naive: number;
  epoch: number;
  via?: string;
}

export interface Turn {
  id: number;
  ts: number;
  from: 'user' | 'assistant' | 'tool';
  text: string;
  tokens?: number;
}

export interface McpState {
  state: 'checking' | 'connected' | 'unreachable' | 'unexpected' | 'simulated';
  latencyMs: number;
  detail: string;
  checkedAt: number;
}

interface Live {
  mcp: McpState;
  presence: Record<string, Presence>;
  lastSeen: Record<string, number>;
  activity: Activity[];
  chats: Record<string, Turn[]>;
  totals: { calls: number; used: number; naive: number };
  perMember: Record<string, { calls: number; used: number; naive: number }>;
  synthetic: boolean;
  running: boolean;
  checkMcp: () => Promise<void>;
  start: (getGraph: () => { graph: Graph; tenant: string; epoch: number }) => void;
  stop: () => void;
  clear: () => void;
}

const QUERIES = ['refund window', 'shipping delay', 'warranty length', 'who owns escalations', 'plan price', 'data retention', 'onboarding steps'];
let nextId = 1;
let timer: ReturnType<typeof setInterval> | null = null;
let ping: ReturnType<typeof setInterval> | null = null;
let cache: { key: string; synth: Graph } | null = null;

function workingGraph(g: Graph): { graph: Graph; synthetic: boolean } {
  if (g.nodes.length >= 40) return { graph: g, synthetic: false };
  const key = 'synth-240';
  if (!cache || cache.key !== key) cache = { key, synth: synthGraph(240) };
  return { graph: cache.synth, synthetic: true };
}

const pick = <T,>(a: T[]): T => a[Math.floor(Math.random() * a.length)]!;

export const useLive = create<Live>((set, get) => ({
  mcp: { state: 'checking', latencyMs: 0, detail: 'Checking…', checkedAt: 0 },
  presence: {},
  lastSeen: {},
  activity: [],
  chats: {},
  totals: { calls: 0, used: 0, naive: 0 },
  perMember: {},
  synthetic: false,
  running: false,

  checkMcp: async () => {
    if (API_MODE === 'mock') {
      // No real server in mock mode. Say so instead of pretending.
      set({ mcp: { state: 'simulated', latencyMs: 18 + Math.round(Math.random() * 14), detail: 'Sample data mode: no real MCP server is being called.', checkedAt: Date.now() } });
      return;
    }
    set((s) => ({ mcp: { ...s.mcp, state: 'checking', detail: 'Checking…' } }));
    const p = await probeMcp('/mcp', tokenFor('human'));
    set({ mcp: { state: p.state, latencyMs: p.latencyMs, detail: p.detail, checkedAt: Date.now() } });
  },

  clear: () => set({ activity: [], chats: {}, totals: { calls: 0, used: 0, naive: 0 }, perMember: {} }),

  stop: () => {
    if (timer) clearInterval(timer);
    if (ping) clearInterval(ping);
    timer = ping = null;
    set({ running: false });
  },

  start: (getGraph) => {
    if (get().running) return;
    set({ running: true });
    void get().checkMcp();
    ping = setInterval(() => void get().checkMcp(), 15000);
    // Live mode has no workspace service yet: presence and activity are not simulated, only the real MCP probe runs.
    if (API_MODE !== 'mock') return;

    // Presence: deterministic-ish so the page looks alive but stable. Real presence needs the workspace service.
    const refreshPresence = () => {
      const { members } = useWorkspace.getState().w;
      const next: Record<string, Presence> = {};
      const seen: Record<string, number> = { ...get().lastSeen };
      members.forEach((m, i) => {
        const roll = (Date.now() / 20000 + i * 1.7) % 6;
        next[m.id] = i === members.length - 1 && members.length > 3 ? 'offline' : roll < 4 ? 'online' : 'idle';
        if (next[m.id] !== 'offline') seen[m.id] = Date.now();
      });
      set({ presence: next, lastSeen: seen });
    };
    refreshPresence();

    const tick = () => {
      refreshPresence();
      const { w } = useWorkspace.getState();
      const online = w.members.filter((m) => get().presence[m.id] === 'online');
      if (!online.length) return;
      const m = pick(online);
      const { graph: g0, epoch } = getGraph();
      const { graph, synthetic } = workingGraph(g0);
      const roll = Math.random();
      const now = Date.now();
      const acts: Activity[] = [];
      const turns: Turn[] = [];
      const t = (tool: string, summary: string, used: number, naive: number, via?: string) =>
        acts.push({ id: nextId++, ts: now, memberId: m.id, member: m.name, tool, summary, used, naive, epoch, via });
      const live = graph.nodes.filter((n) => n.status === 'HEALTHY' && n.kind === 'fact');
      const fact = live.length ? pick(live) : null;
      const q = fact ? fact.label.split(':')[0]!.replace(/\s\d+$/, '') : pick(QUERIES);

      if (roll < 0.6) {
        const p = pack(graph, { query: q, budget: 420 });
        const naive = ragBaseline(graph, q);
        t('retrieve_context', `"${q}" → ${p.included.length} nodes${p.truncated ? ' (trimmed to budget)' : ''}`, p.tokens, naive);
        turns.push(
          { id: nextId++, ts: now, from: 'user', text: `What do we know about ${q}?` },
          { id: nextId++, ts: now, from: 'tool', text: `retrieve_context("${q}", k=5) → ${p.included.length} nodes`, tokens: p.tokens },
          { id: nextId++, ts: now, from: 'assistant', text: fact ? `Per ${shortIds(graph.nodes).get(fact.id) ?? fact.id}: ${fact.label}. I checked its source before answering.` : 'I found nothing relevant that is still healthy.' },
        );
      } else if (roll < 0.72) {
        const p = pack(graph, { query: q, budget: 300, have: { epoch: epoch - 1, ids: graph.nodes.slice(0, Math.floor(graph.nodes.length / 2)).map((n) => n.id) } });
        t('delta', `Refreshed after epoch change → ${p.delta?.added ?? 0} new, ${p.delta?.removed ?? 0} gone`, p.tokens, ragBaseline(graph, q));
        turns.push({ id: nextId++, ts: now, from: 'tool', text: `delta(epoch ${epoch - 1}) → only what changed`, tokens: p.tokens });
      } else if (roll < 0.82) {
        t('remember_fact', `Stored "${q}" with its source`, estimateTokens(`${q} fact`) + 40, 0);
        turns.push({ id: nextId++, ts: now, from: 'assistant', text: `Saved a fact about ${q}. Others can cite it by id now.` });
      } else if (roll < 0.9) {
        t('derive', `Linked a conclusion to 2 parent facts`, 60, 0);
      } else if (roll < 0.95) {
        t('stage_action', `Staged an action. Quorum gate: ${Math.random() < 0.75 ? 'passed' : 'held for review'}`, 70, 0);
      } else {
        t('claim_handoff', `Claimed a task with a lease`, 40, 0);
      }

      // Linked peers receive a cheap delta, not a copy of the context.
      const groups = meshGroups(w);
      const mine = groups.find((gr) => gr.includes(m.id));
      if (mine && mine.length > 1 && acts[0] && acts[0].naive > 0) {
        const peers = mine.filter((id) => id !== m.id).map((id) => w.members.find((x) => x.id === id)).filter((x): x is Member => !!x);
        for (const p of peers.slice(0, 2)) {
          t('delta', `Synced from ${m.name}'s MCP`, 48 + Math.round(Math.random() * 30), acts[0].naive, m.name);
          acts[acts.length - 1]!.memberId = p.id;
          acts[acts.length - 1]!.member = p.name;
        }
      }

      set((s) => {
        const perMember = { ...s.perMember };
        let calls = 0;
        let used = 0;
        let naive = 0;
        for (const a of acts) {
          const pm = perMember[a.memberId] ?? { calls: 0, used: 0, naive: 0 };
          perMember[a.memberId] = { calls: pm.calls + 1, used: pm.used + a.used, naive: pm.naive + a.naive };
          calls++;
          used += a.used;
          naive += a.naive;
        }
        const chats = { ...s.chats };
        if (turns.length) chats[m.id] = [...(chats[m.id] ?? []), ...turns].slice(-60);
        return {
          synthetic,
          activity: [...acts.reverse(), ...s.activity].slice(0, 250),
          chats,
          perMember,
          totals: { calls: s.totals.calls + calls, used: s.totals.used + used, naive: s.totals.naive + naive },
        };
      });
    };
    tick();
    timer = setInterval(tick, 2200);
  },
}));

export const toolMeta = (name: string) => MCP_TOOLS.find((t) => t.name === name);
export { buildMap };
