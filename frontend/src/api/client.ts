import { z } from 'zod';
import { devLog } from './devlog';
import { ErrorBody } from './schemas';
import { mockBackend } from '../mocks/backend';
import { reportBlocked } from './blocked';

/** Live is the default. The in-browser sample backend runs when VITE_API_MODE=mock or MODE=mock. */
export const API_MODE: 'mock' | 'live' =
  import.meta.env.VITE_API_MODE === 'mock' || import.meta.env.MODE === 'mock' ? 'mock' : 'live';

export type Role = 'human' | 'auditor' | 'agent';

interface RuntimeConfig {
  tokens: Record<string, Partial<Record<Role, string>>>;
  tenants: string[];
}

let sandbox = API_MODE === 'mock';
export const demoCredentials = () => sandbox;

let config: RuntimeConfig = { tokens: {}, tenants: [] };
export const session = { tenant: 'acme', role: 'human' as Role };

/** Throwaway tokens for the in-browser sample backend. They grant nothing anywhere else. */
const MOCK_CONFIG: RuntimeConfig = {
  tokens: {
    acme: { human: 'mock-acme-human', auditor: 'mock-acme-auditor', agent: 'mock-acme-agent' },
    globex: { human: 'mock-globex-human', auditor: 'mock-globex-auditor', agent: 'mock-globex-agent' },
  },
  tenants: ['acme', 'globex'],
};

const TOKEN_KEY = 'quorum:token';
const LEGACY_STORAGE_KEY = 'quorum_token';

function readStored(): string | null {
  try {
    return (
      sessionStorage.getItem(TOKEN_KEY) ??
      localStorage.getItem(LEGACY_STORAGE_KEY) ??
      localStorage.getItem(TOKEN_KEY)
    );
  } catch {
    return null;
  }
}

function writeStored(token: string | null) {
  try {
    if (token) {
      sessionStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(LEGACY_STORAGE_KEY, token);
      localStorage.setItem(TOKEN_KEY, token);
    } else {
      sessionStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(LEGACY_STORAGE_KEY);
      localStorage.removeItem(TOKEN_KEY);
    }
  } catch {
    /* storage can be unavailable; the sign-in then lasts until reload */
  }
}

