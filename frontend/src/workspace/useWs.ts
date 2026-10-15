import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, API_MODE } from '../api/client';
import { useStore } from '../state/store';
import { useLive } from './live';
import type { ClientId } from './mcp';
import * as srv from './server';
import { PLANS, seatsLimit, useWorkspace, type MemberRole, type Plan, type Scope } from './store';

export type Status = 'online' | 'idle' | 'offline';

export interface WMember {
  id: string;
  name: string;
  role: MemberRole;
  teamId: string;
  client: string;
  device: string;
  /** Mock mode keeps the sample token; live mode never has it after creation. */
  token?: string;
  tokenHint: string;
  /** unix ms when the token stops working; undefined when it never expires */
  expiresAt?: number;
  status: Status | 'unknown';
  lastSeen?: number;
  calls: number;
  tokensSaved: number;
}

export interface WTeam {
  id: string;
  name: string;
  seats: number;
}

export interface WLink {
  id: string;
  a: string;
  b: string;
  scope: Scope;
  paused: boolean;
}

export interface Issued {
  member: string;
  token: string;
}

export interface Ws {
  live: boolean;
  loading: boolean;
  error: string | null;
  org: string;
  plan: Plan;
  hub: string;
  limits: { seats: number; teams: number; label: string; note: string };
  teams: WTeam[];
  members: WMember[];
  links: WLink[];
  issued: Issued | null;
  dismissIssued: () => void;
  /** Every action resolves to an error message to show, or null on success. */
  rename: (org: string) => Promise<string | null>;
  setPlan: (p: Plan) => Promise<string | null>;
  setHub: (url: string) => Promise<string | null>;
  addTeam: (name: string, seats: number) => Promise<string | null>;
  setSeats: (id: string, seats: number) => Promise<string | null>;
  removeTeam: (id: string) => Promise<string | null>;
  addMember: (m: { name: string; role: MemberRole; teamId: string; client: ClientId; device: string; expiresInDays?: number }) => Promise<string | null>;
  rotate: (id: string) => Promise<string | null>;
  /** Change a member's permission level. Takes effect on their next call. */
  setLevel: (id: string, role: MemberRole) => Promise<string | null>;
  removeMember: (id: string) => Promise<string | null>;
  link: (a: string, b: string, scope: Scope) => Promise<string | null>;
  unlink: (id: string) => Promise<string | null>;
  pause: (id: string) => Promise<string | null>;
  reset?: () => void;
}

const msg = (e: unknown) => (e instanceof ApiError ? e.message : e instanceof Error ? e.message : 'Something went wrong.');

