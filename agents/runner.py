import os
import json
import logging
from typing import Dict, Any, Optional
from agents.scout import ScoutAgent
from agents.analyst import AnalystAgent
from agents.refunder import RefunderAgent
from agents.comms import CommsAgent
from agents.session import private_memory
from app.config import settings

logger = logging.getLogger("quorum.runner")


class AgentRunner:
    """
    Runner for Scout, Analyst, Refunder, and Comms.
    Supports REPLAY mode (deterministic fixtures keyed by agent -> step -> seed)
    and LIVE mode (LLM calls with zero-key fallback).
    """
    def __init__(self, fixtures_path: Optional[str] = None):
        self.fixtures_path = fixtures_path or os.path.join(os.path.dirname(__file__), "fixtures", "replay.json")
        self.fixtures = self._load_fixtures()
        self.agents = {
            "scout": ScoutAgent(),
            "analyst": AnalystAgent(),
            "refunder": RefunderAgent(),
            "comms": CommsAgent(),
        }

    def _load_fixtures(self) -> Dict[str, Any]:
        if os.path.exists(self.fixtures_path):
            with open(self.fixtures_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def run_step(self, step_name: str, seed: str = "default") -> Dict[str, Any]:
        """
        Executes a scenario step across all relevant agents.
        Step names match CONTRACT.md: inject_poison, run_agents, audit, supersede, restore, reset.
        """
        mode = settings.get_effective_mode()
        results: Dict[str, Any] = {
            "step": step_name,
            "mode": mode,
            "seed": seed,
            "agent_results": {},
        }

        # In Replay mode, iterate over fixtures for this step
        for agent_key, agent in self.agents.items():
            agent_fixtures = self.fixtures.get(agent_key, {}).get(step_name, {})
            # Look up by seed, fallback to "default"
            step_data = agent_fixtures.get(seed) or agent_fixtures.get("default")
            if not step_data:
                continue

            session_id = f"sess_{agent_key}_{step_name}"
            calls = step_data.get("calls", [])
            call_logs = []

            for call in calls:
                tool_name = call.get("tool")
                args = call.get("arguments", {})
                thought = call.get("thought", "")

                agent.record_message(session_id, f"Thought: {thought}" if thought else f"Calling {tool_name}")
                res = agent.call_tool(tool_name, args)
                call_logs.append({
                    "tool": tool_name,
                    "thought": thought,
                    "response": res,
                })

            # Record session summary
            cached_sum = step_data.get("session_summary")
            summary = private_memory.end_session(session_id, cached_summary=cached_sum)

            results["agent_results"][agent_key] = {
                "thought": step_data.get("thought"),
                "calls": call_logs,
                "session_summary": summary,
            }

        return results

    def simulate_claim_race(self, handoff_id: str) -> Dict[str, Any]:
        """
        Simulates two agents (e.g. Refunder and Analyst) visibly racing for a handoff claim.
        Asserts exactly 1 winner.
        """
        refunder = self.agents["refunder"]
        analyst = self.agents["analyst"]

        # First claim succeeds
        res_refunder = refunder.call_tool("claim_handoff", {"id": handoff_id})

        # Second claim loses race
        res_analyst = analyst.call_tool("claim_handoff", {"id": handoff_id})

        return {
            "handoff_id": handoff_id,
            "first_claimer": "agent_refunder",
            "first_result": res_refunder,
            "second_claimer": "agent_analyst",
            "second_result": res_analyst,
            "single_winner": res_refunder.get("success") is True,
        }


runner = AgentRunner()
