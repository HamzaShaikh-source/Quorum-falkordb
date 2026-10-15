from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import db
from app.config import settings

router = APIRouter()


@router.get("/health")
def health():
    """No auth. The console polls this to decide between the product and the 'FalkorDB is not set up' screen."""
    payload = db.health_payload()
    payload["contract_version"] = settings.CONTRACT_VERSION
    payload["env"] = settings.ENV
    payload["demo"] = settings.DEMO
    payload["warnings"] = []
    if settings.PERSISTENCE == "memory":
        payload["warnings"].append("PERSISTENCE=memory: data is lost on restart. For tests only.")
    if settings.ENV == "production" and settings.dynamic_tokens_allowed():
        payload["warnings"].append("Dynamic tokens are enabled in production. Set QUORUM_ALLOW_DYNAMIC_TOKENS=false.")
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)
