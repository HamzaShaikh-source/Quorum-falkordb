from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from app.services.llm import llm_adapter


class MessageNode(BaseModel):
    id: str
    text: str
    ts: int
    sender: str
    source_ref: Optional[str] = None
    quote: Optional[str] = None


class SessionNode(BaseModel):
    id: str
    agent_id: str
    created_at: int
    summary: Optional[str] = None
    messages: List[MessageNode] = []


class PrivateAgentMemory:
    """
    Manages private raw graphs: tenant_<id>:agent_<n>:raw.
    NEXT-linked messages and session resumption.
    """
    def __init__(self, tenant: str = "acme"):
        self.tenant = tenant
        self.sessions: Dict[str, SessionNode] = {}

    def get_or_create_session(self, session_id: str, agent_id: str) -> SessionNode:
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionNode(
                id=session_id,
                agent_id=agent_id,
                created_at=0,  # Fixed base for deterministic replay
                summary=None,
                messages=[],
            )
        return self.sessions[session_id]

    def add_message(
        self,
        session_id: str,
        agent_id: str,
        text: str,
        sender: str = "agent",
        source_ref: Optional[str] = None,
        quote: Optional[str] = None,
    ) -> MessageNode:
        session = self.get_or_create_session(session_id, agent_id)
        msg_id = f"msg_{session_id}_{len(session.messages) + 1}"
        msg = MessageNode(
            id=msg_id,
            text=text,
            ts=len(session.messages) * 1000,  # Monotonic relative offset
            sender=sender,
            source_ref=source_ref,
            quote=quote,
        )
        session.messages.append(msg)
        return msg

    def end_session(self, session_id: str, cached_summary: Optional[str] = None) -> str:
        session = self.sessions.get(session_id)
        if not session:
            return "No active session."

        summary = llm_adapter.generate_session_summary(
            session_id=session.id,
            messages=[m.model_dump() for m in session.messages],
            cached_summary=cached_summary,
        )
        session.summary = summary
        return summary

    def resume(self, session_id: str) -> Dict[str, Any]:
        """
        Two-query resume flow:
        Query 1: raw graph (Session and NEXT messages).
        Query 2: commons graph via source_ref pointers.
        """
        session = self.sessions.get(session_id)
        if not session:
            return {
                "session_id": session_id,
                "summary": "Session not found.",
                "memories": [],
            }

        summary = session.summary or self.end_session(session_id)

        memories = []
        for m in session.messages:
            if m.source_ref:
                memories.append({
                    "message_id": m.id,
                    "text": m.text,
                    "source_ref": m.source_ref,
                    "quote": m.quote,
                })

        return {
            "session_id": session.id,
            "agent_id": session.agent_id,
            "summary": summary,
            "message_count": len(session.messages),
            "memories": memories,
        }


private_memory = PrivateAgentMemory()
