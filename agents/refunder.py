from typing import List
from agents.base import BaseAgent
from agents.procedures import procedure_registry


class RefunderAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="agent_refunder",
            name="Refunder Agent",
            role="Payment and Refund Execution",
            tier="established",
            token="token_agent_refunder",
        )

    def process_refund(self, session_id: str, label: str, parent_ids: List[str], idempotency_key: str):
        # Select best procedure via Laplace smoothing
        proc = procedure_registry.get_best_procedure("refund")
        procedure_id = proc.id if proc else "proc_refund_standard"

        stage_res = self.call_tool("stage_action", {
            "label": label,
            "action_type": "refund",
            "reversibility": "COMPENSABLE",
            "parent_ids": parent_ids,
            "procedure_id": procedure_id,
            "idempotency_key": idempotency_key,
        })

        action_id = stage_res.get("action_id")
        if not action_id or stage_res.get("gate") == "BLOCKED":
            self.record_message(session_id, f"Refund action blocked or failed to stage: {label}")
            return stage_res

        self.record_message(session_id, f"Executing refund {action_id}")
        exec_res = self.call_tool("execute_action", {"id": action_id})
        return exec_res
