"""
TechnicalAnalysis — Pydantic schemas for technical analysis and timeseries indicator responses.
"""

from datetime import date
from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class TechnicalIndicatorItem(BaseModel):
    """Timeseries bar containing OHLCV price and all computed indicators."""
    date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[int]
    
    # Trend Indicators
    sma_20: Optional[float]
    sma_50: Optional[float]
    sma_200: Optional[float]
    ema_9: Optional[float]
    ema_21: Optional[float]
    vwap: Optional[float]
    
    # Volatility Indicators
    bb_upper: Optional[float]
    bb_middle: Optional[float]
    bb_lower: Optional[float]
    atr: Optional[float]
    
    # Momentum & Volume Indicators
    rsi_14: Optional[float]
    macd: Optional[float]
    macd_signal: Optional[float]
    macd_hist: Optional[float]
    obv: Optional[float]
    
    # Extras
    volume_avg_20: Optional[float]
    is_volume_spike: bool
    rs_ratio: Optional[float]

    class Config:
        from_attributes = True


class TechnicalIndicatorsWrapper(BaseModel):
    """Wrapper response for high-performance interactive charting with technical indicators."""
    ticker: str
    support_levels: List[float]
    resistance_levels: List[float]
    stop_loss_zone: float
    data: List[TechnicalIndicatorItem]
    count: int

    class Config:
        from_attributes = True
