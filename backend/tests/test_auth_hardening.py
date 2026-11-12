"""Tokens: configured ones work, forged ones do not (in production), and garbage never becomes a human."""
from fastapi.testclient import TestClient

from app.auth import resolve_token
from app.config import settings
from app.main import app
from app.services import mcp_tools


def test_garbage_token_is_not_a_human_anymore():
    with TestClient(app) as c:
        assert c.get("/api/meta", headers={"Authorization": "Bearer nonsense"}).status_code == 401
        assert c.get("/api/meta", headers={"Authorization": "Bearer token_x_wizard"}).status_code == 401


def test_configured_tokens_work():
    assert resolve_token("token_human")["role"] == "human"
    assert resolve_token("token_agent_scout")["tenant"] == "acme"


def test_dynamic_tokens_are_refused_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "ALLOW_DYNAMIC_TOKENS", None)
    assert resolve_token("token_globex_auditor") is None
    assert resolve_token("token_auditor") is not None  # a configured demo token, still valid
    with TestClient(app) as c:
        assert c.get("/api/meta", headers={"Authorization": "Bearer token_globex_auditor"}).status_code == 401


def test_dynamic_tokens_work_in_development(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "ALLOW_DYNAMIC_TOKENS", None)
    assert resolve_token("token_globex_auditor") == {"tenant": "globex", "role": "auditor", "agent_id": "auditor_globex"}


def test_mcp_tool_rejects_a_bad_token():
    out = mcp_tools.guarded(mcp_tools.retrieve_context)("anything", "not-a-token")
    assert out["success"] is False and "token" in out["error"].lower()


def test_cors_is_not_wildcard():
    assert "*" not in settings.cors_origin_list()


def test_demo_tokens_are_not_valid_in_production_when_tokens_are_configured():
    """Config is read at import, so this runs in a fresh interpreter instead of reloading the module the rest of the suite shares."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = "import json; from app.config import settings; print(json.dumps(settings.DEMO_TOKENS))"
    env = {**os.environ, "QUORUM_ENV": "production", "QUORUM_TOKENS_JSON": json.dumps({"real-token-1": {"tenant": "acme", "role": "human", "agent_id": "ops"}})}
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    tokens = json.loads(out.stdout.strip().splitlines()[-1])
    assert tokens == {"real-token-1": {"tenant": "acme", "role": "human", "agent_id": "ops"}}
    assert "token_human" not in tokens
