"""The use guide: one source, read by the chat tool, the REST endpoint, the console and the docs, and it only ever names tools that exist."""
import importlib.util
import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.graph import queries
from app.main import app
from app.mcp_embed import TOOLS
from app.services import guide
from app.services import mcp_tools as t

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def tenant():
    name = f"gd{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def tok(tenant, role="agent"):
    return f"token_{tenant}_{role}"


def test_every_tool_the_guide_names_really_exists():
    g = guide.load()
    named = set()
    for topic in g["topics"]:
        named |= set(topic["tools"])
        named |= set(re.findall(r"\b(quorum_[a-z_]+|[a-z]+_[a-z_]+)\(", " ".join(topic["steps"])))
    named |= set(re.findall(r"\b([a-z]+_[a-z_]+)\(", " ".join(g["rules"])))
    unknown = {n for n in named if n not in TOOLS and n not in {"member_add", "member_set_level", "member_new_token", "member_remove", "leaver_report", "pause_link", "resume_link", "team_create", "team_seats", "team_remove"}}
    assert not unknown, f"the guide mentions tools that do not exist: {sorted(unknown)}"


def test_every_tool_has_a_place_in_the_guide_or_the_help():
    from app.services.mcp_chat import COMMANDS

    assert set(COMMANDS) == set(TOOLS), "quorum_help lists exactly the registered tools"
    in_guide = {x for topic in guide.load()["topics"] for x in topic["tools"]}
    missing = {n for n in TOOLS if n not in in_guide and n not in {"quorum_help", "quorum_guide", "get_map"} | {"get_map"}}
    assert not missing, f"tools no guide topic teaches: {sorted(missing)}"


def test_the_chat_tool_lists_topics_serves_each_and_refuses_unknown_ones(tenant):
    a = tok(tenant)
    idx = t.quorum_guide("", "", a)
    assert "start" in idx["topics"] and "install" in idx["topics"] and "quorum_guide" in idx["text"]
    for topic in guide.topic_ids():
        out = t.quorum_guide(topic, "claude-code", a)
        assert out.get("success", True) is not False and len(out["text"]) > 80, topic
    bad = t.quorum_guide("nonsense", "", a)
    assert bad["success"] is False and "Topics:" in bad["error"]
    assert "Quorum is shared memory" in t.quorum_guide("index", "", a)["text"]
    everything = t.quorum_guide("all", "", a)
    assert "Correcting a wrong belief" in everything["text"] and everything["tokens"] > 500


def test_the_install_plan_names_the_file_the_rules_and_never_a_secret(tenant):
    a = tok(tenant)
    g = guide.load()
    for client_name, info in g["clients"].items():
        plan = t.quorum_guide("install", client_name, a)
        assert info["file"] in plan["text"] and plan["file"] == info["file"]
        for rule in g["rules"]:
            assert rule in plan["text"]
        assert "qtk_" not in plan["text"] and "qrm_" not in plan["text"] and "Bearer " not in plan["text"]
        assert ("Capture hook" in plan["text"]) == info["hooks"]
    assert "CLAUDE.md" in t.quorum_guide("install", "Claude", a)["text"], "aliases are understood"
    assert "wherever this AI keeps" in t.quorum_guide("install", "some-new-ai", a)["text"], "an unknown AI gets the generic plan"


def test_the_guide_needs_a_token(tenant):
    from app.services.mcp_guard import AuthError

    with pytest.raises(AuthError):
        t.quorum_guide("start", "", "")


def test_the_rest_endpoint_serves_the_same_data(tenant):
    h = {"Authorization": f"Bearer {tok(tenant)}"}
    r = client.get("/api/guide", headers=h)
    assert r.status_code == 200
    body = r.json()
    g = guide.load()
    assert [x["id"] for x in body["topics"]] == [x["id"] for x in g["topics"]] and body["rules"] == g["rules"]
    assert client.get("/api/guide").status_code == 401


def test_the_markdown_guide_is_generated_from_the_same_json():
    spec = importlib.util.spec_from_file_location("gen_guide_md", ROOT / "scripts" / "gen_guide_md.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    committed = (ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf8").replace("\r\n", "\n")
    assert committed == gen.render(json.loads((ROOT / "backend" / "app" / "services" / "guide.json").read_text(encoding="utf8"))), "run: python scripts/gen_guide_md.py"
