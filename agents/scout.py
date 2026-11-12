from agents.base import BaseAgent


class ScoutAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="agent_scout",
            name="Scout Agent",
            role="Webhook and Chat Ingestion",
            tier="new",
            token="token_agent_scout",
        )

    def ingest_message(self, session_id: str, key: str, value: str, text: str, origin: str):
        self.record_message(session_id, f"Received payload: {text}", source_ref=f"raw:{origin}")
        return self.call_tool("remember_fact", {
            "key": key,
            "value": value,
            "text": text,
            "origin": origin,
        })
