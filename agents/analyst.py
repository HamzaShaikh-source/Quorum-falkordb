from typing import List
from agents.base import BaseAgent


class AnalystAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="agent_analyst",
            name="Analyst Agent",
            role="Policy and Claim Reasoning",
            tier="established",
            token="token_agent_analyst",
        )

    def analyze_eligibility(self, session_id: str, claim_text: str, parent_ids: List[str], decision: bool = True):
        self.record_message(session_id, f"Derived conclusion: {claim_text}")
        return self.call_tool("derive", {
            "text": claim_text,
            "parent_ids": parent_ids,
            "is_decision": decision,
            "owner_team": "Billing Tier 1",
        })
