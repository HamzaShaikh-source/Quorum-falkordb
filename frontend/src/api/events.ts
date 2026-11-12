import { fetchEventSource } from '@microsoft/fetch-event-source';
import { API_MODE, tokenFor } from './client';
import { devLog } from './devlog';
import { SseEvent } from './schemas';
import { mockBackend } from '../mocks/backend';

export type Connection = 'connecting' | 'live' | 'reconnecting' | 'offline';

interface Handlers {
  lastSeq: () => number;
  onEvent: (e: SseEvent) => void;
  onStatus: (c: Connection) => void;
}

function accept(raw: unknown, onEvent: (e: SseEvent) => void) {
  const r = SseEvent.safeParse(raw);
  if (r.success) {
    onEvent(r.data);
    return;
  }
  const env = raw as { seq?: unknown; type?: unknown } | null;
  const t = typeof env?.type === 'string' ? env.type : 'unknown';
  devLog('warn', `Unrecognized SSE event "${t}" kept in sequence, no state change: ${r.error.issues[0]?.path.join('.') ?? ''} ${r.error.issues[0]?.message ?? ''}`, true);
  if (typeof env?.seq === 'number') {
    const wrapped = SseEvent.safeParse({
      ts: Date.now(),
      epoch: 1,
      ...(raw as object),
      type: 'unknown',
      data: { original_type: t },
    });
    if (wrapped.success) onEvent(wrapped.data);
  }
}

/** Opens the event stream and returns a close function. Mock mode and live mode share the same validation path. */
export function openEventStream(h: Handlers): () => void {
  if (API_MODE === 'mock') {
    h.onStatus('live');
    return mockBackend.subscribe(tokenFor(), h.lastSeq(), (env) => accept(JSON.parse(JSON.stringify(env)), h.onEvent));
  }

  const ctl = new AbortController();
  const token = tokenFor();
  let retryCount = 0;
  h.onStatus('connecting');
  void fetchEventSource('/api/events', {
    signal: ctl.signal,
    openWhenHidden: true,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(h.lastSeq() > 0 ? { 'Last-Event-ID': String(h.lastSeq()) } : {}),
    },
    async onopen(res) {
      if (!res.ok) throw new Error(`events ${res.status}`);
      retryCount = 0;
      h.onStatus('live');
    },
    onmessage(msg) {
      if (msg.event === 'resync_required') {
        h.onEvent({ seq: 0, ts: Date.now(), epoch: 0, type: 'resync_required', data: {} } as SseEvent);
        return;
      }
      if (msg.event === 'heartbeat') return; // liveness only; the backend's heartbeat has no sequence number
      if (msg.event === 'error') {
        h.onStatus('reconnecting'); // e.g. FALKORDB_UNAVAILABLE: the shell shows the setup screen
        return;
      }
      if (!msg.data) return;
      try {
        accept(JSON.parse(msg.data), h.onEvent);
      } catch {
        devLog('warn', 'Ignored an SSE message that was not JSON.', true);
      }
    },
    onclose() {
      h.onStatus('reconnecting');
    },
    onerror() {
      h.onStatus('reconnecting');
      retryCount++;
      const backoff = Math.min(30000, 1000 * Math.pow(1.5, Math.min(retryCount, 8)));
      return Math.round(backoff);
    },
  });
  return () => {
    ctl.abort();
    h.onStatus('offline');
  };
}
