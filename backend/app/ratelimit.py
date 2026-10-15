"""
A small token-bucket limiter, one bucket per caller. In memory, per process: it protects the hub from a runaway agent
loop or a leaked token, it is not a distributed quota. Zero disables a limit.
"""
import threading
import time
from typing import Dict, Tuple


class RateLimiter:
    def __init__(self, per_minute: int, burst: int | None = None, max_entries: int = 10000):
        self.per_minute = per_minute
        self.burst = burst if burst is not None else max(1, per_minute // 6 or 1)
        self.max_entries = max_entries
        self._b: Dict[str, Tuple[float, float]] = {}
        self._last_prune = time.monotonic()
        self._lock = threading.Lock()

    def configure(self, per_minute: int, burst: int | None = None) -> None:
        with self._lock:
            self.per_minute = per_minute
            self.burst = burst if burst is not None else max(1, per_minute // 6 or 1)
            self._b.clear()
            self._last_prune = time.monotonic()

    def allow(self, key: str) -> Tuple[bool, float]:
        """Returns (allowed, seconds_until_next_token)."""
        if self.per_minute <= 0:
            return True, 0.0
        rate = self.per_minute / 60.0
        cap = float(max(self.burst, 1))
        now = time.monotonic()
        with self._lock:
            if now - self._last_prune > 60.0 or len(self._b) > self.max_entries:
                cutoff = now - 600.0
                self._b = {k: v for k, v in self._b.items() if v[1] >= cutoff}
                if len(self._b) > self.max_entries:
                    oldest_keys = sorted(self._b.keys(), key=lambda k: self._b[k][1])[: len(self._b) - self.max_entries]
                    for k in oldest_keys:
                        self._b.pop(k, None)
                self._last_prune = now
            tokens, last = self._b.get(key, (cap, now))
            tokens = min(cap, tokens + (now - last) * rate)
            if tokens >= 1.0:
                self._b[key] = (tokens - 1.0, now)
                res = True, 0.0
            else:
                self._b[key] = (tokens, now)
                res = False, (1.0 - tokens) / rate
            if len(self._b) > self.max_entries:
                oldest_key = min(self._b.keys(), key=lambda k: self._b[k][1])
                self._b.pop(oldest_key, None)
            return res


def _mk(name: str, default: int) -> RateLimiter:
    from app.config import settings

    return RateLimiter(int(getattr(settings, name, default)))


# Tool calls (one bucket per token) and HTTP requests (one bucket per token or address).
tool_limiter = _mk("RATE_LIMIT_TOOLS_PER_MIN", 600)
http_limiter = _mk("RATE_LIMIT_HTTP_PER_MIN", 3000)


class FailureTracker:
    """Counts failed sign-ins per address. After `limit` failures inside a minute the address is locked out until the oldest ages out."""

    def __init__(self, limit: int, max_entries: int = 5000):
        self.limit = limit
        self.max_entries = max_entries
        self._hits: Dict[str, list] = {}
        self._last_prune = time.monotonic()
        self._lock = threading.Lock()

    def configure(self, limit: int) -> None:
        with self._lock:
            self.limit = limit
            self._hits.clear()
            self._last_prune = time.monotonic()

    def _recent(self, key: str, now: float) -> list:
        hits = [t for t in self._hits.get(key, []) if now - t < 60]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def locked(self, key: str) -> float:
        """Seconds the address must wait, or 0."""
        if self.limit <= 0:
            return 0.0
        now = time.monotonic()
        with self._lock:
            hits = self._recent(key, now)
            return max(0.0, 60 - (now - hits[0])) if len(hits) >= self.limit else 0.0

    def record(self, key: str) -> None:
        if self.limit <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_prune > 30.0 or len(self._hits) > self.max_entries:
                for k in list(self._hits.keys()):
                    if not self._recent(k, now):
                        self._hits.pop(k, None)
                self._last_prune = now
            self._recent(key, now)
            self._hits.setdefault(key, []).append(now)
            if len(self._hits) > self.max_entries:
                # Prioritize unlocked keys first (so attacker dummy traffic cannot evict locked IPs), then oldest
                oldest = min(self._hits.keys(), key=lambda k: (len(self._hits[k]) >= self.limit, self._hits[k][-1] if self._hits[k] else 0))
                self._hits.pop(oldest, None)


from app.config import settings as _settings  # noqa: E402

auth_failures = FailureTracker(int(_settings.AUTH_FAILURES_PER_MIN))
