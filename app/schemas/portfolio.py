from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.schemas.company import CompanyResponse

# DB column is Numeric(12, 4) → max absolute value is 99,999,999.9999
_MAX_SHARES = 99_999_999.0
# DB column is Numeric(12, 2) → max absolute value is 9,999,999,999.99
_MAX_PRICE  = 9_999_999_999.0

class PortfolioHoldingCreate(BaseModel):
    ticker: str
    shares: float
    average_price: float

    @field_validator("shares")
    @classmethod
    def validate_shares(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Shares must be a positive number.")
        if v > _MAX_SHARES:
            raise ValueError(
                f"Shares value {v:,.2f} exceeds the maximum allowed ({_MAX_SHARES:,.0f}). "
                "Please enter a realistic quantity."
            )
        return v

    @field_validator("average_price")
    @classmethod
    def validate_average_price(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Average price must be a positive number.")
        if v > _MAX_PRICE:
            raise ValueError(
                f"Price value {v:,.2f} exceeds the maximum allowed ({_MAX_PRICE:,.0f})."
            )
        return v

class PortfolioHoldingUpdate(BaseModel):
    shares: float
    average_price: float

    @field_validator("shares")
    @classmethod
    def validate_shares(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Shares must be a positive number.")
        if v > _MAX_SHARES:
            raise ValueError(
                f"Shares value {v:,.2f} exceeds the maximum allowed ({_MAX_SHARES:,.0f}). "
                "Please enter a realistic quantity."
            )
        return v

    @field_validator("average_price")
    @classmethod
    def validate_average_price(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Average price must be a positive number.")
        if v > _MAX_PRICE:
            raise ValueError(
                f"Price value {v:,.2f} exceeds the maximum allowed ({_MAX_PRICE:,.0f})."
            )
        return v

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

_MAX_CAPITAL    = 1_000_000_000.0   # ₹100 Cr
_MIN_CALC_CAP   = 1_000.0           # ₹1,000 (position sizing)
_MIN_BUILD_CAP  = 10_000.0          # ₹10,000 (builder)
_MAX_PRICE_VAL  = 100_000.0         # ₹1,00,000
_VALID_PROFILES = {"conservative", "moderate", "aggressive"}
_VALID_HORIZONS = {"Short-term", "Medium-term", "Long-term"}

class PositionSizeRequest(BaseModel):
    ticker: str
    total_capital: float
    risk_profile: str  # conservative, moderate, aggressive
    stop_loss_pct: float
    take_profit_pct: float
    manual_price: Optional[float] = None

    @field_validator("total_capital")
    @classmethod
    def validate_capital(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("total_capital must be a positive number.")
        if v < _MIN_CALC_CAP:
            raise ValueError(f"Minimum capital is ₹{_MIN_CALC_CAP:,.0f}.")
        if v > _MAX_CAPITAL:
            raise ValueError(f"Maximum capital is ₹{_MAX_CAPITAL:,.0f} (₹100 Cr).")
        return v

    @field_validator("risk_profile")
    @classmethod
    def validate_risk_profile(cls, v: str) -> str:
        if v.lower() not in _VALID_PROFILES:
            raise ValueError(f"risk_profile must be one of: {', '.join(_VALID_PROFILES)}.")
        return v.lower()

    @field_validator("stop_loss_pct")
    @classmethod
    def validate_stop_loss(cls, v: float) -> float:
        if v <= 0 or v > 50:
            raise ValueError("stop_loss_pct must be between 0.1 and 50.")
        return v

    @field_validator("take_profit_pct")
    @classmethod
    def validate_take_profit(cls, v: float) -> float:
        if v <= 0 or v > 500:
            raise ValueError("take_profit_pct must be between 0.1 and 500.")
        return v

    @field_validator("manual_price")
    @classmethod
    def validate_manual_price(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            if v <= 0:
                raise ValueError("manual_price must be a positive number.")
            if v > _MAX_PRICE_VAL:
                raise ValueError(f"manual_price cannot exceed ₹{_MAX_PRICE_VAL:,.0f}.")
        return v


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

    @field_validator("total_capital")
    @classmethod
    def validate_capital(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("total_capital must be a positive number.")
        if v < _MIN_BUILD_CAP:
            raise ValueError(f"Minimum capital for portfolio builder is ₹{_MIN_BUILD_CAP:,.0f}.")
        if v > _MAX_CAPITAL:
            raise ValueError(f"Maximum capital is ₹{_MAX_CAPITAL:,.0f} (₹100 Cr).")
        return v

    @field_validator("risk_profile")
    @classmethod
    def validate_risk_profile(cls, v: str) -> str:
        if v.lower() not in _VALID_PROFILES:
            raise ValueError(f"risk_profile must be one of: {', '.join(_VALID_PROFILES)}.")
        return v.lower()

    @field_validator("horizon")
    @classmethod
    def validate_horizon(cls, v: str) -> str:
        if v not in _VALID_HORIZONS:
            raise ValueError(f"horizon must be one of: {', '.join(_VALID_HORIZONS)}.")
        return v

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

