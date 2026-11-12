"""
Permission levels, in plain words. The stored role names (agent, auditor, human) stay as they are because tokens and the
API use them; people see Contributor, Reviewer and Admin. This table is what the Team page shows, and a test keeps it equal
to what the code really enforces.
"""
from typing import Any, Dict, List

LEVELS: Dict[str, Dict[str, str]] = {
    "agent": {
        "name": "Contributor",
        "summary": "Reads the memory, saves facts, proposes and runs actions. Cannot quarantine or restore, and cannot approve reviews.",
    },
    "auditor": {
        "name": "Reviewer",
        "summary": "Everything a Contributor can do, plus quarantine and restore facts. Cannot approve reviews or manage people.",
    },
    "human": {
        "name": "Admin",
        "summary": "Everything: also approves reviews and manages people, teams, links, plan and tokens.",
    },
}

# True = yes, False = no, "limited" = yes with a restriction (see `note`)
CAPABILITIES: List[Dict[str, Any]] = [
    {"key": "read", "label": "Look things up", "detail": "retrieve_context, get_map, get_node, find_path, delta, blast-radius preview", "agent": True, "auditor": True, "human": True},
    {"key": "write", "label": "Save facts and conclusions", "detail": "remember_fact, observe, derive", "agent": True, "auditor": True, "human": True},
    {"key": "act", "label": "Stage and run actions", "detail": "stage_action, execute_action (the quorum gate still decides)", "agent": True, "auditor": True, "human": True},
    {"key": "claim", "label": "Claim handoffs", "detail": "claim_handoff", "agent": "limited", "auditor": True, "human": True, "note": "Contributors can claim tasks only, not reviews or escalations."},
    {"key": "quarantine", "label": "Quarantine and restore facts", "detail": "quarantine, restore", "agent": False, "auditor": True, "human": True},
    {"key": "review", "label": "Approve or reject reviews", "detail": "review_handoff", "agent": False, "auditor": False, "human": True},
    {"key": "manage", "label": "Manage people, teams, links, plan and tokens", "detail": "the Team page, /api/workspace, and quorum_manage, quorum_connect, quorum_respond in chat", "agent": False, "auditor": False, "human": True},
]

ORDER = ("agent", "auditor", "human")


def level_name(role: str) -> str:
    return LEVELS.get(role, {}).get("name", role)


def needs(*allowed: str) -> str:
    """'Reviewer or Admin' for ('auditor', 'human')."""
    return " or ".join(level_name(r) for r in ORDER if r in allowed)


def refusal(who: str, role: str, action: str, *allowed: str) -> str:
    """The sentence a refused agent (and the activity log) shows: what was refused, why, and who can fix it."""
    return f"{who} is a {level_name(role)} and cannot {action}. That needs {needs(*allowed)}. An admin can change this person's level on the Team page."


def catalog() -> Dict[str, Any]:
    return {"levels": [{"role": r, **LEVELS[r]} for r in ORDER], "capabilities": CAPABILITIES}
