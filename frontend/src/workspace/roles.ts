import guide from '../../../backend/tests/fixtures/roles.json';
import type { MemberRole } from './store';

/**
 * Permission levels in plain words. The stored role names (agent, auditor, human) are what tokens and the API use; people see
 * Contributor, Reviewer and Admin. This is the backend's own table (GET /api/roles), snapshotted into a fixture that a backend
 * test keeps equal to what the code enforces, so this page cannot promise something the hub will refuse.
 */
export interface Level {
  role: MemberRole;
  name: string;
  summary: string;
}
export interface Capability {
  key: string;
  label: string;
  detail: string;
  agent: boolean | 'limited';
  auditor: boolean | 'limited';
  human: boolean | 'limited';
  note?: string;
}

export const LEVELS = guide.levels as Level[];
export const CAPABILITIES = guide.capabilities as Capability[];
export const levelName = (role: string) => LEVELS.find((l) => l.role === role)?.name ?? role;
export const levelSummary = (role: string) => LEVELS.find((l) => l.role === role)?.summary ?? '';
