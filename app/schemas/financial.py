"""
Financial — Pydantic schemas for financial data response.
"""

from datetime import date, datetime
from pydantic import BaseModel


class FinancialResponse(BaseModel):
    """Financial period statement response."""
    id: int
    company_id: int
    period_type: str
    period_end: date
    
    # Profitability
    revenue: float | None
    gross_profit: float | None
    ebitda: float | None
    pat: float | None
    eps: float | None
    
    # Returns
    roe: float | None
    roce: float | None
    
    # Leverage & Liquidity
    debt_equity: float | None
    current_ratio: float | None
    
    # Cash Flow
    operating_cash_flow: float | None
    free_cash_flow: float | None
    
    created_at: datetime

    class Config:
        from_attributes = True


class CompanyFinancialsWrapper(BaseModel):
    """Wrapper response separating annual and quarterly financials."""
    ticker: str
    annual: list[FinancialResponse]
    quarterly: list[FinancialResponse]
