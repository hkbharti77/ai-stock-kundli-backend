"""
Backtest API Endpoint — Exposes historical strategy replay simulator to the frontend dashboard.
"""

import logging
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from app.core.security import get_current_user_id
from app.core.database import SessionLocal
from app.services.backtesting import BacktestingEngine

logger = logging.getLogger("app.api.backtest")
router = APIRouter(prefix="/backtest", tags=["Backtesting"])

# -----------------
# Pydantic Schemas
# -----------------
class BacktestRequest(BaseModel):
    tickers: List[str] = Field(..., description="List of stock tickers to simulate in portfolio.")
    start_date: str = Field("2025-05-30", description="Start date in YYYY-MM-DD format.")
    end_date: str = Field("2026-05-30", description="End date in YYYY-MM-DD format.")
    starting_balance: Optional[float] = Field(10000.0, description="Initial investment capital.")
    strategy_type: Optional[str] = Field("signal_following", description="Strategy: 'signal_following' or 'buy_and_hold'.")

class BacktestSummary(BaseModel):
    starting_balance: float
    final_balance: float
    total_return_pct: float
    benchmark_return_pct: float
    cagr_pct: float
    benchmark_cagr_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int
    win_rate_pct: float

class EquityCurvePoint(BaseModel):
    date: str
    portfolio: float
    benchmark: float

class TradeLogPoint(BaseModel):
    date: str
    ticker: str
    action: str
    price: float
    shares: float
    value: float
    profit: float

class BacktestResponse(BaseModel):
    summary: BacktestSummary
    equity_curve: List[EquityCurvePoint]
    trades: List[TradeLogPoint]

# -----------------
# API Routes
# -----------------
@router.post("/run", response_model=BacktestResponse)
def run_strategy_backtest(
    payload: BacktestRequest,
    user_id: int = Depends(get_current_user_id)
):
    """
    Executes a historical signal replay backtest for a portfolio of tickers.
    Calculates returns, drawdown, Sharpe, and provides daily data points for plotting.
    """
    # Parse dates
    try:
        start_dt = datetime.strptime(payload.start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(payload.end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="Start date must be before end date.")

    # Validate tickers
    if not payload.tickers:
        raise HTTPException(status_code=400, detail="List of tickers cannot be empty.")

    # Run backtest
    with SessionLocal() as db:
        try:
            result = BacktestingEngine.run_backtest(
                db=db,
                tickers=payload.tickers,
                start_date=start_dt,
                end_date=end_dt,
                starting_balance=payload.starting_balance or 10000.0,
                strategy_type=payload.strategy_type or "signal_following"
            )
            return result
        except ValueError as val_err:
            raise HTTPException(status_code=404, detail=str(val_err))
        except Exception as e:
            logger.error(f"Unexpected backtest engine error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="An error occurred running the backtest simulation.")
