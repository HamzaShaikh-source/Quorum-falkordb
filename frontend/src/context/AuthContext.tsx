import React, { createContext, useContext, useEffect, useState } from 'react';
import { signIn, type Role } from '../api/client';
import { setRole } from '../state/pipeline';
import { useStore } from '../state/store';

export const DEFAULT_DEMO_TOKEN = 'token_human';
export const TOKEN_STORAGE_KEY = 'quorum_token';

export const EVALUATOR_PRESETS = [
  { id: 'admin', role: 'human' as Role, label: 'Admin', token: 'token_human', icon: '👑' },
  { id: 'auditor', role: 'auditor' as Role, label: 'Auditor', token: 'token_acme_auditor', icon: '🛡️' },
  { id: 'agent', role: 'agent' as Role, label: 'Worker Agent', token: 'token_acme_agent', icon: '🤖' },
] as const;

export interface AuthContextValue {
  token: string | null;
  role: Role;
  tenant: string;
  loginAs: (token: string) => Promise<boolean>;
  switchPreset: (presetToken: string) => Promise<boolean>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}

export function initAutoAuth(): string {
  try {
    const existing =
      localStorage.getItem(TOKEN_STORAGE_KEY) ??
      localStorage.getItem('quorum:token') ??
      sessionStorage.getItem('quorum:token');
    const wasSignedOut = sessionStorage.getItem('quorum:signed_out') === '1';

    if (!existing && !wasSignedOut) {
      localStorage.setItem(TOKEN_STORAGE_KEY, DEFAULT_DEMO_TOKEN);
      sessionStorage.setItem('quorum:token', DEFAULT_DEMO_TOKEN);
      return DEFAULT_DEMO_TOKEN;
    }
    return existing ?? DEFAULT_DEMO_TOKEN;
  } catch {
    return DEFAULT_DEMO_TOKEN;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => {
    if (typeof window === 'undefined') return DEFAULT_DEMO_TOKEN;
    return initAutoAuth();
  });
  const storeRole = useStore((s) => s.role);
  const storeTenant = useStore((s) => s.tenant);

  useEffect(() => {
    // Ensure storage is initialized on initial client mount
    initAutoAuth();
  }, []);

  const loginAs = async (targetToken: string): Promise<boolean> => {
    const res = await signIn(targetToken);
    if (res.ok) {
      setTokenState(targetToken);
      try {
        sessionStorage.removeItem('quorum:signed_out');
      } catch {
        /* ignore */
      }
      return true;
    }
    return false;
  };

  const switchPreset = async (presetToken: string): Promise<boolean> => {
    const ok = await loginAs(presetToken);
    if (ok) {
      let nextRole: Role = 'human';
      if (presetToken.includes('auditor')) nextRole = 'auditor';
      else if (presetToken.includes('agent')) nextRole = 'agent';
      setRole(nextRole);
      useStore.getState().pushToast(`Active role: ${nextRole.toUpperCase()} (${presetToken})`, 'good');
      return true;
    }
    return false;
  };

  return (
    <AuthContext.Provider
      value={{
        token,
        role: storeRole,
        tenant: storeTenant,
        loginAs,
        switchPreset,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
