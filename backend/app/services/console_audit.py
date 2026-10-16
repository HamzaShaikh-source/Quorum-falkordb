"""Which console and API actions count as something a person did, and how to say it. Used by the edge guard after a write succeeds."""
import re
from typing import Dict, Optional, Tuple

# (method, route template under /api) -> (tool name in the call log, what to say)
ACTIONS: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("POST", "/api/actions/stage"): ("console.stage_action", "an action"),
    ("POST", "/api/actions/{action_id}/execute"): ("console.execute_action", "an action"),
    ("POST", "/api/actions/{action_id}/approve-correction"): ("console.approve_correction", "a correction notice"),
    ("POST", "/api/handoffs/{handoff_id}/claim"): ("console.claim_handoff", "a handoff"),
    ("POST", "/api/handoffs/{handoff_id}/review"): ("console.review_handoff", "a handoff"),
    ("POST", "/api/scenario/{step}"): ("console.scenario", ""),
    ("PUT", "/api/workspace"): ("console.workspace", "changed the plan or workspace name"),
    ("POST", "/api/workspace/teams"): ("console.workspace", "added a team"),
    ("PATCH", "/api/workspace/teams/{team_id}"): ("console.workspace", "changed a team's seats"),
    ("DELETE", "/api/workspace/teams/{team_id}"): ("console.workspace", "removed a team"),
    ("POST", "/api/workspace/members"): ("console.workspace", "added a member and issued a token"),
    ("POST", "/api/workspace/members/{member_id}/rotate"): ("console.workspace", "rotated a member's token"),
    ("DELETE", "/api/workspace/members/{member_id}"): ("console.workspace", "removed a member and revoked their token"),
    ("POST", "/api/graph/restore"): ("console.restore", "a node"),
    ("POST", "/api/workspace/links"): ("console.workspace", "linked two members"),
    ("PATCH", "/api/workspace/links/{link_id}"): ("console.workspace", "paused or resumed a link"),
    ("DELETE", "/api/workspace/links/{link_id}"): ("console.workspace", "removed a link"),
}


_PATTERNS = [(m, re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", t) + "/?$"), v) for (m, t), v in ACTIONS.items()]


def describe(method: str, path: str) -> Optional[Tuple[str, str]]:
    """(tool name, what to say) for a successful write to `path`, or None when it is not something worth logging."""
    for m, rx, (tool, what) in _PATTERNS:
        if m != method:
            continue
        hit = rx.match(path)
        if not hit:
            continue
        params = hit.groupdict()
        if tool == "console.scenario":
            what = params.get("step", "").replace("_", " ")
        elif params.get("action_id") or params.get("handoff_id"):
            what = f"{what} {params.get('action_id') or params.get('handoff_id')}"
        return tool, what
    return None
