"""
PriceHistory — Pydantic schemas for historical OHLCV data.
"""

from datetime import date
from pydantic import BaseModel


class PriceHistoryResponse(BaseModel):
    """Single EOD price candle response."""
    id: int
    company_id: int
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None

    class Config:
        from_attributes = True


class HistoricalPricesWrapper(BaseModel):
    """Wrapper response for high-performance price series data."""
    ticker: str
    prices: list[PriceHistoryResponse]
    count: int
