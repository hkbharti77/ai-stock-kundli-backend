"""
IntradayService — Manages 5-minute intraday market data ingestion, indicator calculation, and live updates.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Dict, List, Set
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import anyio
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.models.company import Company
from app.models.intraday_price import IntradayPrice
from app.core.websocket import manager

logger = logging.getLogger("IntradayService")

# Keep track of tickers that are actively being viewed via WebSockets
active_websocket_tickers: Set[str] = set()


class IntradayService:
    @classmethod
    def is_market_hours(cls) -> bool:
        """
        Helper to check if current time is within market hours.
        For Indian markets: 9:00 AM to 3:45 PM IST.
        For US markets: 9:30 AM to 4:00 PM EST.
        To support 24/7 testing and development, we default to True or checking time.
        We'll make it return True in development/testing mode so it always runs.
        """
        # Return True for development ease so it runs continuously in development environment
        return True

    @classmethod
    async def fetch_intraday_data(cls, company: Company) -> List[dict]:
        """
        Fetches 5-minute intraday price bars for the given company.
        Tries to use yfinance as a reliable out-of-the-box fallback.
        """
        ticker_symbol = company.ticker
        if company.exchange in ("NSE", "NSI"):
            ticker_symbol = f"{company.ticker}.NS"
        elif company.exchange in ("BSE", "BOM"):
            ticker_symbol = f"{company.ticker}.BO"

        def _fetch():
            try:
                ticker = yf.Ticker(ticker_symbol)
                # Fetch 5-minute data for the last 1 day (or 5 days if 1 day has too few bars)
                df = ticker.history(interval="5m", period="1d")
                if df.empty or len(df) < 5:
                    df = ticker.history(interval="5m", period="5d")
                return df
            except Exception as e:
                logger.error(f"Error fetching yfinance intraday for {ticker_symbol}: {e}")
                return pd.DataFrame()

        df = await anyio.to_thread.run_sync(_fetch)
        if df.empty:
            return []

        # Convert index to datetime and sort
        df = df.reset_index()
        if "Datetime" in df.columns:
            df["timestamp"] = df["Datetime"]
        elif "Date" in df.columns:
            df["timestamp"] = df["Date"]
        else:
            logger.warning(f"No Datetime/Date column in yfinance output for {company.ticker}")
            return []

        # Clean columns
        df = df.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume"
        })

        # Keep only required columns
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Drop rows with NaN in critical columns
        df = df.dropna(subset=["open", "high", "low", "close"])

        # Compute Technical Indicators (RSI, VWAP)
        if len(df) >= 14:
            # RSI (14)
            df["rsi"] = ta.rsi(df["close"], length=14)
        else:
            df["rsi"] = 50.0 # Default fallback

        # VWAP calculation (Volume-Weighted Average Price resetting daily)
        # We group by date of the timestamp and calculate cumulative VWAP for each session
        df["date"] = df["timestamp"].dt.date
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3.0
        df["pv"] = df["typical_price"] * df["volume"]
        
        df["cum_pv"] = df.groupby("date")["pv"].cumsum()
        df["cum_vol"] = df.groupby("date")["volume"].cumsum()
        df["vwap"] = df["cum_pv"] / df["cum_vol"].replace(0, 1)

        # Fill NaNs
        df = df.fillna(0.0)

        # Convert back to list of dicts
        candles = []
        for _, row in df.iterrows():
            # Convert timestamp to timezone-naive datetime to store in DB cleanly
            ts = row["timestamp"].to_pydatetime()
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)

            candles.append({
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "rsi": float(row["rsi"]),
                "vwap": float(row["vwap"])
            })

        return candles

    @classmethod
    async def ingest_intraday_for_company(cls, db: Session, company: Company) -> List[dict]:
        """
        Fetches, computes indicators, and upserts 5-minute candles for a company.
        Broadcasts the latest candle update via WebSockets.
        """
        logger.info(f"Ingesting intraday 5m data for {company.ticker}...")
        candles = await cls.fetch_intraday_data(company)
        if not candles:
            logger.warning(f"No intraday data fetched for {company.ticker}")
            return []

        # Find existing records to update
        timestamps = [c["timestamp"] for c in candles]
        min_ts = min(timestamps)
        max_ts = max(timestamps)

        stmt = select(IntradayPrice).where(
            IntradayPrice.company_id == company.id,
            IntradayPrice.timestamp >= min_ts,
            IntradayPrice.timestamp <= max_ts
        )
        
        def _get_existing():
            return {p.timestamp: p for p in db.execute(stmt).scalars().all()}

        existing = await anyio.to_thread.run_sync(_get_existing)

        new_records = 0
        updated_records = 0

        latest_candle = None

        for candle in candles:
            ts = candle["timestamp"]
            if ts in existing:
                # Update
                record = existing[ts]
                record.open = candle["open"]
                record.high = candle["high"]
                record.low = candle["low"]
                record.close = candle["close"]
                record.volume = candle["volume"]
                record.rsi = candle["rsi"]
                record.vwap = candle["vwap"]
                updated_records += 1
                latest_candle = record
            else:
                # Create new
                new_record = IntradayPrice(
                    company_id=company.id,
                    timestamp=ts,
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                    rsi=candle["rsi"],
                    vwap=candle["vwap"]
                )
                db.add(new_record)
                new_records += 1
                latest_candle = new_record

        def _commit():
            db.commit()

        await anyio.to_thread.run_sync(_commit)
        logger.info(f"Intraday ingest for {company.ticker} completed: {new_records} new, {updated_records} updated.")

        # Broadcast the latest price candle to websocket channels
        if latest_candle:
            ws_message = {
                "type": "price_update",
                "ticker": company.ticker,
                "timestamp": latest_candle.timestamp.isoformat(),
                "open": float(latest_candle.open),
                "high": float(latest_candle.high),
                "low": float(latest_candle.low),
                "close": float(latest_candle.close),
                "volume": int(latest_candle.volume),
                "rsi": float(latest_candle.rsi) if latest_candle.rsi else 50.0,
                "vwap": float(latest_candle.vwap) if latest_candle.vwap else float(latest_candle.close),
            }
            # Broadcast to all users connected to the platform
            await manager.broadcast(ws_message)

        return candles


async def run_intraday_ticker_loop():
    """
    Infinite background loop that fetches intraday updates every 5 minutes during market hours
    for active or watched/websocket-subscribed stocks.
    """
    logger.info("Starting background Intraday Ingestion Loop...")
    from app.core.database import SessionLocal

    while True:
        try:
            if IntradayService.is_market_hours():
                db = SessionLocal()
                try:
                    # 1. Identify companies to update:
                    # - Top 15 active companies by market cap
                    # - Plus any ticker in active_websocket_tickers
                    stmt_top = select(Company).where(Company.is_active == True).order_by(desc(Company.market_cap)).limit(15)
                    top_companies = db.execute(stmt_top).scalars().all()
                    
                    companies_to_update = {c.ticker: c for c in top_companies}
                    
                    if active_websocket_tickers:
                        stmt_ws = select(Company).where(Company.ticker.in_(list(active_websocket_tickers)))
                        ws_companies = db.execute(stmt_ws).scalars().all()
                        for c in ws_companies:
                            companies_to_update[c.ticker] = c
                    
                    logger.info(f"[Intraday Loop] Ingesting {len(companies_to_update)} active/watched tickers: {list(companies_to_update.keys())}")
                    
                    for ticker, company in companies_to_update.items():
                        try:
                            await IntradayService.ingest_intraday_for_company(db, company)
                        except Exception as e:
                            logger.error(f"Failed to ingest intraday for {ticker} in background loop: {e}")
                        # Sleep briefly between tickers to avoid rate limiting
                        await asyncio.sleep(2.0)
                finally:
                    db.close()
            else:
                logger.info("[Intraday Loop] Outside market hours, sleeping...")
        except Exception as ex:
            logger.error(f"[Intraday Loop Error] Exception in background loop: {ex}")
            
        # Sleep for 5 minutes (300 seconds) before next tick
        await asyncio.sleep(300)
