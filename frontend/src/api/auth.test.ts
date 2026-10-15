import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
type Client = typeof import('./client');
let c: Client;

type Reply = { status: number; body?: unknown };

/** A hub that answers /api/whoami, /api/dev-config and /config.json as configured. */
function hub(routes: Record<string, (auth: string | null) => Reply>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url);
    const h = routes[path];
    const auth = (init?.headers as Record<string, string> | undefined)?.Authorization ?? null;
    const r = h ? h(auth) : { status: 404 };
    return { ok: r.status >= 200 && r.status < 300, status: r.status, json: async () => r.body } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function memoryStorage() {
  const m = new Map<string, string>();
  vi.stubGlobal('sessionStorage', { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) });
  return m;
}

beforeEach(async () => {
  // the test environment builds the sample (mock) console; these tests are about the live one
  vi.resetModules();
  vi.stubEnv('VITE_API_MODE', 'live');
  memoryStorage();
  c = await import('./client');
  c.signOut();
  c.session.tenant = 'acme';
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const good = (auth: string | null) => (auth === 'Bearer qtk_good' ? { status: 200, body: { tenant: 'acme', role: 'human' } } : { status: 401 });

describe('sign-in', () => {
  it('asks for a token when production rules leave no other way in', async () => {
    hub({ '/api/whoami': good });
    expect(await c.resolveAuth()).toBe('needed');
  });

  it('accepts a good token, keeps it for this tab only, and uses it for every role', async () => {
    hub({ '/api/whoami': good });
    const store = memoryStorage();
    expect(await c.signIn('  qtk_good ')).toEqual({ ok: true });
    expect(store.get('quorum:token')).toBe('qtk_good');
    expect(c.signedInWithToken()).toBe(true);
    expect(c.tokenFor('human')).toBe('qtk_good');
    expect(c.tokenFor('auditor')).toBe('qtk_good');
  });

  it('refuses a wrong token without keeping it', async () => {
    hub({ '/api/whoami': good });
    const store = memoryStorage();
    const r = await c.signIn('qtk_bad');
    expect(r.ok).toBe(false);
    expect(store.size).toBe(0);
    expect(await c.signIn('   ')).toEqual({ ok: false, message: 'Paste a token first.' });
  });

  it('picks up a token signed in earlier in this tab, and drops it once the hub refuses it', async () => {
    const store = memoryStorage();
    store.set('quorum:token', 'qtk_good');
    hub({ '/api/whoami': good });
    expect(await c.resolveAuth()).toBe('ok');
    c.signOut();
    store.set('quorum:token', 'qtk_revoked');
    expect(await c.resolveAuth()).toBe('needed');
    expect(store.has('quorum:token')).toBe(false);
  });

  it('uses the demo tokens only when the hub offers them (development)', async () => {
    hub({ '/api/dev-config': () => ({ status: 200, body: { tokens: { acme: { human: 'token_human' } }, tenants: ['acme'] } }) });
    expect(await c.resolveAuth()).toBe('ok');
    expect(c.tokenFor('human')).toBe('token_human');
  });

  it('ignores credentials published in a public config.json', async () => {
    hub({ '/config.json': () => ({ status: 200, body: { tokens: {}, tenants: [] } }) });
    expect(await c.resolveAuth()).toBe('needed');
    hub({ '/config.json': () => ({ status: 200, body: { tokens: { acme: { human: 'deployed' } }, tenants: ['acme'] } }) });
    expect(await c.resolveAuth()).toBe('needed');
    expect(c.tokenFor('human')).toBeNull();
  });

  it('signing out forgets everything', async () => {
    hub({ '/api/whoami': good });
    await c.signIn('qtk_good');
    c.signOut();
    expect(c.signedInWithToken()).toBe(false);
    expect(c.tokenFor('human')).toBeNull();
  });
});
