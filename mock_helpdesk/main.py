"""
Mock Helpdesk API Service
Simulates external third-party helpdesk & payment systems with strict idempotency and TOMBSTONE semantics.

Rule: A compensation for an unseen idempotency key records a TOMBSTONE.
Any later execute call with that key is rejected (409 Conflict), preventing late-arriving executions from landing.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional

app = FastAPI(title="Quorum Mock Helpdesk")

# State stores
tombstones = set()
executed_actions: Dict[str, Dict[str, Any]] = {}
compensations: Dict[str, Dict[str, Any]] = {}


class ExecuteRequest(BaseModel):
    idempotency_key: str
    action_type: str
    amount: Optional[float] = None
    customer_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class CompensateRequest(BaseModel):
    idempotency_key: str
    reason: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/helpdesk/execute")
def execute_action(req: ExecuteRequest):
    # TOMBSTONE Check: if compensation arrived first, reject late execute!
    if req.idempotency_key in tombstones:
        raise HTTPException(
            status_code=409,
            detail=f"Action with idempotency_key {req.idempotency_key} has been tombstoned by compensation."
        )
    
    if req.idempotency_key in executed_actions:
        return {"status": "already_executed", "record": executed_actions[req.idempotency_key]}
    
    record = {
        "idempotency_key": req.idempotency_key,
        "action_type": req.action_type,
        "status": "SUCCESS",
    }
    executed_actions[req.idempotency_key] = record
    return {"status": "executed", "record": record}


@app.post("/api/helpdesk/compensate")
def compensate_action(req: CompensateRequest):
    # If the action has never been seen, set a TOMBSTONE to block future executes
    if req.idempotency_key not in executed_actions:
        tombstones.add(req.idempotency_key)
        return {"status": "tombstoned_unseen_key", "idempotency_key": req.idempotency_key}
    
    record = {
        "idempotency_key": req.idempotency_key,
        "status": "COMPENSATED",
        "reason": req.reason
    }
    compensations[req.idempotency_key] = record
    return {"status": "compensated", "record": record}
