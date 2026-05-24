import logging
from datetime import datetime, date
from typing import Dict, Any, List
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import anyio
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.company import Company
from app.models.price_history import PriceHistory
from app.models.agent_output import AgentOutput
from app.services.llm import LLMService
from app.core.cache import cache

logger = logging.getLogger("TechnicalAnalystAgent")

class TechnicalAnalystAgent:
    @classmethod
    async def get_nifty_prices(cls) -> pd.DataFrame:
        """
        Fetches last 1-year daily Nifty 50 (^NSEI) EOD prices.
        Caches in Redis for 4 hours to avoid API throttling.
        """
        cache_key = "prices:nifty_50_1y"
        try:
            cached = await cache.get(cache_key)
            if cached and "data" in cached:
                df = pd.DataFrame(cached["data"])
                df['date'] = pd.to_datetime(df['date']).dt.date
                return df
        except Exception as e:
            logger.warning(f"Cache lookup failed for Nifty: {e}")

        # Fetch from yfinance
        try:
            ticker = yf.Ticker("^NSEI")
            df_yf = await anyio.to_thread.run_sync(lambda: ticker.history(period="1y"))
            if not df_yf.empty:
                df_yf = df_yf.reset_index()
                df_yf['date'] = df_yf['Date'].dt.date
                
                # Prepare JSON serializable structure for caching
                data = []
                for _, r in df_yf.iterrows():
                    data.append({
                        "date": r['date'].isoformat(),
                        "Close": float(r['Close'])
                    })
                
                try:
                    await cache.set(cache_key, {"data": data}, ttl_seconds=14400) # 4 hours
                except Exception as cache_err:
                    logger.warning(f"Failed to cache Nifty 50: {cache_err}")

                df_yf['Date'] = pd.to_datetime(df_yf['Date']).dt.date
                return df_yf.rename(columns={'Date': 'date'})
        except Exception as e:
            logger.error(f"Failed to fetch Nifty 50 prices: {e}")

        return pd.DataFrame()

    @classmethod
    def compute_technical_indicators(cls, prices: List[PriceHistory], nifty_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calculates all required technical indicators:
        - SMA (20, 50, 200), EMA (9, 21), VWAP
        - Bollinger Bands (20, 2), ATR (14)
        - RSI (14), MACD (12, 26, 9), OBV
        - Support/Resistance lines & Stop Loss
        - Volume spikes and stock vs Nifty 50 relative strength (RS) ratio.
        """
        if not prices:
            return {"data": [], "supports": [], "resistances": [], "stop_loss_zone": 0.0}

        # Convert to Pandas DataFrame
        data = []
        for p in prices:
            data.append({
                "date": p.date,
                "open": float(p.open) if p.open is not None else 0.0,
                "high": float(p.high) if p.high is not None else 0.0,
                "low": float(p.low) if p.low is not None else 0.0,
                "close": float(p.close) if p.close is not None else 0.0,
                "volume": int(p.volume) if p.volume is not None else 0,
            })
        
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date']).dt.date
        df = df.sort_values('date').reset_index(drop=True)

        if len(df) < 5:
            # Fallback if too few records
            return {"data": [], "supports": [], "resistances": [], "stop_loss_zone": 0.0}

        # ── 1. Trend Indicators ──────────────────
        df['sma_20'] = ta.sma(df['close'], length=20)
        df['sma_50'] = ta.sma(df['close'], length=50)
        df['sma_200'] = ta.sma(df['close'], length=200)
        df['ema_9'] = ta.ema(df['close'], length=9)
        df['ema_21'] = ta.ema(df['close'], length=21)
        
        # Safe VWAP calculation: session cumulative or standard rolling
        # For EOD price history, simple cumulative daily volume-weighted price works great:
        df['vwap'] = (df['volume'] * (df['high'] + df['low'] + df['close']) / 3).cumsum() / df['volume'].cumsum().replace(0, 1)

        # ── 2. Volatility Indicators ──────────────
        bbands = ta.bbands(df['close'], length=20, std=2)
        if bbands is not None and not bbands.empty:
            upper_col = next((c for c in bbands.columns if c.startswith('BBU_')), None)
            middle_col = next((c for c in bbands.columns if c.startswith('BBM_')), None)
            lower_col = next((c for c in bbands.columns if c.startswith('BBL_')), None)
            
            if upper_col and middle_col and lower_col:
                df['bb_upper'] = bbands[upper_col]
                df['bb_middle'] = bbands[middle_col]
                df['bb_lower'] = bbands[lower_col]
            else:
                try:
                    df['bb_upper'] = bbands['BBU_20_2.0']
                    df['bb_middle'] = bbands['BBM_20_2.0']
                    df['bb_lower'] = bbands['BBL_20_2.0']
                except KeyError:
                    try:
                        df['bb_upper'] = bbands['BBU_20_2']
                        df['bb_middle'] = bbands['BBM_20_2']
                        df['bb_lower'] = bbands['BBL_20_2']
                    except KeyError:
                        df['bb_upper'] = df['close'] * 1.05
                        df['bb_middle'] = df['close']
                        df['bb_lower'] = df['close'] * 0.95
        else:
            df['bb_upper'] = df['close'] * 1.05
            df['bb_middle'] = df['close']
            df['bb_lower'] = df['close'] * 0.95

        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

        # ── 3. Momentum & Volume Indicators ───────
        df['rsi_14'] = ta.rsi(df['close'], length=14)
        macd_df = ta.macd(df['close'], fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            macd_col = next((c for c in macd_df.columns if c.startswith('MACD_')), None)
            signal_col = next((c for c in macd_df.columns if c.startswith('MACDs_')), None)
            hist_col = next((c for c in macd_df.columns if c.startswith('MACDh_')), None)
            
            if macd_col and signal_col and hist_col:
                df['macd'] = macd_df[macd_col]
                df['macd_signal'] = macd_df[signal_col]
                df['macd_hist'] = macd_df[hist_col]
            else:
                df['macd'] = macd_df.iloc[:, 0]
                df['macd_signal'] = macd_df.iloc[:, 1]
                df['macd_hist'] = macd_df.iloc[:, 2]
        else:
            df['macd'] = 0.0
            df['macd_signal'] = 0.0
            df['macd_hist'] = 0.0

        df['obv'] = ta.obv(df['close'], df['volume'])

        # ── 4. Volume Spike Detection ─────────────
        df['volume_avg_20'] = df['volume'].rolling(window=20).mean()
        df['is_volume_spike'] = df.apply(
            lambda r: bool(r['volume'] > 2 * r['volume_avg_20']) if pd.notnull(r['volume_avg_20']) else False,
            axis=1
        )

        # ── 5. Relative Strength against Nifty 50 ──
        df['rs_ratio'] = 1.0
        if not nifty_df.empty:
            merged = pd.merge(df, nifty_df, on='date', how='left', suffixes=('', '_nifty'))
            # Forward fill missing Nifty close values in case of misalignment
            merged['Close'] = merged['Close'].ffill().bfill()
            merged['rs_ratio'] = merged['close'] / merged['Close'].replace(0, 1)
            df['rs_ratio'] = merged['rs_ratio']

        # Clean NaN values so Pydantic/JSON doesn't choke on them
        df = df.fillna(0.0)

        # ── 6. Support & Resistance Calculations ───
        levels = cls.calculate_support_resistance_zones(df)
        latest_close = float(df['close'].iloc[-1])
        stop_loss_zone = round(latest_close * 0.88, 2)

        # Build list of items
        items = []
        for _, row in df.iterrows():
            items.append({
                "date": row['date'],
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": int(row['volume']),
                "sma_20": float(row['sma_20']),
                "sma_50": float(row['sma_50']),
                "sma_200": float(row['sma_200']),
                "ema_9": float(row['ema_9']),
                "ema_21": float(row['ema_21']),
                "vwap": float(row['vwap']),
                "bb_upper": float(row['bb_upper']),
                "bb_middle": float(row['bb_middle']),
                "bb_lower": float(row['bb_lower']),
                "atr": float(row['atr']),
                "rsi_14": float(row['rsi_14']),
                "macd": float(row['macd']),
                "macd_signal": float(row['macd_signal']),
                "macd_hist": float(row['macd_hist']),
                "obv": float(row['obv']),
                "volume_avg_20": float(row['volume_avg_20']),
                "is_volume_spike": bool(row['is_volume_spike']),
                "rs_ratio": float(row['rs_ratio'])
            })

        return {
            "data": items,
            "supports": levels["supports"],
            "resistances": levels["resistances"],
            "stop_loss_zone": stop_loss_zone
        }

    @classmethod
    def calculate_support_resistance_zones(cls, df: pd.DataFrame, window: int = 10) -> Dict[str, List[float]]:
        """
        Identifies local peaks & troughs and clusters them into key support/resistance zones.
        """
        if len(df) < window * 2:
            return {"supports": [], "resistances": []}

        latest_price = float(df['close'].iloc[-1])
        
        levels = []
        for i in range(window, len(df) - window):
            is_peak = True
            is_trough = True
            for j in range(1, window + 1):
                if df['high'].iloc[i] < df['high'].iloc[i-j] or df['high'].iloc[i] < df['high'].iloc[i+j]:
                    is_peak = False
                if df['low'].iloc[i] > df['low'].iloc[i-j] or df['low'].iloc[i] > df['low'].iloc[i+j]:
                    is_trough = False
                    
            if is_peak:
                levels.append((float(df['high'].iloc[i]), 'resistance'))
            if is_trough:
                levels.append((float(df['low'].iloc[i]), 'support'))
                
        # Group levels within 1.5% tolerance
        tolerance = latest_price * 0.015
        clusters = []
        for price, l_type in levels:
            found_cluster = False
            for c in clusters:
                if abs(c['price_sum'] / c['count'] - price) <= tolerance:
                    c['price_sum'] += price
                    c['count'] += 1
                    c['types'].append(l_type)
                    found_cluster = True
                    break
            if not found_cluster:
                clusters.append({
                    'price_sum': price,
                    'count': 1,
                    'types': [l_type]
                })
                
        # Average and sort clusters by intensity
        scored_levels = []
        for c in clusters:
            avg_price = c['price_sum'] / c['count']
            scored_levels.append((avg_price, c['count']))
            
        scored_levels.sort(key=lambda x: x[1], reverse=True)
        
        supports = []
        resistances = []
        
        for level, _ in scored_levels:
            if level < latest_price:
                supports.append(level)
            elif level > latest_price:
                resistances.append(level)
                
        supports.sort(reverse=True)
        resistances.sort()
        
        return {
            "supports": [round(s, 2) for s in supports[:3]],
            "resistances": [round(r, 2) for r in resistances[:3]]
        }

    @classmethod
    def detect_candlestick_patterns(cls, df_list: List[Dict[str, Any]]) -> List[str]:
        """
        Applies mathematical criteria to identify hammer, doji, engulfing, star candlestick patterns.
        """
        if len(df_list) < 5:
            return []
        
        patterns = []
        # Check last 5 candles for active pattern signals
        for i in range(len(df_list) - 5, len(df_list)):
            if i < 1:
                continue
            row = df_list[i]
            prev = df_list[i-1]
            
            body = abs(row['close'] - row['open'])
            candle_range = row['high'] - row['low'] if (row['high'] - row['low']) > 0 else 0.001
            
            # Doji
            if body <= 0.1 * candle_range:
                patterns.append(f"Doji forming on {row['date'].strftime('%d %b')} — signals key trend indecision.")
                
            # Hammer
            lower_shadow = min(row['open'], row['close']) - row['low']
            upper_shadow = row['high'] - max(row['open'], row['close'])
            if lower_shadow >= 2 * body and upper_shadow <= 0.1 * candle_range and body > 0:
                patterns.append(f"Bullish Hammer on {row['date'].strftime('%d %b')} — bottom reversal candidate.")
                
            # Engulfing Patterns
            prev_body = abs(prev['close'] - prev['open'])
            if prev_body > 0:
                # Bullish Engulfing
                if prev['close'] < prev['open'] and row['close'] > row['open'] and row['open'] <= prev['close'] and row['close'] >= prev['open']:
                    patterns.append(f"Bullish Engulfing pattern on {row['date'].strftime('%d %b')} — reversal buying signal.")
                # Bearish Engulfing
                if prev['close'] > prev['open'] and row['close'] < row['open'] and row['open'] >= prev['close'] and row['close'] <= prev['open']:
                    patterns.append(f"Bearish Engulfing pattern on {row['date'].strftime('%d %b')} — major distribution pressure.")
                    
        return list(set(patterns))

    @classmethod
    async def analyze_company(cls, db: Session, company: Company) -> AgentOutput:
        """
        Performs the complete Technical Analysis Agent flow.
        Calculates indicators, detects patterns, prompts LLM (or fallbacks) and saves in DB.
        """
        logger.info(f"Starting technical analysis agent for {company.name} ({company.ticker})")

        # 1. Fetch daily price history
        stmt = select(PriceHistory).where(PriceHistory.company_id == company.id).order_by(PriceHistory.date)
        prices = list(db.execute(stmt).scalars().all())

        if not prices:
            logger.warning(f"No price history found for {company.ticker}. Creating simulated defaults.")
            raise ValueError(f"No historical price data found in database for ticker {company.ticker}")

        # 2. Get Nifty prices & calculate indicators
        nifty_df = await cls.get_nifty_prices()
        results = cls.compute_technical_indicators(prices, nifty_df)
        
        data_list = results["data"]
        latest_close = data_list[-1]["close"] if data_list else 100.0

        # Detect candlestick patterns
        active_patterns = cls.detect_candlestick_patterns(data_list)

        # 3. Create a clean dict summary of computed statistics for LLM context
        latest = data_list[-1] if data_list else {}
        summary = {
            "close_price": float(latest_close),
            "rsi_14": float(latest.get("rsi_14", 50.0)),
            "macd": float(latest.get("macd", 0.0)),
            "macd_signal": float(latest.get("macd_signal", 0.0)),
            "macd_hist": float(latest.get("macd_hist", 0.0)),
            "sma_20": float(latest.get("sma_20", latest_close)),
            "sma_50": float(latest.get("sma_50", latest_close)),
            "sma_200": float(latest.get("sma_200", latest_close)),
            "ema_9": float(latest.get("ema_9", latest_close)),
            "ema_21": float(latest.get("ema_21", latest_close)),
            "vwap": float(latest.get("vwap", latest_close)),
            "bb_upper": float(latest.get("bb_upper", latest_close * 1.05)),
            "bb_lower": float(latest.get("bb_lower", latest_close * 0.95)),
            "is_volume_spike": bool(latest.get("is_volume_spike", False)),
            "rs_ratio": float(latest.get("rs_ratio", 1.0)),
            "active_patterns": active_patterns,
            "supports": results["supports"],
            "resistances": results["resistances"]
        }

        # 4. Trigger LLM Analysis
        analysis_data = await LLMService.generate_technical_analysis(
            ticker=company.ticker,
            company_name=company.name,
            summary=summary
        )

        # 5. Save or update Technical Agent output in DB
        stmt_out = select(AgentOutput).where(
            AgentOutput.company_id == company.id,
            AgentOutput.agent_type == "technical_analyst"
        )
        existing_output = db.execute(stmt_out).scalar_one_or_none()

        score = int(analysis_data.get("score", 60))
        confidence = int(analysis_data.get("confidence", 90))
        trend = str(analysis_data.get("trend", "neutral"))
        strengths = list(analysis_data.get("strengths", []))
        concerns = list(analysis_data.get("concerns", []))
        reasoning = str(analysis_data.get("reasoning", ""))

        if existing_output:
            logger.info(f"Updating existing technical analysis output for {company.ticker}")
            existing_output.score = score
            existing_output.confidence = confidence
            existing_output.trend = trend
            existing_output.strengths = strengths
            existing_output.concerns = concerns
            existing_output.reasoning = reasoning
            existing_output.updated_at = datetime.utcnow()
            agent_output = existing_output
        else:
            logger.info(f"Creating new technical analysis output for {company.ticker}")
            agent_output = AgentOutput(
                company_id=company.id,
                agent_type="technical_analyst",
                score=score,
                confidence=confidence,
                trend=trend,
                strengths=strengths,
                concerns=concerns,
                reasoning=reasoning,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(agent_output)

        try:
            db.commit()
            db.refresh(agent_output)
            logger.info(f"Successfully saved Technical Analyst agent output for {company.ticker}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to commit technical agent output: {e}")
            raise e

        return agent_output
