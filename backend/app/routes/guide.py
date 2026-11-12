"""The use guide as data: the console renders it, and the chat tool and the docs read the same file."""
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.auth import AuthContext, get_auth_context
from app.services import guide

router = APIRouter()


@router.get("/guide")
def get_guide(auth: AuthContext = Depends(get_auth_context)) -> Dict[str, Any]:
    g = guide.load()
    return {"intro": g["intro"], "topics": g["topics"], "rules": g["rules"], "clients": g["clients"], "hooks": g["hooks"]}
