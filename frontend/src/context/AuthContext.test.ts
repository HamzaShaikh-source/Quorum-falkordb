import { describe, expect, it, beforeEach, vi, afterEach } from 'vitest';
import { DEFAULT_DEMO_TOKEN, EVALUATOR_PRESETS, initAutoAuth, TOKEN_STORAGE_KEY } from './AuthContext';

describe('AuthContext and Evaluator Auto-Login', () => {
  const localMap = new Map<string, string>();
  const sessionMap = new Map<string, string>();

  beforeEach(() => {
    localMap.clear();
    sessionMap.clear();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => localMap.get(k) ?? null,
      setItem: (k: string, v: string) => void localMap.set(k, v),
      removeItem: (k: string) => void localMap.delete(k),
    });
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => sessionMap.get(k) ?? null,
      setItem: (k: string, v: string) => void sessionMap.set(k, v),
      removeItem: (k: string) => void sessionMap.delete(k),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('initializes with token_human if no token is stored in localStorage or sessionStorage', () => {
    expect(localMap.has(TOKEN_STORAGE_KEY)).toBe(false);
    expect(sessionMap.has('quorum:token')).toBe(false);

    const token = initAutoAuth();
    expect(token).toBe(DEFAULT_DEMO_TOKEN);
    expect(localMap.get(TOKEN_STORAGE_KEY)).toBe('token_human');
    expect(sessionMap.get('quorum:token')).toBe('token_human');
  });

  it('preserves existing token if one already exists in localStorage', () => {
    localMap.set(TOKEN_STORAGE_KEY, 'token_acme_auditor');
    const token = initAutoAuth();
    expect(token).toBe('token_acme_auditor');
  });

  it('exposes the required 3 evaluator role presets with correct tokens and icons', () => {
    expect(EVALUATOR_PRESETS).toHaveLength(3);
    const [admin, auditor, agent] = EVALUATOR_PRESETS;

    expect(admin.token).toBe('token_human');
    expect(admin.role).toBe('human');
    expect(admin.label).toBe('Admin');
    expect(admin.icon).toBe('👑');

    expect(auditor.token).toBe('token_acme_auditor');
    expect(auditor.role).toBe('auditor');
    expect(auditor.label).toBe('Auditor');
    expect(auditor.icon).toBe('🛡️');

    expect(agent.token).toBe('token_acme_agent');
    expect(agent.role).toBe('agent');
    expect(agent.label).toBe('Worker Agent');
    expect(agent.icon).toBe('🤖');
  });
});
