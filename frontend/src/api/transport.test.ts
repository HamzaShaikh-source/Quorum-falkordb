import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';

type Client = typeof import('./client');
let c: Client;

function reply(status: number, body: unknown) {
  const f = vi.fn(async () => ({ ok: status < 400, status, json: async () => body }) as Response);
  vi.stubGlobal('fetch', f);
  return f;
}

beforeEach(async () => {
  vi.resetModules();
  vi.stubEnv('VITE_API_MODE', 'live');
  vi.stubGlobal('sessionStorage', { getItem: () => 'qtk_t', setItem: () => undefined, removeItem: () => undefined });
  c = await import('./client');
  c.session.tenant = 'acme';
  // sign in with a token already stored in this tab
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ tenant: 'acme', role: 'human' }) }) as Response));
  await c.resolveAuth();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const Thing = z.object({ id: z.string() });

describe('the API client', () => {
  it('sends the bearer token and parses a good answer', async () => {
    const f = reply(200, { id: 'x' });
    expect(await c.api(Thing, 'GET', 'thing', { query: { a: 1, b: undefined } })).toEqual({ id: 'x' });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/thing?a=1');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer qtk_t');
  });

  it('sends a JSON body with the right content type', async () => {
    const f = reply(200, { id: 'x' });
    await c.api(Thing, 'POST', 'thing', { body: { n: 1 } });
    const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(init.body).toBe('{"n":1}');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });

  it('turns an error body into an ApiError with its code', async () => {
    reply(409, { error: { code: 'CONFLICT', message: 'no seats' } });
    await expect(c.api(Thing, 'POST', 'thing')).rejects.toMatchObject({ code: 'CONFLICT', message: 'no seats', status: 409 });
  });

  it('survives an error with no usable body', async () => {
    reply(500, null);
    await expect(c.api(Thing, 'GET', 'thing')).rejects.toMatchObject({ code: 'INTERNAL', status: 500 });
  });

  it('reports a schema mismatch as SCHEMA, naming the field', async () => {
    reply(200, { id: 5 });
    await expect(c.api(Thing, 'GET', 'thing')).rejects.toMatchObject({ code: 'SCHEMA', field: 'id' });
  });

  it('reports an unreachable hub', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('failed'))));
    await expect(c.api(Thing, 'GET', 'thing')).rejects.toMatchObject({ code: 'UNREACHABLE', status: 0 });
  });

  it('tells the shell when FalkorDB is the problem, with the steps', async () => {
    const { onBlocked } = await import('./blocked');
    const seen: unknown[] = [];
    onBlocked((b) => seen.push(b));
    reply(503, { error: { code: 'FALKORDB_UNAVAILABLE', message: 'down', details: { reason: 'refused', url: 'redis://x', steps: ['docker compose up -d falkordb'] } } });
    await expect(c.api(Thing, 'GET', 'thing')).rejects.toMatchObject({ code: 'FALKORDB_UNAVAILABLE' });
    expect(seen).toEqual([{ reason: 'refused', url: 'redis://x', steps: ['docker compose up -d falkordb'] }]);
  });

  it('announces a refused token so the console can ask you to sign in again', async () => {
    const heard = vi.fn();
    const off = c.onUnauthorized(heard);
    reply(401, { error: { code: 'UNAUTHENTICATED', message: 'Invalid token.' } });
    await expect(c.api(Thing, 'GET', 'thing')).rejects.toMatchObject({ status: 401 });
    expect(heard).toHaveBeenCalledTimes(1);
    off();
  });
});

describe('small helpers', () => {
  it('health: ok, blocked, and unreachable', async () => {
    const { fetchHealth } = await import('./health');
    const health = { status: 'ok', persistence: 'falkordb', falkordb: { up: true, url: 'x', latency_ms: 1, version: '8', error: null }, steps: [], warnings: [] };
    reply(200, health);
    expect((await fetchHealth()).kind).toBe('ok');
    reply(503, { ...health, status: 'blocked' });
    expect((await fetchHealth()).kind).toBe('blocked');
    reply(502, {});
    expect(await fetchHealth()).toMatchObject({ kind: 'unreachable', reason: expect.stringContaining('502') });
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new Error('refused'))));
    expect(await fetchHealth()).toMatchObject({ kind: 'unreachable', reason: 'refused' });
  });

  it('dev log keeps the last 200, can de-duplicate, and notifies listeners', async () => {
    const { devLog, subscribeDevLog } = await import('./devlog');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    let latest: { message: string }[] = [];
    const off = subscribeDevLog((e) => (latest = e));
    devLog('warn', 'same', true);
    devLog('warn', 'same', true);
    expect(latest.filter((e) => e.message === 'same')).toHaveLength(1);
    for (let i = 0; i < 250; i++) devLog('info', `m${i}`);
    expect(latest).toHaveLength(200);
    off();
    expect(warn).toHaveBeenCalled();
  });

  it('copyText uses the clipboard, then falls back to the old way, then reports failure', async () => {
    const { copyText } = await import('../workspace/clipboard');
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn(async () => undefined) } });
    expect(await copyText('a')).toBe(true);
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn(async () => Promise.reject(new Error('blocked'))) } });
    const ta = { value: '', setAttribute: vi.fn(), style: {} as Record<string, string>, select: vi.fn(), remove: vi.fn() };
    vi.stubGlobal('document', { createElement: () => ta, body: { appendChild: vi.fn() }, execCommand: () => true });
    expect(await copyText('b')).toBe(true);
    vi.stubGlobal('document', { createElement: () => ta, body: { appendChild: vi.fn() }, execCommand: () => false });
    expect(await copyText('c')).toBe(false);
    vi.stubGlobal('document', undefined);
    expect(await copyText('d')).toBe(false);
  });

  it('audit sentences read naturally in both forms', async () => {
    const { sentenceFor } = await import('../workspace/audit');
    expect(sentenceFor('retrieve_context', 'refund window', true)).toEqual({ text: 'looked up “refund window”', severity: 'read' });
    expect(sentenceFor('quarantine', 'f1', true).severity).toBe('alert');
    expect(sentenceFor('quarantine', 'f1', false)).toEqual({ text: 'tried to quarantine f1', severity: 'failed' });
    expect(sentenceFor('remember_fact', '', true).text).toBe('recorded a fact');
    expect(sentenceFor('delta', 'Synced from Mira', true, true).text).toBe('checked what changed — Synced from Mira');
    expect(sentenceFor('unknown_tool', '', true)).toEqual({ text: 'unknown_tool', severity: 'read' });
  });

  it('probeMcp tells connected, refused, wrong-token and unreachable apart', async () => {
    const { probeMcp } = await import('../workspace/mcp');
    reply(200, {});
    expect((await probeMcp('/mcp', 'qtk_t')).state).toBe('connected');
    const f = reply(401, {});
    const p = await probeMcp('/mcp', 'qtk_t');
    expect(p.state).toBe('unexpected');
    expect(p.detail).toMatch(/wants an Authorization header/);
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1].headers).toMatchObject({ Authorization: 'Bearer qtk_t' });
    reply(500, {});
    expect((await probeMcp()).detail).toMatch(/HTTP 500/);
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new Error('offline'))));
    expect(await probeMcp()).toMatchObject({ state: 'unreachable', detail: 'offline' });
  });
});