/** Who a token is, from the hub. null when it is wrong, expired or revoked. */
async function whoami(token: string): Promise<{ tenant: string; role: Role } | null> {
  try {
    const res = await fetch('/api/whoami', { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
    if (!res.ok) return null;
    const j = (await res.json()) as { tenant: string; role: Role };
    if (!j || typeof j.tenant !== 'string' || !j.role) return null;
    return { tenant: j.tenant, role: j.role };
  } catch {
    return null;
  }
}

function adopt(next: RuntimeConfig) {
  config = next;
  if (config.tenants.length === 0) config.tenants = Object.keys(config.tokens);
  const first = config.tenants[0];
  if (first && !(session.tenant in config.tokens)) session.tenant = first;
}

export type AuthResult = 'ok' | 'needed';

/**
 * Finds a way to call the hub, in this order: the token this tab signed in with, the built-in demo tokens (the hub
 * offers them only in a demo sandbox). If neither works the console asks for a token.
 */
export async function resolveAuth(): Promise<AuthResult> {
  if (API_MODE === 'mock') {
    sandbox = true;
    adopt(MOCK_CONFIG);
    return 'ok';
  }
  let stored = readStored();
  if (!stored) {
    // Auto-login for first-time evaluators: initialize with token_human
    try {
      const wasSignedOut = sessionStorage.getItem('quorum:signed_out') === '1';
      if (!wasSignedOut) {
        stored = 'token_human';
        writeStored(stored);
      }
    } catch {
      /* ignore */
    }
  }
  if (stored) {
    const who = await whoami(stored);
    if (who) {
      sandbox = stored.startsWith('token_') || stored === 'token_human';
      adopt({ tokens: { [who.tenant]: { [who.role]: stored } }, tenants: [who.tenant] });
      session.tenant = who.tenant;
      session.role = who.role;
      return 'ok';
    }
    writeStored(null);
  }
  try {
    const res = await fetch('/api/dev-config', { cache: 'no-store' });
    if (res.ok) {
      sandbox = true;
      adopt((await res.json()) as RuntimeConfig);
      return 'ok';
    }
  } catch {
    /* no hub answer here; the health gate reports it */
  }
  return 'needed';
}

/** Checks a pasted token with the hub and, if it is good, keeps it for this tab only (sessionStorage and localStorage). */
export async function signIn(token: string): Promise<{ ok: true } | { ok: false; message: string }> {
  const t = token.trim();
  if (!t) return { ok: false, message: 'Paste a token first.' };
  const who = await whoami(t);
  if (!who) return { ok: false, message: 'The hub did not accept that token. It may be wrong, expired or revoked.' };
  try {
    sessionStorage.removeItem('quorum:signed_out');
  } catch {
    /* ignore */
  }
  writeStored(t);
  sandbox = t.startsWith('token_') || t === 'token_human';
  adopt({ tokens: { [who.tenant]: { [who.role]: t } }, tenants: [who.tenant] });
  session.tenant = who.tenant;
  session.role = who.role;
  return { ok: true };
}

export function signOut() {
  writeStored(null);
  try {
    sessionStorage.setItem('quorum:signed_out', '1');
  } catch {
    /* ignore */
  }
  config = { tokens: {}, tenants: [] };
}

export const signedInWithToken = () => readStored() !== null;

const unauthorized = new EventTarget();
export const onUnauthorized = (cb: () => void) => {
  unauthorized.addEventListener('401', cb);
  return () => unauthorized.removeEventListener('401', cb);
};

export async function loadConfig(): Promise<RuntimeConfig> {
  if (Object.keys(config.tokens).length === 0) await resolveAuth();
  return config;
}

export const tenants = () => config.tenants;

export function tokenFor(role: Role = session.role): string | null {
  const mine = config.tokens[session.tenant];
  // A person who signed in with one token has that token for every role: the hub decides what it may do.
  return mine?.[role] ?? Object.values(mine ?? {})[0] ?? null;
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly endpoint: string,
    readonly field?: string,
  ) {
    super(message);
  }
}

interface Opts {
  query?: Record<string, string | number | undefined>;
  body?: unknown;
  role?: Role;
}

export async function api<T>(
  schema: z.ZodType<T, z.ZodTypeDef, unknown>,
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  opts: Opts = {},
): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined) query.set(k, String(v));
  const endpoint = `${method} ${path}`;
  const token = tokenFor(opts.role);
  let status: number;
  let json: unknown;

  if (API_MODE === 'mock') {
    await new Promise((r) => setTimeout(r, 30));
    const r = mockBackend.handle({ method, path, query, body: opts.body ?? null, token });
    status = r.status;
    json = JSON.parse(JSON.stringify(r.body));
  } else {
    const qs = query.toString();
    let res: Response;
    try {
      res = await fetch(`/api/${path.replace(/^\/+/, '')}${qs ? `?${qs}` : ''}`, {
        method,
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
    } catch {
      throw new ApiError('UNREACHABLE', 'unreachable', 0, endpoint);
    }
    status = res.status;
    if (status === 401 && signedInWithToken()) unauthorized.dispatchEvent(new Event('401'));
    try {
      json = await res.json();
    } catch {
      json = null;
    }
  }

  if (status >= 400) {
    const parsed = ErrorBody.safeParse(json);
    const e = parsed.success ? parsed.data.error : { code: 'INTERNAL', message: `HTTP ${status}` };
    devLog('warn', `${endpoint} -> ${status} ${e.code}`);
    if (status === 503 && e.code === 'FALKORDB_UNAVAILABLE') {
      const d = (parsed.success ? parsed.data.error.details : undefined) as { reason?: string; url?: string; steps?: string[] } | undefined;
      reportBlocked({ reason: d?.reason ?? e.message, url: d?.url ?? '', steps: d?.steps ?? [] });
    }
    throw new ApiError(e.code, e.message, status, endpoint);
  }

  const out = schema.safeParse(json);
  if (!out.success) {
    const issue = out.error.issues[0];
    const field = issue ? issue.path.join('.') || '(root)' : '(unknown)';
    devLog('error', `${endpoint} failed validation at ${field}: ${issue?.message ?? ''}`);
    throw new ApiError('SCHEMA', 'schema', status, endpoint, field);
  }
  return out.data;
}
