from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.schemas.company import CompanyResponse

class PortfolioHoldingCreate(BaseModel):
    ticker: str
    shares: float
    average_price: float

class PortfolioHoldingUpdate(BaseModel):
    shares: float
    average_price: float

class PortfolioHoldingResponse(BaseModel):
    id: int
    user_id: int
    company_id: int
    shares: float
    average_price: float
    created_at: datetime
    company: CompanyResponse
    
    # Dynamic values computed on retrieval
    current_price: float
    current_value: float
    total_cost: float
    pnl: float
    pnl_percentage: float

    model_config = ConfigDict(from_attributes=True)

class SectorAllocation(BaseModel):
    sector: str
    value: float
    percentage: float

class StockCorrelation(BaseModel):
    ticker1: str
    ticker2: str
    correlation: float

class FitEvaluation(BaseModel):
    ticker: str
    fit_score: int
    recommendation: str
    reasons: List[str]
    sector: str
    current_weight: float
    prospective_weight: float

class PortfolioAnalysisResponse(BaseModel):
    total_value: float
    total_cost: float
    total_pnl: float
    total_pnl_percentage: float
    risk_score: float
    diversification_score: float
    concentration_risk: str
    sector_allocations: List[SectorAllocation]
    correlations: List[StockCorrelation]
    correlation_alerts: List[str]
    ai_advisor_report: str


# --- Sprint 19-20 Position Sizing & Portfolio Builder Schemas ---

class PositionSizeRequest(BaseModel):
    ticker: str
    total_capital: float
    risk_profile: str  # conservative, moderate, aggressive
    stop_loss_pct: float
    take_profit_pct: float
    manual_price: Optional[float] = None

class PositionSizeResponse(BaseModel):
    ticker: str
    company_name: str
    risk_profile: str
    entry_price: float
    win_probability: float
    reward_risk_ratio: float
    kelly_fraction: float
    suggested_allocation_amt: float
    suggested_allocation_pct: float
    suggested_shares: float
    stop_loss_pct: float
    take_profit_pct: float
    stop_loss_price: float
    take_profit_price: float
    max_capital_risk_pct_allowed: float
    max_capital_risk_amt_allowed: float
    actual_capital_risk_amt: float
    actual_capital_risk_pct: float
    normal_drawdown_scenario: float
    worst_case_drawdown_scenario: float
    extreme_drawdown_scenario: float

class PortfolioBuilderRequest(BaseModel):
    total_capital: float
    risk_profile: str  # conservative, moderate, aggressive
    horizon: str       # Short-term, Medium-term, Long-term
    preferences: Optional[List[str]] = None  # selected preferred sectors

class BuilderHoldingRecommendation(BaseModel):
    ticker: str
    company_name: str
    sector: str
    price: float
    allocation_pct: float
    allocation_amt: float
    shares: float
    suggested_stop_loss_pct: float
    suggested_take_profit_pct: float
    stop_loss_price: float
    take_profit_price: float
    capital_at_risk_amt: float
    capital_at_risk_pct: float
    composite_score: float

class PortfolioBuilderResponse(BaseModel):
    total_capital: float
    investable_capital: float
    cash_reserve_amt: float
    cash_reserve_pct: float
    holdings: List[BuilderHoldingRecommendation]
    portfolio_max_drawdown_amt: float
    portfolio_max_drawdown_pct: float
    worst_case_drawdown_amt: float
    worst_case_drawdown_pct: float
    extreme_drawdown_amt: float
    extreme_drawdown_pct: float

