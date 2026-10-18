"""The documentation is part of the product: links resolve, every tool it names exists, and the charts are drawn from the committed results."""
import importlib.util
import re
from pathlib import Path

from app.mcp_embed import TOOLS

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").glob("*.md"))]


def _links(md: Path):
    text = md.read_text(encoding="utf8")
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    for target in re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:src|href)="([^"]+)"', text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        yield target.split("#")[0]


def test_every_relative_link_and_image_resolves():
    missing = []
    for md in DOCS:
        for target in _links(md):
            if target and not (md.parent / target).resolve().exists():
                missing.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not missing, "broken links:\n" + "\n".join(missing)


def test_every_doc_is_reachable_from_the_docs_index():
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf8")
    unlisted = [p.name for p in (ROOT / "docs").glob("*.md") if p.name != "README.md" and p.name not in index]
    assert not unlisted, f"docs/README.md does not list: {unlisted}"


def test_the_readme_names_every_tool_and_the_right_count():
    readme = (ROOT / "README.md").read_text(encoding="utf8")
    absent = [t for t in TOOLS if f"`{t}`" not in readme]
    assert not absent, f"README tool table is missing: {absent}"
    assert f"{len(TOOLS)} MCP tools" in readme and f"MCP%20tools-{len(TOOLS)}" in readme


def test_the_readme_tool_names_are_all_real():
    readme = (ROOT / "README.md").read_text(encoding="utf8")
    section = readme.split("## What your AI gets")[1].split("\n## ")[0]
    named = set(re.findall(r"`([a-z_]+)`", section))
    unknown = sorted(n for n in named if n not in TOOLS and "_" in n and not n.startswith("QUORUM"))
    assert not unknown, f"README names tools that do not exist: {unknown}"


def test_the_charts_match_the_committed_results():
    spec = importlib.util.spec_from_file_location("gen_charts", ROOT / "scripts" / "gen_charts.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    for name, make in (("evidence.svg", gen.evidence), ("cascade.svg", gen.cascade)):
        committed = (ROOT / "docs" / "charts" / name).read_text(encoding="utf8").replace("\r\n", "\n").rstrip("\n")
        assert committed == make(), f"docs/charts/{name} is stale: run python scripts/gen_charts.py"
