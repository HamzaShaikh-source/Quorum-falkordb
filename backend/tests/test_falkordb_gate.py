"""Without FalkorDB the product does not run: every API call is refused and the caller is told how to fix it."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import app

H = {"Authorization": "Bearer token_human"}


@pytest.fixture()
def no_falkordb(monkeypatch):
    monkeypatch.setattr(settings, "PERSISTENCE", "falkordb")
    monkeypatch.setattr(settings, "FALKORDB_URL", "redis://127.0.0.1:1")  # nothing listens here
    db.reset_client()
    db._health["checked_at"] = 0.0
    yield
    db.reset_client()
    db._health["checked_at"] = 0.0


def test_api_is_blocked_with_setup_steps(no_falkordb):
    with TestClient(app) as c:
        r = c.get("/api/graph", headers=H)
        assert r.status_code == 503
        body = r.json()["error"]
        assert body["code"] == "FALKORDB_UNAVAILABLE"
        assert "docker compose up -d falkordb" in " ".join(body["details"]["steps"])
        assert r.headers.get("Retry-After")


def test_every_api_route_is_blocked_not_just_graph(no_falkordb):
    with TestClient(app) as c:
        for method, path in (("get", "/api/meta"), ("get", "/api/handoffs"), ("post", "/api/scenario/reset"), ("get", "/api/events")):
            r = getattr(c, method)(path, headers=H)
            assert r.status_code == 503, path
            assert r.json()["error"]["code"] == "FALKORDB_UNAVAILABLE"


def test_health_reports_the_problem_and_stays_reachable(no_falkordb):
    with TestClient(app) as c:
        r = c.get("/api/health")
        assert r.status_code == 503
        j = r.json()
        assert j["status"] == "blocked"
        assert j["falkordb"]["up"] is False
        assert j["falkordb"]["error"]
        assert j["steps"]


def test_health_is_ok_with_a_real_falkordb(monkeypatch):
    monkeypatch.setattr(settings, "PERSISTENCE", "falkordb")
    db.reset_client()
    db._health["checked_at"] = 0.0
    with TestClient(app) as c:
        r = c.get("/api/health")
    if r.status_code != 200:
        pytest.skip("FalkorDB is not running. Start it with: docker compose up -d falkordb")
    j = r.json()
    assert j["falkordb"]["up"] is True and j["status"] == "ok"
