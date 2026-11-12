"""A fresh install is an empty workspace: no sample agents, no guided scenario, until QUORUM_DEMO=1 asks for them."""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as cfg
from app.config import Settings
from app.graph import queries
from app.main import app

client = TestClient(app)


@pytest.fixture()
def tenant():
    name = f"ci{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def H(t, role="human"):
    return {"Authorization": f"Bearer token_{t}_{role}"}


def test_demo_is_off_unless_asked_for():
    assert Settings.model_fields["DEMO"].default is False


def test_a_new_workspace_is_empty_and_says_demo_is_off(tenant, monkeypatch):
    monkeypatch.setattr(cfg.settings, "DEMO", False)
    assert client.get("/api/health").json()["demo"] is False
    g = client.get("/api/graph", headers=H(tenant)).json()
    assert g["nodes"] == [] and g["edges"] == []
    assert client.get("/api/agents", headers=H(tenant)).json() == []
    ws = client.get("/api/workspace", headers=H(tenant)).json()
    assert ws["teams"] == [] and ws["members"] == [] and ws["links"] == []
    assert client.get("/api/handoffs", headers=H(tenant)).json() == []
    assert client.get("/api/actions", headers=H(tenant)).json() == []


def test_the_guided_scenario_is_refused_when_demo_is_off(tenant, monkeypatch):
    monkeypatch.setattr(cfg.settings, "DEMO", False)
    r = client.post("/api/scenario/inject_poison", headers=H(tenant))
    assert r.status_code == 404 and "QUORUM_DEMO" in r.json()["error"]["message"]
    assert client.get("/api/graph", headers=H(tenant)).json()["nodes"] == [], "and nothing was injected"


def test_demo_on_brings_the_story_back(tenant, monkeypatch):
    monkeypatch.setattr(cfg.settings, "DEMO", True)
    assert client.get("/api/health").json()["demo"] is True
    assert len(client.get("/api/agents", headers=H(tenant)).json()) >= 3
