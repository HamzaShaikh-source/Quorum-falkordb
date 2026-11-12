"""
FalkorDB connection and health.

Quorum's system of record is FalkorDB. When it is not reachable the API refuses to serve (HTTP 503,
code FALKORDB_UNAVAILABLE) and says exactly what to do about it. Nothing quietly falls back to memory.
"""
import threading
import time
from typing import Any, Dict
from urllib.parse import urlparse

from app.config import settings

SETUP_STEPS = [
    "Start FalkorDB: docker compose up -d falkordb",
    "Check it answers: python scripts/doctor.py",
    "Make sure QUORUM_FALKORDB_URL points at it (default redis://localhost:6379)",
    "Restart the backend if it was started before FalkorDB, then reload the console",
]


class FalkorUnavailable(Exception):
    """FalkorDB cannot be reached or is not set up correctly."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_client = None
_client_lock = threading.Lock()
_health_lock = threading.Lock()
_health_refreshing = False  # single-flight: one caller pings, the rest reuse the last answer meanwhile
_health: Dict[str, Any] = {"checked_at": 0.0, "up": False, "error": "not checked yet", "latency_ms": None, "version": None}
HEALTH_TTL_S = 1.0


def redacted_url() -> str:
    u = urlparse(settings.FALKORDB_URL)
    host = u.hostname or "localhost"
    port = u.port or 6379
    return f"{u.scheme or 'redis'}://{host}:{port}"


def get_client():
    """Shared FalkorDB client. Raises FalkorUnavailable if the library or the server is missing."""
    global _client
    with _client_lock:
        if _client is None:
            try:
                from falkordb import FalkorDB
            except Exception as e:  # pragma: no cover - dependency problem, not a runtime one
                raise FalkorUnavailable(f"The falkordb Python package is not installed ({e}). Run: pip install -r backend/requirements.txt")
            u = urlparse(settings.FALKORDB_URL)
            try:
                from redis.backoff import NoBackoff
                from redis.retry import Retry

                _client = FalkorDB(
                    host=u.hostname or "localhost",
                    port=u.port or 6379,
                    username=u.username,
                    password=u.password,
                    socket_connect_timeout=1,
                    socket_timeout=4,
                    retry=Retry(NoBackoff(), 0),  # fail fast: a down database must be reported in a second, not after backoff
                )
            except Exception as e:
                raise FalkorUnavailable(f"Cannot create a FalkorDB client for {redacted_url()}: {e}")
        return _client


def reset_client() -> None:
    global _client
    with _client_lock:
        _client = None


def check_health(force: bool = False) -> Dict[str, Any]:
    """Pings FalkorDB and verifies the graph module is loaded. Cached for one second so hot paths stay cheap."""
    now = time.time()
    global _health_refreshing
    with _health_lock:
        if not force and now - _health["checked_at"] < HEALTH_TTL_S:
            return dict(_health)
        if not force and _health_refreshing and _health["checked_at"] > 0:
            return dict(_health)  # a refresh is already in flight: under load, never stampede the database with pings
        _health_refreshing = True
    t0 = time.time()
    result: Dict[str, Any] = {"up": False, "error": None, "latency_ms": None, "version": None}
    conn = None
    try:
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        u = urlparse(settings.FALKORDB_URL)
        # A throwaway connection with tight timeouts, so a dead database is answered in about a second.
        conn = redis.Redis(
            host=u.hostname or "localhost",
            port=u.port or 6379,
            username=u.username,
            password=u.password,
            socket_connect_timeout=1,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
            decode_responses=True,
        )
        conn.ping()
        mods = conn.execute_command("MODULE", "LIST")
        names = [(m.get("name") if isinstance(m, dict) else m[1]) for m in mods]
        names = [n.decode() if isinstance(n, bytes) else str(n) for n in names]
        if "graph" not in names:
            result["error"] = "Redis is running but the FalkorDB graph module is not loaded. Use the falkordb/falkordb image."
        else:
            result["up"] = True
            info = conn.info("server")
            result["version"] = info.get("redis_version")
    except FalkorUnavailable as e:
        result["error"] = e.reason
    except Exception as e:
        reset_client()
        result["error"] = f"Cannot reach FalkorDB at {redacted_url()}: {type(e).__name__}: {e}"[:300]
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    result["latency_ms"] = round((time.time() - t0) * 1000, 1)
    with _health_lock:
        _health.update(result)
        _health["checked_at"] = time.time()
        _health_refreshing = False
        return dict(_health)


def require_db() -> None:
    h = check_health()
    if not h["up"]:
        raise FalkorUnavailable(h["error"] or "FalkorDB is not reachable")


def graph_for(tenant: str):
    """The tenant's API graph. Named quorum:<tenant>, separate from the benchmark graphs (tenant_<id>:commons)."""
    return get_client().select_graph(f"quorum:{tenant}")


def health_payload() -> Dict[str, Any]:
    h = check_health(force=True)
    return {
        "status": "ok" if (h["up"] or settings.PERSISTENCE == "memory") else "blocked",
        "persistence": settings.PERSISTENCE,
        "falkordb": {
            "up": h["up"],
            "url": redacted_url(),
            "latency_ms": h["latency_ms"],
            "version": h["version"],
            "error": None if h["up"] else h["error"],
        },
        "steps": [] if h["up"] else SETUP_STEPS,
    }
