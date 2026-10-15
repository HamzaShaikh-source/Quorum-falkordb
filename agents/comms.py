from typing import List
from agents.base import BaseAgent
from agents.procedures import procedure_registry


class CommsAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="agent_comms",
            name="Comms Agent",
            role="Customer Notifications and External Email",
            tier="new",
            token="token_agent_comms",
        )

    def send_customer_email(self, session_id: str, label: str, parent_ids: List[str], idempotency_key: str):
        proc = procedure_registry.get_best_procedure("notify")
        procedure_id = proc.id if proc else "proc_notify_customer"

        # Staging an IRREVERSIBLE action -> Quorum Gate will evaluate
        stage_res = self.call_tool("stage_action", {
            "label": label,
            "action_type": "email_customer",
            "reversibility": "IRREVERSIBLE",
            "parent_ids": parent_ids,
            "procedure_id": procedure_id,
            "idempotency_key": idempotency_key,
        })

        if stage_res.get("gate") == "BLOCKED":
            self.record_message(session_id, f"Customer email held for human review: {stage_res.get('blocked_reason')}")
            return stage_res

        action_id = stage_res.get("action_id")
        exec_res = self.call_tool("execute_action", {"id": action_id})
        return exec_res
