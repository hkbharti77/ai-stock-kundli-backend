"""
Watchlist — Pydantic schemas for watchlist requests and responses.
"""

from datetime import datetime
from pydantic import BaseModel
from app.schemas.company import CompanyResponse


class WatchlistCreate(BaseModel):
    """Add a company to watchlist request body."""
    ticker: str


class WatchlistResponse(BaseModel):
    """Watchlist item response."""
    id: int
    user_id: int
    company_id: int
    created_at: datetime
    company: CompanyResponse
    latest_score: int = 70
    latest_signal: str = "Buy"

    class Config:
        from_attributes = True
