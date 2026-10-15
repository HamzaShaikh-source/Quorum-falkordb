import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { Resume, GraphNode, GraphResponse } from './schemas';

type Client = typeof import('./client');
let c: Client;

function reply(status: number, body: unknown, headers: Record<string, string> = { 'Content-Type': 'application/json' }) {
  const f = vi.fn(async () => {
    const textBody = typeof body === 'string' ? body : JSON.stringify(body);
    return {
      ok: status < 400,
      status,
      headers: new Headers(headers),
      text: async () => textBody,
      json: async () => {
        if (typeof body === 'string') {
          return JSON.parse(body);
        }
        return body;
      },
    } as unknown as Response;
  });
  vi.stubGlobal('fetch', f);
  return f;
}

function replyRaw(status: number, rawText: string) {
  const f = vi.fn(async () => {
    return {
      ok: status < 400,
      status,
      text: async () => rawText,
      json: async () => {
        return JSON.parse(rawText);
      },
    } as unknown as Response;
  });
  vi.stubGlobal('fetch', f);
  return f;
}

const Thing = z.object({ id: z.string() });

describe('Subagent_16 QA Stress & Boundary Suite', () => {
  beforeEach(async () => {
    vi.resetModules();
    vi.stubEnv('VITE_API_MODE', 'live');
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => storage.get(k) ?? null,
      setItem: (k: string, v: string) => void storage.set(k, v),
      removeItem: (k: string) => void storage.delete(k),
    });
    c = await import('./client');
    c.session.tenant = 'acme';
    c.session.role = 'human';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  describe('1. Corrupted & Expired SessionStorage Tokens', () => {
    it('corrupted token string "undefined" is dropped after whoami returns 401', async () => {
      sessionStorage.setItem('quorum:token', 'undefined');
      reply(401, { error: { code: 'UNAUTHENTICATED', message: 'Invalid token' } });
      const res = await c.resolveAuth();
      expect(res).toBe('needed');
      expect(sessionStorage.getItem('quorum:token')).toBeNull();
    });

    it('corrupted token string "[object Object]" is dropped after whoami returns 401', async () => {
      sessionStorage.setItem('quorum:token', '[object Object]');
      reply(401, { error: { code: 'UNAUTHENTICATED', message: 'Invalid token' } });
      const res = await c.resolveAuth();
      expect(res).toBe('needed');
      expect(sessionStorage.getItem('quorum:token')).toBeNull();
    });

    it('expired token during active api call triggers onUnauthorized event when signed in with token', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_expired');
      const unauthorizedListener = vi.fn();
      const off = c.onUnauthorized(unauthorizedListener);

      reply(401, { error: { code: 'UNAUTHENTICATED', message: 'Token expired' } });

      await expect(c.api(Thing, 'GET', 'protected-resource')).rejects.toMatchObject({
        status: 401,
        code: 'UNAUTHENTICATED',
      });

      expect(unauthorizedListener).toHaveBeenCalledTimes(1);
      off();
    });

    it('whoami returning 200 with empty body rejects invalid session and evicts stored token', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_weird');
      reply(200, {}); // Missing tenant and role from backend
      const res = await c.resolveAuth();
      expect(res).toBe('needed');
      expect(sessionStorage.getItem('quorum:token')).toBeNull();
    });

    it('resolveAuth updates session.tenant and session.role correctly', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_auditor');
      reply(200, { tenant: 'globex', role: 'auditor' });
      const res = await c.resolveAuth();
      expect(res).toBe('ok');
      expect(c.session.tenant).toBe('globex');
      expect(c.session.role).toBe('auditor');
    });
  });

  describe('2. Unexpected HTTP Status Codes & Truncated Bodies', () => {
    it('handles 429 RATE_LIMITED with structured JSON body', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_valid');
      reply(429, { error: { code: 'RATE_LIMITED', message: 'Too many requests. Retry in 5.0s.' } });

      await expect(c.api(Thing, 'GET', 'high-load-endpoint')).rejects.toMatchObject({
        status: 429,
        code: 'RATE_LIMITED',
        message: 'Too many requests. Retry in 5.0s.',
      });
    });

    it('handles 429 from reverse proxy with plain text (non-JSON) body gracefully', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_valid');
      replyRaw(429, '429 Too Many Requests: Rate limit exceeded');

      await expect(c.api(Thing, 'GET', 'high-load-endpoint')).rejects.toMatchObject({
        status: 429,
        code: 'INTERNAL',
        message: 'HTTP 429',
      });
    });

    it('handles 502 Bad Gateway from Nginx with HTML error body gracefully', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_valid');
      replyRaw(502, '<html><body><h1>502 Bad Gateway</h1></body></html>');

      await expect(c.api(Thing, 'GET', 'downstream-service')).rejects.toMatchObject({
        status: 502,
        code: 'INTERNAL',
        message: 'HTTP 502',
      });
    });

    it('handles 504 Gateway Timeout from load balancer with empty or text body gracefully', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_valid');
      replyRaw(504, 'Gateway Timeout');

      await expect(c.api(Thing, 'GET', 'slow-service')).rejects.toMatchObject({
        status: 504,
        code: 'INTERNAL',
        message: 'HTTP 504',
      });
    });

    it('handles truncated JSON body on 200 response without unhandled crash', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_valid');
      replyRaw(200, '{"id": "partially_transmitted_');

      await expect(c.api(Thing, 'GET', 'interrupted-stream')).rejects.toMatchObject({
        code: 'SCHEMA',
        status: 200,
      });
    });

    it('handles 503 generic Service Unavailable without falsely triggering FalkorDB blocked overlay', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_valid');
      const { onBlocked } = await import('./blocked');
      const blockedListener = vi.fn();
      const off = onBlocked(blockedListener);

      reply(503, { error: { code: 'MAINTENANCE', message: 'Scheduled maintenance' } });

      await expect(c.api(Thing, 'GET', 'maintenance-endpoint')).rejects.toMatchObject({
        status: 503,
        code: 'MAINTENANCE',
      });

      expect(blockedListener).not.toHaveBeenCalled();
      off();
    });

    it('handles 503 FALKORDB_UNAVAILABLE by dispatching to reportBlocked', async () => {
      sessionStorage.setItem('quorum:token', 'qtk_valid');
      const { onBlocked } = await import('./blocked');
      const blockedListener = vi.fn();
      const off = onBlocked(blockedListener);

      reply(503, {
        error: {
          code: 'FALKORDB_UNAVAILABLE',
          message: 'Database connection failed',
          details: { reason: 'Connection refused', url: 'redis://localhost:6379', steps: ['docker compose up -d'] },
        },
      });

      await expect(c.api(Thing, 'GET', 'db-dependent')).rejects.toMatchObject({
        status: 503,
        code: 'FALKORDB_UNAVAILABLE',
      });

      expect(blockedListener).toHaveBeenCalledWith({
        reason: 'Connection refused',
        url: 'redis://localhost:6379',
        steps: ['docker compose up -d'],
      });
      off();
    });
  });

  describe('3. Schema Fidelity Checks (Zod vs Backend Models)', () => {
    it('DEFECT REPRO: Resume schema fails when backend returns null for optional source_ref / quote', () => {
      const backendPayload = {
        session_id: 'sess_123',
        summary: 'Agent recap',
        memories: [
          {
            fact_id: 'fact_1',
            text: 'Important discovery',
            source_ref: null, // backend AgentMemoryItem has source_ref: Optional[str] = None
            quote: null,      // backend AgentMemoryItem has quote: Optional[str] = None
          },
        ],
      };

      const result = Resume.safeParse(backendPayload);
      expect(result.success).toBe(true);
      if (result.success) {
        expect(result.data.memories[0]?.source_ref).toBeUndefined();
        expect(result.data.memories[0]?.quote).toBeUndefined();
      }
    });

    it('GraphNode schema properly recognizes backend NodeKind.ALERT ("alert")', () => {
      const backendNode = {
        id: 'node_alert_1',
        kind: 'alert', // backend NodeKind.ALERT
        label: 'System Alert',
        status: 'HEALTHY',
        stale: false,
      };

      const parsed = GraphNode.safeParse(backendNode);
      expect(parsed.success).toBe(true);
      if (parsed.success) {
        expect(parsed.data.kind).toBe('alert');
      }
    });

    it('GraphResponse schema succeeds when as_of is omitted / undefined', () => {
      const backendGraph = {
        contract_version: '0.1.0',
        tenant: 'acme',
        epoch: 1,
        seq: 10,
        // as_of is omitted (backend has Optional[int] = None)
        generated_at: Date.now(),
        truncated: false,
        nodes: [],
        edges: [],
      };

      const parsed = GraphResponse.safeParse(backendGraph);
      expect(parsed.success).toBe(true);
    });
  });
});
