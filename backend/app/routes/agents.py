from typing import List
from fastapi import APIRouter, Depends
from app.auth import get_auth_context, AuthContext
from app.models.agents import AgentItem, AgentResumeResponse, AgentMemoryItem
from app.models.common import Status
from app.services.fixtures import get_fixture_agents

router = APIRouter()


@router.get("/agents", response_model=List[AgentItem])
def list_agents(auth: AuthContext = Depends(get_auth_context)):
    from app.config import settings

    return get_fixture_agents() if settings.DEMO else []


@router.get("/agents/{agent_id}/resume", response_model=AgentResumeResponse)
def resume_agent(agent_id: str, auth: AuthContext = Depends(get_auth_context)):
    return AgentResumeResponse(
        session_id=f"sess_{agent_id}_001",
        summary="Session resumed: Ingested partner webhook and flagged refund inquiry for order 1042.",
        memories=[
            AgentMemoryItem(
                fact_id="fact_30d",
                text="Refund window is 30 days.",
                source_ref="tenant_acme:commons:src_policy_v3",
                quote="Refunds are allowed within 30 days.",
            ),
            AgentMemoryItem(
                fact_id="fact_90d",
                text="Refund window is 90 days (quarantined).",
                source_ref="tenant_acme:agent_scout:raw:msg_004",
                quote="Partner policy states 90 day coverage.",
                status=Status.QUARANTINED,
            ),
        ],
    )
