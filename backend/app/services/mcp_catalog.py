"""
The MCP tool catalog, read from the tools themselves (signature and docstring) plus a small table of who may call a tool
and what it saves. The console's Connect page fetches this instead of keeping its own copy.
"""
import inspect
from typing import Any, Dict, List

# tool -> (who, writes, what it saves, one-line summary for people)
META: Dict[str, tuple] = {
    "retrieve_context": ("any", False, "Compact lines with short ids and provenance, trimmed to your budget", 'Healthy, in-scope facts for a query, best match first. format="compact" costs a fraction of the tokens'),
    "remember_fact": ("agent", True, "Store once, reference by id everywhere", 'Store a fact with its source. scope="team" keeps it private to your team (and linked teams)'),
    "observe": ("agent", True, "Facts are captured from what you already wrote, with no extra tool call per fact", "Save the facts in text you already produced (numbers with units, policies, limits, dates), each with its source sentence"),
    "derive": ("agent", True, "Conclusions are linked, not re-explained", "Record a conclusion or decision from parent facts"),
    "stage_action": ("agent", True, "One call replaces a long approval thread", "Stage an action; the quorum gate decides"),
    "execute_action": ("agent", True, "No retries from stale context", "Run a staged action under the atomic guard"),
    "claim_handoff": ("agent", True, "Two agents never do the same work", "Claim a task with an atomic lease"),
    "quarantine": ("auditor", True, "Bad context is removed, not re-read", "Quarantine a fact and everything built on it"),
    "restore": ("auditor", True, "Undo without re-ingesting", "Restore a quarantined fact and valid descendants"),
    "explain_blast_radius": ("any", False, "Ids only, no node bodies", "Dry run: what a quarantine would touch"),
    "review_handoff": ("human", True, "Humans decide once, agents read the verdict", "Approve or reject a gate-blocked review"),
    "get_map": ("any", False, "Orientation in a few hundred tokens, for long sessions or an unfamiliar memory", "A small index of the shared memory: areas, hubs, how to ask"),
    "get_node": ("any", False, "A few lines instead of a document", "One node with its parents and children, by short id (F3) or full id"),
    "delta": ("any", False, "Refresh without re-reading", "What stopped being true since your last read, plus new matches"),
    "find_path": ("any", False, "Provenance without the whole subgraph", "Shortest causal chain between two nodes"),
}


def _summary(doc: str) -> str:
    doc = " ".join((doc or "").split())
    first = doc.split(". ")[0].rstrip(".")
    return first[:220]


def catalog() -> List[Dict[str, Any]]:
    from app.mcp_embed import TOOLS
    from app.services import mcp_tools

    out = []
    for name in TOOLS:
        fn = getattr(mcp_tools, name)
        who, writes, saves, summary = META.get(name, ("any", False, "", _summary(inspect.getdoc(fn) or "")))
        args = []
        for p in inspect.signature(fn).parameters.values():
            if p.name == "ctx":
                continue
            req = p.default is inspect.Parameter.empty
            p_type = p.annotation.__name__ if hasattr(p.annotation, "__name__") else str(p.annotation)
            if p.annotation is inspect.Parameter.empty:
                p_type = "any"
            args.append({"name": p.name, "type": p_type, "required": req and p.name != "token", "default": None if req else p.default})
        out.append({"name": name, "summary": summary, "doc": inspect.getdoc(fn) or "", "args": args, "who": who, "writes": writes, "saves": saves})
    return out