/** One workspace model for both builds: the server in live mode, the saved sample in sample mode. */
export function useWs(): Ws {
  const live = API_MODE === 'live';
  const qc = useQueryClient();
  const loaded = useStore((s) => s.loaded);
  const q = useQuery({ queryKey: ['workspace'], queryFn: srv.getWorkspace, enabled: live && loaded, refetchInterval: live ? 10_000 : false });
  const [issued, setIssued] = useState<Issued | null>(null);

  // sample mode
  const local = useWorkspace((s) => s.w);
  const presence = useLive((s) => s.presence);
  const per = useLive((s) => s.perMember);
  const act = useWorkspace.getState();

  const run = useCallback(
    async (fn: () => Promise<srv.WsView>, issuedFor?: boolean): Promise<string | null> => {
      try {
        const v = await fn();
        qc.setQueryData(['workspace'], { ...v, issued: undefined });
        if (issuedFor && v.issued) setIssued({ member: String(v.issued.member.name ?? ''), token: v.issued.token });
        return null;
      } catch (e) {
        return msg(e);
      }
    },
    [qc],
  );

  if (!live) {
    const sync = <T,>(r: T) => Promise.resolve(r);
    return {
      live: false,
      loading: false,
      error: null,
      org: local.org,
      plan: local.plan,
      hub: local.hub,
      limits: { seats: seatsLimit(local), teams: PLANS[local.plan].teams, label: PLANS[local.plan].label, note: PLANS[local.plan].note },
      teams: local.teams,
      members: local.members.map((m) => ({
        id: m.id,
        name: m.name,
        role: m.role,
        teamId: m.teamId,
        client: m.client,
        device: m.device,
        token: m.token,
        tokenHint: `${m.token.slice(0, 8)}••••••`,
        status: presence[m.id] ?? 'offline',
        calls: per[m.id]?.calls ?? 0,
        tokensSaved: Math.max(0, (per[m.id]?.naive ?? 0) - (per[m.id]?.used ?? 0)),
      })),
      links: local.links,
      issued: null,
      dismissIssued: () => undefined,
      rename: (o) => { act.rename(o); return sync(null); },
      setPlan: (p) => { act.setPlan(p); return sync(null); },
      setHub: (u) => { act.setHub(u); return sync(null); },
      addTeam: (n, s) => sync(act.addTeam(n, s)),
      setSeats: (id, s) => { act.setSeats(id, s); return sync(null); },
      removeTeam: (id) => { act.removeTeam(id); return sync(null); },
      addMember: (m) => sync(act.addMember(m)),
      rotate: (id) => { act.rotateToken(id); return sync(null); },
      setLevel: (id, role) => { act.setMemberRole(id, role); return sync(null); },
      removeMember: (id) => { act.removeMember(id); return sync(null); },
      link: (a, b, s) => sync(act.link(a, b, s)),
      unlink: (id) => { act.unlink(id); return sync(null); },
      pause: (id) => { act.pauseLink(id); return sync(null); },
      reset: () => act.reset(),
    };
  }

  const d = q.data;
  return {
    live: true,
    loading: q.isLoading,
    error: q.isError ? msg(q.error) : null,
    org: d?.org ?? '',
    plan: (d?.plan as Plan) ?? 'business',
    hub: d?.hub ?? '',
    limits: d?.limits ?? { seats: 0, teams: 0, label: '', note: '' },
    teams: d?.teams ?? [],
    members: (d?.members ?? []).map((m) => ({
      id: m.id,
      name: m.name,
      role: m.role as MemberRole,
      teamId: m.team_id,
      client: m.client,
      device: m.device,
      tokenHint: `${m.token_prefix ?? 'qrm_'}••••••`,
      expiresAt: typeof m.expires_at === 'number' ? m.expires_at : undefined,
      status: m.status,
      lastSeen: m.last_seen,
      calls: m.calls,
      tokensSaved: m.tokens_saved,
    })),
    links: (d?.links ?? []).map((l) => ({ id: l.id, a: l.a, b: l.b, scope: l.scope as Scope, paused: l.paused })),
    issued,
    dismissIssued: () => setIssued(null),
    rename: (o) => run(() => srv.patchWorkspace({ org: o })),
    setPlan: (p) => run(() => srv.patchWorkspace({ plan: p })),
    setHub: (u) => run(() => srv.patchWorkspace({ hub: u })),
    addTeam: (n, s) => run(() => srv.addTeam(n, s)),
    setSeats: (id, s) => run(() => srv.setSeats(id, s)),
    removeTeam: (id) => run(() => srv.removeTeam(id)),
    addMember: (m) => run(() => srv.addMember({ name: m.name, role: m.role, team_id: m.teamId, client: m.client, device: m.device, expires_in_days: m.expiresInDays }), true),
    rotate: (id) => run(() => srv.rotateMember(id), true),
    setLevel: (id, role) => run(() => srv.updateMember(id, { role })),
    removeMember: (id) => run(() => srv.removeMember(id)),
    link: (a, b, s) => run(() => srv.addLink(a, b, s)),
    unlink: (id) => run(() => srv.removeLink(id)),
    pause: (id) => {
      const l = d?.links.find((x) => x.id === id);
      return run(() => srv.pauseLink(id, !(l?.paused ?? false)));
    },
  };
}
