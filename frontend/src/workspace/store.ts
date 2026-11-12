import { create } from 'zustand';
import type { ClientId } from './mcp';

export type MemberRole = 'agent' | 'human' | 'auditor';
export type Presence = 'online' | 'idle' | 'offline';
export type Plan = 'team' | 'business' | 'enterprise';

export const PLANS: Record<Plan, { label: string; seats: number; teams: number; note: string }> = {
  team: { label: 'Team', seats: 10, teams: 2, note: 'One shared commons, small group' },
  business: { label: 'Business', seats: 50, teams: 8, note: 'Several teams, per-team scopes' },
  enterprise: { label: 'Enterprise', seats: 500, teams: 40, note: 'Many teams, audit trail, linked workspaces' },
};

export interface Team {
  id: string;
  name: string;
  seats: number;
}

export interface Member {
  id: string;
  name: string;
  role: MemberRole;
  teamId: string;
  client: ClientId;
  device: string;
  token: string;
  createdAt: number;
}

export type Scope = 'commons' | 'team';
export interface Link {
  id: string;
  a: string;
  b: string;
  scope: Scope;
  paused: boolean;
  createdAt: number;
}

export interface Workspace {
  org: string;
  plan: Plan;
  hub: string;
  teams: Team[];
  members: Member[];
  links: Link[];
}

