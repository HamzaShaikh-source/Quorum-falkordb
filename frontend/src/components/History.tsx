import { copy } from '../copy';
import { graphBus } from '../graph/bus';
import type { FeedGroup } from '../state/reducer';
import { useStore, type FeedFilter } from '../state/store';
import { Icon } from './Icon';
import { fmtTime, Skeleton } from './ui';

const FILTERS: FeedFilter[] = ['all', 'graph', 'actions', 'handoffs', 'system'];
const TONE_ICON: Record<string, string> = { bad: 'alert', good: 'check', review: 'user', warn: 'clock', neutral: 'fact' };

export function HistoryFeed() {
  const feed = useStore((s) => s.feed);
  const filter = useStore((s) => s.feedFilter);
  const loaded = useStore((s) => s.loaded);
  const story = useStore((s) => s.story);
  const set = useStore((s) => s.set);
  const shown = feed.filter((f) => filter === 'all' || f.group === (filter as FeedGroup));

  return (
    <section className="card history" aria-label={copy.history.title}>
      <header className="card-h">
        <h2>{copy.history.title}</h2>
        <div className="seg" role="group" aria-label={copy.history.filterLabel}>
          {FILTERS.map((f) => (
            <button key={f} className={`seg-btn${filter === f ? ' on' : ''}`} aria-pressed={filter === f} onClick={() => set({ feedFilter: f })}>
              {copy.history.filters[f]}
            </button>
          ))}
        </div>
      </header>
      {!loaded && <Skeleton lines={4} />}
      {loaded && shown.length === 0 && (
        <div className="feed-empty">
          <Icon name="clock" size={22} />
          <p>{story ? copy.history.empty : copy.history.emptyLive}</p>
        </div>
      )}
      <ol className="feed" aria-live="polite" aria-relevant="additions">
        {shown.map((f) => (
          <li key={f.id} className={`feed-item tone-${f.tone}`}>
            <button
              className="feed-btn"
              disabled={!f.nodeId}
              onClick={() => {
                if (!f.nodeId) return;
                set({ selectedId: f.nodeId, tab: 'graph' });
                graphBus.focus.emit(f.nodeId);
              }}
            >
              <span className="feed-ico">
                <Icon name={TONE_ICON[f.tone] ?? 'fact'} size={13} />
              </span>
              <span className="feed-text">{f.text}</span>
              <span className="feed-time num">{fmtTime(f.ts)}</span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
