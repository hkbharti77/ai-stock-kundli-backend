"""
Company — Pydantic schemas for company data responses.
"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, model_validator


class CompanyResponse(BaseModel):
    """Company listing response."""
    id: int
    ticker: str
    name: str
    isin: str | None
    sector: str | None
    sub_sector: str | None
    industry: str | None = None
    exchange: str | None
    market_cap: float | None
    is_active: bool
    currency: str = "INR"

    @model_validator(mode="before")
    @classmethod
    def set_industry(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("industry"):
                data["industry"] = data.get("sub_sector")
        else:
            # ORM object
            sub_sector = getattr(data, "sub_sector", None)
            setattr(data, "industry", sub_sector)
        return data

    class Config:
        from_attributes = True


class CompanySearchResponse(BaseModel):
    """Search results wrapper."""
    results: list[CompanyResponse]
    total: int
