
from fastapi import APIRouter, Depends

from app.auth import AuthContext, get_auth_context
from app.config import settings
from app.services import embeddings, mcp_catalog

router = APIRouter()


@router.get("/mcp/catalog")
def mcp_catalog_route(auth: AuthContext = Depends(get_auth_context)):
    """The tools this hub serves, read from the code, plus how to reach it."""
    return {
        "tools": mcp_catalog.catalog(),
        "public_url": settings.PUBLIC_URL or None,
        "header_required": settings.mcp_header_required(),
        "rate_limit_per_min": {"tools": settings.RATE_LIMIT_TOOLS_PER_MIN, "http": settings.RATE_LIMIT_HTTP_PER_MIN},
        "embedding_provider": embeddings.provider_name(),
    }

