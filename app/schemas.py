from pydantic import BaseModel, ConfigDict
from datetime import datetime
from app.triage import Severity


class CallBase(BaseModel):
    call_sid: str
    severity: Severity
    condition: str
    duration_seconds: int
    created_at: datetime


class CallLogResponse(CallBase):
     id: int
     model_config= ConfigDict(from_attributes=True)