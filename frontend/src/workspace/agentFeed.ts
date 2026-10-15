import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { API_MODE } from '../api/client';
import { sentenceFor } from './audit';
import { useLive } from './live';
import { getAudit, type AuditEvent } from './server';

export interface FeedRow {
  id: number;
  ts: number;
  who: string;
  tool: string;
  text: string;
  severity: AuditEvent['severity'];
  ok: boolean;
}

/** One list of "what the AI just did" for every screen that shows it: the audit log when live, the simulated feed otherwise. */
export function useAgentFeed(limit = 12): { rows: FeedRow[]; loading: boolean; failed: boolean } {
  const live = API_MODE === 'live';
  const sample = useLive((s) => s.activity);
  const q = useQuery({ queryKey: ['audit', 'feed', limit], queryFn: () => getAudit({ limit }), enabled: live, refetchInterval: 4000 });
  const events = q.data?.events;
  const rows = useMemo<FeedRow[]>(() => {
    if (live) return (events ?? []).map((e) => ({ id: e.id, ts: e.ts, who: e.who, tool: e.tool, text: e.text, severity: e.severity, ok: e.ok }));
    return sample.slice(0, limit).map((a) => ({ id: a.id, ts: a.ts, who: a.member, tool: a.tool, ...sentenceFor(a.tool, a.summary, true, true), ok: true }));
  }, [live, events, sample, limit]);
  return { rows, loading: live && q.isLoading, failed: live && q.isError };
}

/** Plain words for what each kind of step is for, so the feed explains itself. */
export const PURPOSE: Record<AuditEvent['severity'], string> = {
  read: 'Reading memory to answer a question. Nothing changed.',
  write: 'Adding to memory. This becomes something other agents may rely on.',
  alert: 'A safety step: quarantining, restoring or reviewing a belief.',
  failed: 'Refused. The agent tried something its token or the safety rules do not allow.',
};
