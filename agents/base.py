import logging
from typing import Dict, Any, List, Optional
from agents.session import private_memory

logger = logging.getLogger("quorum.agent")


class BaseAgent:
    """
    Base Quorum Agent.
    Features:
    - MCP tool dispatch.
    - Context Epoch refresh: automatically rebuilds context if epoch changed.
    - Procedural memory integration.
    """
    def __init__(self, agent_id: str, name: str, role: str, tier: str = "new", token: str = "token_agent_scout"):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.tier = tier
        self.token = token
        self.last_seen_epoch: int = 1
        self.working_context: List[Dict[str, Any]] = []

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches tool call to mcp_tools service and verifies epoch freshness."""
        from app.services import mcp_tools

        tool_map = {
            "remember_fact": mcp_tools.remember_fact,
            "derive": mcp_tools.derive,
            "retrieve_context": mcp_tools.retrieve_context,
            "stage_action": mcp_tools.stage_action,
            "execute_action": mcp_tools.execute_action,
            "claim_handoff": mcp_tools.claim_handoff,
        }

        func = tool_map.get(tool_name)
        if not func:
            return {"error": f"Unknown tool '{tool_name}'", "success": False}

        # Inject token if not present
        if "token" not in arguments:
            arguments["token"] = self.token

        result = func(**arguments)  # type: ignore[operator]  # the tool_map values are the guarded MCP tools

        # Epoch check: if epoch bumped, rebuild context!
        returned_epoch = result.get("epoch", self.last_seen_epoch)
        if returned_epoch > self.last_seen_epoch:
            logger.info(f"Agent [{self.agent_id}] detected epoch shift ({self.last_seen_epoch} -> {returned_epoch}). Rebuilding context...")
            self.last_seen_epoch = returned_epoch
            self.refresh_context()

        return result

    def refresh_context(self, query: str = "refund policy"):
        ctx = self.call_tool("retrieve_context", {"query": query, "k": 5})
        self.working_context = ctx.get("context", [])

    def record_message(self, session_id: str, text: str, source_ref: Optional[str] = None, quote: Optional[str] = None):
        private_memory.add_message(
            session_id=session_id,
            agent_id=self.agent_id,
            text=text,
            sender=self.agent_id,
            source_ref=source_ref,
            quote=quote,
        )
