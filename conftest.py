"""Repository-level test setup: the suite runs the hub in development mode (demo and ad-hoc tokens exist only there)."""
import os

os.environ.setdefault("QUORUM_ENV", "development")
os.environ.setdefault("QUORUM_DEMO", "1")
os.environ.setdefault("QUORUM_AUTH_FAILURES_PER_MIN", "0")
