from pydantic import BaseModel
from typing import Literal


class MetaResponse(BaseModel):
    contract_version: str
    mode: Literal["replay", "live"]
    tenant: str
    role: str
    epoch: int
    seq: int
    replay_available: bool = True