const KEY = 'quorum:workspace:v1';
const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 8)}`;
export const newToken = () => `qrm_${Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('')}`;

export function seedWorkspace(): Workspace {
  const t1: Team = { id: 'team_support', name: 'Support', seats: 4 };
  const t2: Team = { id: 'team_finance', name: 'Finance', seats: 3 };
  const mk = (name: string, role: MemberRole, teamId: string, client: ClientId, device: string): Member => ({
    id: uid('m'),
    name,
    role,
    teamId,
    client,
    device,
    token: `qrm_sample_${name.toLowerCase()}`,
    createdAt: Date.now() - 86_400_000,
  });
  const m = [
    mk('Mira', 'agent', t1.id, 'claude-code', 'Mira MacBook'),
    mk('Jon', 'agent', t1.id, 'cursor', 'Jon ThinkPad'),
    mk('Aisha', 'auditor', t2.id, 'claude-desktop', 'Aisha Surface'),
    mk('Leo', 'human', t2.id, 'chatgpt', 'Leo iPad'),
  ];
  return {
    org: 'Acme Support',
    plan: 'business',
    hub: 'https://hub.your-company.example/quorum',
    teams: [t1, t2],
    members: m,
    links: [{ id: uid('l'), a: m[0]!.id, b: m[1]!.id, scope: 'team', paused: false, createdAt: Date.now() - 3_600_000 }],
  };
}

function load(): Workspace {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const w = JSON.parse(raw) as Workspace;
      if (w && Array.isArray(w.teams) && Array.isArray(w.members)) return w;
    }
  } catch {
    /* fall through to the seed */
  }
  return seedWorkspace();
}

function save(w: Workspace) {
  try {
    localStorage.setItem(KEY, JSON.stringify(w));
  } catch {
    /* storage can be unavailable; the session still works */
  }
}

interface Actions {
  rename: (org: string) => void;
  setPlan: (p: Plan) => void;
  setHub: (url: string) => void;
  addTeam: (name: string, seats: number) => string | null;
  setSeats: (teamId: string, seats: number) => void;
  removeTeam: (teamId: string) => void;
  addMember: (m: { name: string; role: MemberRole; teamId: string; client: ClientId; device: string }) => string | null;
  removeMember: (id: string) => void;
  rotateToken: (id: string) => void;
  setMemberRole: (id: string, role: MemberRole) => void;
  link: (a: string, b: string, scope: Scope) => string | null;
  unlink: (id: string) => void;
  pauseLink: (id: string) => void;
  reset: () => void;
}

export const seatsUsed = (w: Workspace) => w.members.length;
export const seatsLimit = (w: Workspace) => PLANS[w.plan].seats;

export const useWorkspace = create<{ w: Workspace } & Actions>((set, get) => {
  const commit = (fn: (w: Workspace) => Workspace) =>
    set((s) => {
      const w = fn(s.w);
      save(w);
      return { w };
    });
  return {
    w: load(),
    rename: (org) => commit((w) => ({ ...w, org: org.trim() || w.org })),
    setPlan: (plan) => commit((w) => ({ ...w, plan })),
    setHub: (hub) => commit((w) => ({ ...w, hub })),
    addTeam: (name, seats) => {
      const w = get().w;
      const n = name.trim();
      if (!n) return 'Give the team a name.';
      if (w.teams.some((t) => t.name.toLowerCase() === n.toLowerCase())) return 'A team with that name already exists.';
      if (w.teams.length >= PLANS[w.plan].teams) return `The ${PLANS[w.plan].label} plan allows ${PLANS[w.plan].teams} teams.`;
      commit((x) => ({ ...x, teams: [...x.teams, { id: uid('team'), name: n, seats: Math.max(1, seats) }] }));
      return null;
    },
    setSeats: (teamId, seats) => commit((w) => ({ ...w, teams: w.teams.map((t) => (t.id === teamId ? { ...t, seats: Math.max(1, Math.min(200, seats)) } : t)) })),
    removeTeam: (teamId) =>
      commit((w) => {
        const gone = new Set(w.members.filter((m) => m.teamId === teamId).map((m) => m.id));
        return { ...w, teams: w.teams.filter((t) => t.id !== teamId), members: w.members.filter((m) => m.teamId !== teamId), links: w.links.filter((l) => !gone.has(l.a) && !gone.has(l.b)) };
      }),
    addMember: (m) => {
      const w = get().w;
      const name = m.name.trim();
      if (!name) return 'Give the member a name.';
      if (seatsUsed(w) >= seatsLimit(w)) return `All ${seatsLimit(w)} seats on the ${PLANS[w.plan].label} plan are used. Upgrade the plan or remove a member.`;
      const team = w.teams.find((t) => t.id === m.teamId);
      if (!team) return 'Pick a team.';
      if (w.members.filter((x) => x.teamId === team.id).length >= team.seats) return `${team.name} has no free seats. Raise its seat count first.`;
      commit((x) => ({ ...x, members: [...x.members, { ...m, name, id: uid('m'), token: newToken(), createdAt: Date.now() }] }));
      return null;
    },
    removeMember: (id) => commit((w) => ({ ...w, members: w.members.filter((m) => m.id !== id), links: w.links.filter((l) => l.a !== id && l.b !== id) })),
    setMemberRole: (id, role) => commit((w) => ({ ...w, members: w.members.map((m) => (m.id === id ? { ...m, role } : m)) })),
    rotateToken: (id) => commit((w) => ({ ...w, members: w.members.map((m) => (m.id === id ? { ...m, token: newToken() } : m)) })),
    link: (a, b, scope) => {
      const w = get().w;
      if (!a || !b) return 'Pick two members.';
      if (a === b) return 'Pick two different members.';
      if (w.links.some((l) => (l.a === a && l.b === b) || (l.a === b && l.b === a))) return 'Those two are already linked.';
      commit((x) => ({ ...x, links: [...x.links, { id: uid('l'), a, b, scope, paused: false, createdAt: Date.now() }] }));
      return null;
    },
    unlink: (id) => commit((w) => ({ ...w, links: w.links.filter((l) => l.id !== id) })),
    pauseLink: (id) => commit((w) => ({ ...w, links: w.links.map((l) => (l.id === id ? { ...l, paused: !l.paused } : l)) })),
    reset: () => commit(() => seedWorkspace()),
  };
});

/** Connected components of the link graph: members who share context through any chain of links. */
export function meshGroups(w: Workspace): string[][] {
  const adj = new Map<string, string[]>(w.members.map((m) => [m.id, []]));
  for (const l of w.links) {
    if (l.paused) continue;
    adj.get(l.a)?.push(l.b);
    adj.get(l.b)?.push(l.a);
  }
  const seen = new Set<string>();
  const out: string[][] = [];
  for (const m of w.members) {
    if (seen.has(m.id)) continue;
    const comp: string[] = [];
    const q = [m.id];
    seen.add(m.id);
    while (q.length) {
      const c = q.shift() as string;
      comp.push(c);
      for (const n of adj.get(c) ?? []) if (!seen.has(n)) {
          seen.add(n);
          q.push(n);
        }
    }
    out.push(comp);
  }
  return out.sort((a, b) => b.length - a.length);
}
