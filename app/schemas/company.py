"""
Company — Pydantic schemas for company data responses.
"""

from datetime import datetime
from pydantic import BaseModel


class CompanyResponse(BaseModel):
    """Company listing response."""
    id: int
    ticker: str
    name: str
    isin: str | None
    sector: str | None
    sub_sector: str | None
    exchange: str | None
    market_cap: float | None
    is_active: bool

    class Config:
        from_attributes = True


class CompanySearchResponse(BaseModel):
    """Search results wrapper."""
    results: list[CompanyResponse]
    total: int
