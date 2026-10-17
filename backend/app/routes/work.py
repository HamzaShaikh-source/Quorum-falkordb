"""REST door for announce_work, so a Claude Code PreToolUse hook (which cannot speak MCP) can warn before two agents collide."""
from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import AuthContext, get_auth_context
from app.services import mcp_coord

router = APIRouter()


class AnnounceIn(BaseModel):
    target: str = Field(..., min_length=1, max_length=300)
    intent: str = Field("", max_length=200)
    minutes: int = Field(30, ge=1, le=1440)
    done: bool = False


@router.post("/work/announce")
def announce(body: AnnounceIn, auth: AuthContext = Depends(get_auth_context)) -> Dict[str, Any]:
    return mcp_coord.announce_work(body.target, body.intent, token=auth.token, minutes=body.minutes, done=body.done)
