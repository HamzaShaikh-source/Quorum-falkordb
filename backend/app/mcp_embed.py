"""
Optional: serve the MCP tools from the API process itself (set QUORUM_EMBED_MCP=1).

Why: the API and a standalone MCP server each hold their own in-memory graph, so what agents write over MCP never shows
up in the console. Embedding puts both on one graph and one event bus. Standalone MCP writers are unsupported with persistence; stdio clients use mcp/bridge.py to reach the single hub.
"""
from typing import Optional

CORE_TOOLS = (
    "remember_fact",
    "derive",
    "retrieve_context",
    "stage_action",
    "execute_action",
    "claim_handoff",
    "quarantine",
    "restore",
    "explain_blast_radius",
    "review_handoff",
    "get_map",
    "get_node",
    "find_path",
    "delta",
    "observe",
)

# The chat layer: onboarding, status, team management and connecting hubs from inside the AI chat.
CHAT_TOOLS = (
    "quorum_help",
    "quorum_start",
    "quorum_status",
    "quorum_manage",
    "quorum_discover",
    "quorum_connect",
    "quorum_inbox",
    "quorum_respond",
    "quorum_connections",
    "peer_retrieve",
)


def active_tools():
    """QUORUM_TOOL_PROFILE=core lists only the 15 memory tools (fewest tokens in the client's prompt)."""
    from app.config import settings

    return CORE_TOOLS if settings.TOOL_PROFILE == "core" else CORE_TOOLS + CHAT_TOOLS


TOOLS = CORE_TOOLS + CHAT_TOOLS


def build_mcp_app():
    """Returns the streamable-HTTP ASGI app, or None when the `mcp` package (2.x) is not installed."""
    try:
        from mcp.server.mcpserver import MCPServer
    except Exception:
        return None
    from app.services import mcp_tools

    server = MCPServer("quorum")
    for name in active_tools():
        server.tool()(mcp_tools.guarded(getattr(mcp_tools, name)))
    try:
        from mcp.server.transport_security import TransportSecuritySettings
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        return server.streamable_http_app(transport_security=security)
    except Exception:
        return server.streamable_http_app()


def mount(app, mcp_app) -> Optional[object]:
    """Adds the MCP routes (/mcp) to the FastAPI app. The caller runs mcp_app's lifespan."""
    if mcp_app is None:
        return None
    app.router.routes.extend(mcp_app.routes)
    return mcp_app
