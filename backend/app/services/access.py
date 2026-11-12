"""Shared non-disclosing node and handoff access policy. Service credentials remain unscoped."""
from fastapi import HTTPException

from app.services.workspace import viewer_for


def node_for(store, auth, node_id):
    member_id = auth.get("member_id") if isinstance(auth, dict) else auth.member_id
    node = store.nodes.get(node_id)
    if node is None or not viewer_for(store, member_id).can_see(node):
        raise HTTPException(404, {"error": {"code": "NOT_FOUND", "message": f"Node '{node_id}' not found."}})
    return node


def handoff_visible(store, viewer, handoff):
    if not viewer.scoped:
        return True
    if not handoff.context_root_id:
        return False  # historical handoffs without provenance are system-only
    node = store.nodes.get(handoff.context_root_id)
    return node is not None and viewer.can_see(node)


def inherited_scopes(parents):
    return sorted({s for p in parents for s in [*p.scope_teams, p.scope_team] if s})


def call_visible(store, viewer, call):
    if not viewer.scoped:
        return True
    # Older audit records lack sufficient provenance to disclose safely to members.
    if "scope_teams" not in call:
        return False
    allowed = {viewer.team_id} | viewer.linked_teams
    if not set(call["scope_teams"]) <= allowed:
        return False
    member = next((m for m in store.workspace["members"] if m["id"] == call.get("member_id")), None)
    return member is not None and member["team_id"] in allowed


def audit_scopes(store, info):
    # Console summaries and failed tool arguments can refer to any context the actor
    # had access to. Conservatively retain those scopes with the audit record.
    viewer = viewer_for(store, info.get("member_id"))
    return inherited_scopes([n for n in store.nodes.values() if viewer.can_see(n)])


def creation_matches(store, existing, candidate, parents):
    """A retry may return current state; it may never replace it or re-run a guard."""
    if existing is None:
        return False
    fields = ("kind", "label", "text", "key", "value", "quote", "origin", "asserted_by", "source_ref",
              "scope_team", "scope_teams", "owner_team", "is_decision")
    if any(getattr(existing, key) != getattr(candidate, key) for key in fields):
        return False
    if bool(existing.action) != bool(candidate.action):
        return False
    if existing.action and any(getattr(existing.action, key) != getattr(candidate.action, key)
                               for key in ("type", "reversibility", "procedure_id", "idempotency_key", "payload")):
        return False
    return {p.id for p in store.get_parents(existing.id, ["LEADS_TO"])} == {p.id for p in parents}
