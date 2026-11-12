from typing import List
from fastapi import APIRouter, Depends
from app.auth import get_auth_context, AuthContext
from app.models.agents import ProcedureItem
from agents.procedures import procedure_registry

router = APIRouter()


@router.get("/procedures", response_model=List[ProcedureItem])
def list_procedures(auth: AuthContext = Depends(get_auth_context)):
    procs = procedure_registry.list_procedures()
    return [
        ProcedureItem(
            id=p.id,
            task_type=p.task_type,
            steps=p.steps,
            uses=p.uses,
            successes=p.successes,
            failures=p.failures,
            rank_score=p.rank_score,
            seeded=p.seeded,
        )
        for p in procs
    ]

