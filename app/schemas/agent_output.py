"""
AgentOutput — Pydantic schemas for agent analysis responses.
"""

from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional


class AgentOutputResponse(BaseModel):
    id: int
    company_id: int
    agent_type: str
    score: int
    confidence: int
    trend: Optional[str]
    strengths: Optional[List[str]]
    concerns: Optional[List[str]]
    reasoning: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
