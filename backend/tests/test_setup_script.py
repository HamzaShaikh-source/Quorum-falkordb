"""scripts/setup.py writes an .env that docker compose reads back exactly, with strong secrets and a matching admin token."""
import importlib.util
import json
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location("quorum_setup", Path(__file__).resolve().parents[2] / "scripts" / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def parse_like_compose(text: str) -> dict:
    """The subset of dotenv rules compose uses: bare, 'single' (literal) and "double" (escapes, $$) values."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        if v.startswith("'") and v.endswith("'"):
            v = v[1:-1]
        elif v.startswith('"') and v.endswith('"'):
            v = re.sub(r"\\(.)", r"\1", v[1:-1]).replace("$$", "$")
        out[k] = v
    return out


def test_values_survive_quoting_including_apostrophes_spaces_and_json(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "ENV", tmp_path / ".env")
    monkeypatch.setattr(setup, "free_subnet", lambda: "172.29.90.0/24")
    env = setup.write_env("Maya O'Neil", "Support Desk", "http://192.168.1.20:8000")
    raw = (tmp_path / ".env").read_bytes()
    assert b"\r" not in raw, "LF only: compose parses the same on every OS"
    back = parse_like_compose(raw.decode("utf8"))
    assert back == env
    assert back["QUORUM_HUB_NAME"] == "Maya O'Neil's Quorum"
    tokens = json.loads(back["QUORUM_TOKENS_JSON"])
    assert tokens[back["QUORUM_ADMIN_TOKEN"]] == {"tenant": "support-desk", "role": "human", "agent_id": "Maya O'Neil"}
    assert back["QUORUM_ENV"] == "production" and back["QUORUM_DEMO"] == "0"
    assert len(back["FALKORDB_PASSWORD"]) >= 24 and back["QUORUM_ADMIN_TOKEN"].startswith("qtk_") and len(back["QUORUM_ADMIN_TOKEN"]) > 40


def test_each_run_makes_new_secrets_and_the_slug_is_a_valid_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "ENV", tmp_path / ".env")
    monkeypatch.setattr(setup, "free_subnet", lambda: "172.29.90.0/24")
    a = setup.write_env("A", "Team One", "http://x:8000")
    b = setup.write_env("A", "Team One", "http://x:8000")
    assert a["QUORUM_ADMIN_TOKEN"] != b["QUORUM_ADMIN_TOKEN"] and a["FALKORDB_PASSWORD"] != b["FALKORDB_PASSWORD"]
    for name in ("My Team!!", "  ", "ÜBER Team", "a" * 80):
        assert re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", setup.slug(name))


def test_the_prompt_names_every_step_the_chat_layer_supports():
    from app.services.mcp_chat import COMMANDS

    p = setup.PROMPT.format(mcp="http://h:8000/mcp", token="qtk_x")
    for cmd in ("quorum_help", "quorum_start", "quorum_discover", "quorum_connect", "quorum_inbox", "quorum_respond", "quorum_status", "quorum_manage", "retrieve_context", "remember_fact"):
        assert cmd in COMMANDS and cmd in p
    assert "Authorization: Bearer qtk_x" in p and "http://h:8000/mcp" in p
