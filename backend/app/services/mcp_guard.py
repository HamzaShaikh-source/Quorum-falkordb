"""
What every MCP tool shares: who is calling (header or argument token), the rate limit, the call log, FalkorDB persistence.
"""
import contextvars
import json
import os
import time
from typing import Any, Dict, Optional, Sequence

from app.auth import resolve_token
from app.config import settings
from app.graph.queries import get_graph_store
from app.models.graph import GraphEdge, GraphNode
from app.services import workspace
from app.services.context_pack import estimate_tokens
from app.services.event_bus import event_bus
from app.services.access import audit_scopes


def emit(tenant: str, nodes: Sequence[GraphNode] = (), edges: Sequence[GraphEdge] = ()) -> None:
    """Tell connected consoles about what an agent just wrote, so MCP writes show up live like scenario writes do."""
    for n in nodes:
        event_bus.publish(tenant, "node_added", {"node": n.model_dump()})
    for e in edges:
        event_bus.publish(tenant, "edge_added", {"edge": e.model_dump()})


class AuthError(Exception):
    pass


# The token the connection proved with its Authorization header. Set per call by `guarded`; a header token wins over
# a token passed as a tool argument, and the argument stays supported for clients that cannot send headers.
_header_token: contextvars.ContextVar[str] = contextvars.ContextVar("quorum_header_token", default="")


def effective_token(arg: Any = "") -> str:
    """Header token, else the token argument, else QUORUM_MCP_TOKEN (set on a stdio server so the model never sees it)."""
    return _header_token.get() or (arg if isinstance(arg, str) else "") or os.getenv("QUORUM_MCP_TOKEN", "")


def _get_auth(token: str = "") -> Dict[str, Any]:
    info = resolve_token(effective_token(token))
    if not info:
        raise AuthError("Missing or invalid token. Send Authorization: Bearer <token> on the connection, or pass token.")
    return info


def _summary(tool: str, bound: Dict[str, Any]) -> str:
    for key in ("query", "id", "ref", "root_id", "key", "text", "label", "from_id"):
        v = bound.get(key)
        if isinstance(v, str) and v:
            if tool == "find_path":
                s = f"{bound.get('from_id')} to {bound.get('to_id')}"
                return s if len(s) <= 70 else s[:69] + "…"
            return v if len(v) <= 70 else v[:69] + "…"
    return ""


def _record(tool: str, bound: Dict[str, Any], out: Any, ms: float) -> None:
    """Every tool call becomes a row: who, what, how many tokens it cost, and what a typical retriever would have sent."""
    info = resolve_token(effective_token(bound.get("token")))
    if not info:
        return
    store = get_graph_store(info["tenant"])
    ok = not (isinstance(out, dict) and out.get("success") is False)
    tokens = out.get("tokens") if isinstance(out, dict) and isinstance(out.get("tokens"), int) else estimate_tokens(json.dumps(out, default=str))
    baseline = out.get("tokens_baseline", 0) if isinstance(out, dict) and isinstance(out.get("tokens_baseline"), int) else 0
    rec = store.record_call(
        {
            "tool": tool,
            "agent_id": info.get("agent_id"),
            "member_id": info.get("member_id"),
            "role": info["role"],
            "summary": _summary(tool, bound),
            "scope_teams": audit_scopes(store, info),
            "tokens": tokens,
            "baseline": baseline,
            "ms": round(ms, 1),
            "ok": ok,
            "error": (out.get("error") if isinstance(out, dict) and not ok else None),
        }
    )
    event_bus.publish(info["tenant"], "mcp_call", {"call": rec})


def _header_from(ctx) -> str:
    """The bearer token on the HTTP request that carries this tool call (streamable HTTP only)."""
    try:
        req = ctx.request_context.request
        h = req.headers
    except Exception:
        return ""
    auth = h.get("authorization") or ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (h.get("x-quorum-token") or "").strip()


def guarded(fn):
    """
    Wraps an MCP tool: refuses to run without FalkorDB, reads the Authorization header, rate limits per token, turns a
    bad token into a clear error, records the call, and persists the tool's writes before returning. The tool's
    signature is preserved (plus an injected `ctx`) so MCP can still describe it.
    """
    import functools
    import inspect

    from mcp.server.mcpserver import Context

    sig = inspect.signature(fn)
    ctx_param = inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Context)

    @functools.wraps(fn)
    def wrapper(*args, ctx=None, **kwargs):
        from app import db
        from app.db import FalkorUnavailable
        from app.graph.queries import sync_all
        from app.ratelimit import tool_limiter

        reset = _header_token.set(_header_from(ctx) if ctx is not None else "")
        try:
            if settings.PERSISTENCE == "falkordb":
                db.require_db()
                if os.getenv("QUORUM_STANDALONE_MCP") == "1":
                    raise RuntimeError("Standalone MCP cannot safely share a writer with the API. Use embedded MCP in the supported single-process deployment.")
            bound_args = dict(sig.bind(*args, **kwargs).arguments)
            who = effective_token(bound_args.get("token")) or "anonymous"
            ok, wait = tool_limiter.allow(who)
            if not ok:
                return {"error": f"Rate limit reached. Retry in {wait:.1f}s.", "success": False, "rate_limited": True, "retry_after": round(wait, 1)}
            t0 = time.time()
            out = fn(*args, **kwargs)
            try:
                _record(fn.__name__, bound_args, out, (time.time() - t0) * 1000)
            except AuthError:
                pass
            if settings.PERSISTENCE == "falkordb":
                sync_all()
            return out
        except AuthError as e:
            return {"error": str(e), "success": False}
        except FalkorUnavailable as e:
            raise RuntimeError(f"FALKORDB_UNAVAILABLE: {e.reason}. Start FalkorDB (docker compose up -d falkordb) and retry.")
        finally:
            _header_token.reset(reset)

    wrapper.__signature__ = sig.replace(parameters=[*sig.parameters.values(), ctx_param])
    wrapper.__annotations__ = {**getattr(fn, "__annotations__", {}), "ctx": Context}
    return wrapper



def viewer_of(auth: Dict[str, Any], store):
    return workspace.viewer_for(store, auth.get("member_id"))



def record_event(info: Dict[str, Any], tool: str, summary: str, ms: float = 0.0, ok: bool = True, error: Optional[str] = None) -> None:
    """Log something a person did through the console (or the capture hook) next to what agents did over MCP, so the audit log is complete."""
    store = get_graph_store(info["tenant"])
    rec = store.record_call(
        {
            "tool": tool,
            "agent_id": info.get("agent_id"),
            "member_id": info.get("member_id"),
            "role": info["role"],
            "summary": summary if len(summary) <= 70 else summary[:69] + "…",
            "scope_teams": audit_scopes(store, info),
            "tokens": 0,
            "baseline": 0,
            "ms": round(ms, 1),
            "ok": ok,
            "error": error,
        }
    )
    event_bus.publish(info["tenant"], "mcp_call", {"call": rec})
