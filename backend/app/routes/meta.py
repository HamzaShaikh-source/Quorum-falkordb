from fastapi import APIRouter, Depends
from app.auth import get_auth_context, AuthContext
from app.config import settings
from app.models.meta import MetaResponse
from app.services.event_bus import event_bus

router = APIRouter()


@router.get("/meta", response_model=MetaResponse)
def get_meta(auth: AuthContext = Depends(get_auth_context)):
    buf = event_bus.get_tenant_buffer(auth.tenant)
    return MetaResponse(
        contract_version=settings.CONTRACT_VERSION,
        mode=settings.get_effective_mode(),
        tenant=auth.tenant,
        role=auth.role,
        epoch=buf.current_epoch,
        seq=buf.current_seq,
        replay_available=settings.REPLAY_AVAILABLE,
    )
