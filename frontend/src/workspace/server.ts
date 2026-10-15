import { z } from 'zod';
import { api, tokenFor } from '../api/client';
import { McpCall, TokenStats, ContextPreview } from '../api/schemas';

const nul = <T extends z.ZodTypeAny>(t: T) => t.nullish().transform((v) => v ?? undefined);

export const WsMember = z
  .object({
    id: z.string(),
    name: z.string(),
    role: z.string(),
    team_id: z.string(),
    client: z.string(),
    device: z.string(),
    token_prefix: nul(z.string()),
    created_at: z.number(),
    status: z.enum(['online', 'idle', 'offline']),
    last_seen: nul(z.number()),
    calls: z.number(),
    tokens_saved: z.number(),
  })
  .passthrough();

export const WsView = z
  .object({
    org: z.string(),
    plan: z.string(),
    hub: z.string(),
    limits: z.object({ seats: z.number(), teams: z.number(), label: z.string(), note: z.string() }),
    teams: z.array(z.object({ id: z.string(), name: z.string(), seats: z.number() })),
    members: z.array(WsMember),
    links: z.array(z.object({ id: z.string(), a: z.string(), b: z.string(), scope: z.string(), paused: z.boolean(), created_at: z.number().optional() })),
    issued: nul(z.object({ member: WsMember.partial().passthrough(), token: z.string() })),
  })
  .passthrough();
export type WsView = z.infer<typeof WsView>;

const human = { role: 'human' as const };

export const getWorkspace = () => api(WsView, 'GET', 'workspace');
export const patchWorkspace = (body: { org?: string; plan?: string; hub?: string }) => api(WsView, 'PUT', 'workspace', { ...human, body });
export const addTeam = (name: string, seats: number) => api(WsView, 'POST', 'workspace/teams', { ...human, body: { name, seats } });
export const setSeats = (id: string, seats: number) => api(WsView, 'PATCH', `workspace/teams/${encodeURIComponent(id)}`, { ...human, body: { seats } });
export const removeTeam = (id: string) => api(WsView, 'DELETE', `workspace/teams/${encodeURIComponent(id)}`, human);
export const addMember = (m: { name: string; role: string; team_id: string; client: string; device: string; expires_in_days?: number }) => api(WsView, 'POST', 'workspace/members', { ...human, body: m });
export const updateMember = (id: string, patch: { role?: string; team_id?: string; client?: string }) => api(WsView, 'PATCH', `workspace/members/${encodeURIComponent(id)}`, { ...human, body: patch });
export const rotateMember = (id: string) => api(WsView, 'POST', `workspace/members/${encodeURIComponent(id)}/rotate`, human);
export const removeMember = (id: string) => api(WsView, 'DELETE', `workspace/members/${encodeURIComponent(id)}`, human);
export const addLink = (a: string, b: string, scope: string) => api(WsView, 'POST', 'workspace/links', { ...human, body: { a, b, scope } });
export const pauseLink = (id: string, paused: boolean) => api(WsView, 'PATCH', `workspace/links/${encodeURIComponent(id)}`, { ...human, body: { paused } });
export const removeLink = (id: string) => api(WsView, 'DELETE', `workspace/links/${encodeURIComponent(id)}`, human);

export const getActivity = (memberId?: string) => api(z.array(McpCall), 'GET', 'mcp/activity', { query: { limit: 150, member_id: memberId } });
export const getTokenStats = () => api(TokenStats, 'GET', 'token-stats');
export const getContextPreview = (query: string, budget: number) => api(ContextPreview, 'GET', 'context/preview', { query: { query, budget } });

const CatalogTool = z
  .object({
    name: z.string(),
    summary: z.string(),
    args: z.array(z.object({ name: z.string(), required: z.boolean(), default: z.unknown().optional() }).passthrough()),
    who: z.enum(['agent', 'auditor', 'human', 'any']).catch('any'),
    writes: z.boolean(),
    saves: z.string(),
  })
  .passthrough();

export const Catalog = z
  .object({
    tools: z.array(CatalogTool),
    public_url: nul(z.string()),
    header_required: z.boolean(),
    rate_limit_per_min: z.object({ tools: z.number(), http: z.number() }),
    embedding_provider: z.string(),
  })
  .passthrough();
export type Catalog = z.infer<typeof Catalog>;
export const getCatalog = () => api(Catalog, 'GET', 'mcp/catalog');

export const AuditEvent = z
  .object({
    id: z.number(),
    ts: z.number(),
    who: z.string(),
    team: nul(z.string()),
    client: nul(z.string()),
    tool: z.string(),
    text: z.string(),
    severity: z.enum(['read', 'write', 'alert', 'failed']).catch('read'),
    ok: z.boolean(),
    tokens: z.number(),
    saved: z.number(),
  })
  .passthrough();
export type AuditEvent = z.infer<typeof AuditEvent>;

export const Audit = z.object({
  events: z.array(AuditEvent),
  summary: z.object({ events: z.number(), agents: z.number(), by_severity: z.record(z.number()), tokens: z.number(), tokens_saved: z.number() }),
  retention: z.string(),
});
export type Audit = z.infer<typeof Audit>;
export const getAudit = (q: { severity?: string; agent?: string; limit?: number }) => api(Audit, 'GET', 'audit', { query: { ...q, limit: q.limit ?? 300 } });

/** The audit log as a spreadsheet. Fetched with the session token, then saved by the browser. */
export async function downloadAuditCsv(): Promise<boolean> {
  const token = tokenFor('human');
  try {
    const res = await fetch('/api/audit?format=csv&limit=2000', { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) return false;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = 'quorum-audit.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return true;
  } catch {
    return false;
  }
}
