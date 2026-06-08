"""
Backtesting Engine Service — Simulates historical agent signal execution, trades, and portfolio returns.
"""

import logging
from datetime import datetime, date
from typing import List, Dict, Any, Tuple
import math
from sqlalchemy.orm import Session
from app.models.company import Company
from app.models.price_history import PriceHistory

logger = logging.getLogger("app.backtesting")

class BacktestingEngine:
    @classmethod
    def run_backtest(
        cls,
        db: Session,
        tickers: List[str],
        start_date: date,
        end_date: date,
        starting_balance: float = 10000.0,
        strategy_type: str = "signal_following"
    ) -> Dict[str, Any]:
        """
        Executes a historical signal replay backtest.
        Returns:
            - summary: Dict of key KPIs (Total Return, CAGR, Sharpe, Drawdown, etc.)
            - equity_curve: List of daily portfolio values vs benchmark
            - trades: List of executed trade logs
        """
        logger.info(f"Running backtest for {tickers} from {start_date} to {end_date} (capital: {starting_balance})")

        # 1. Fetch companies and their daily price history
        companies = db.query(Company).filter(Company.ticker.in_(tickers), Company.is_active == True).all()
        if not companies:
            raise ValueError("No active companies found matching the provided tickers.")

        # Cache price histories by company ID
        company_prices: Dict[int, List[PriceHistory]] = {}
        all_dates_set = set()

        for comp in companies:
            prices = db.query(PriceHistory).filter(
                PriceHistory.company_id == comp.id,
                PriceHistory.date >= start_date,
                PriceHistory.date <= end_date
            ).order_by(PriceHistory.date.asc()).all()
            
            if prices:
                company_prices[comp.id] = prices
                for p in prices:
                    all_dates_set.add(p.date)

        if not all_dates_set:
            raise ValueError("No price history found for the specified period.")

        sorted_dates = sorted(list(all_dates_set))
        
        # Filter out companies with no price history to prevent KeyErrors and allocate capital correctly
        companies = [comp for comp in companies if comp.id in company_prices]
        
        # 2. Simulate historical signals (Buy/Sell/Hold) per stock dynamically
        # To make it realistic, we calculate technical indicators on a rolling basis
        signals: Dict[int, Dict[date, str]] = {}
        for comp in companies:
            prices = company_prices.get(comp.id, [])
            signals[comp.id] = {}
            if not prices:
                continue

            # Precalculate simple moving averages
            close_prices = [float(p.close) for p in prices]
            for idx, p in enumerate(prices):
                # Simple SMAs for dynamic signal replay
                sma_20 = sum(close_prices[max(0, idx-19):idx+1]) / min(idx+1, 20)
                sma_50 = sum(close_prices[max(0, idx-49):idx+1]) / min(idx+1, 50)
                
                # Dynamic RSI simulation helper
                rsi = 50.0
                if idx >= 14:
                    gains, losses = 0.0, 0.0
                    for k in range(idx-13, idx+1):
                        diff = close_prices[k] - close_prices[k-1]
                        if diff > 0:
                            gains += diff
                        else:
                            losses -= diff
                    rs = (gains / 14) / ((losses / 14) or 1.0)
                    rsi = 100.0 - (100.0 / (1.0 + rs))

                # Replay Signal Rules
                curr_price = float(p.close)
                if curr_price > sma_20 and curr_price > sma_50 and 40 <= rsi <= 68:
                    sig = "BUY"
                elif curr_price < sma_20 or rsi >= 75:
                    sig = "SELL"
                else:
                    sig = "HOLD"
                
                signals[comp.id][p.date] = sig

        # 3. Simulate Trading Execution
        # Each ticker is allocated an equal portion of starting balance
        allocation = starting_balance / len(companies)
        
        # State tracking per company:
        # - shares: float
        # - cash_balance: float (unused cash for this position)
        # - is_invested: bool
        portfolio_state: Dict[int, Dict[str, Any]] = {}
        for comp in companies:
            portfolio_state[comp.id] = {
                "shares": 0.0,
                "cash": allocation,
                "invested": False,
                "last_buy_price": 0.0
            }

        trade_logs = []
        equity_curve = []
        
        # Daily simulation loop
        for d in sorted_dates:
            daily_stock_val = 0.0
            daily_cash_val = 0.0
            
            for comp in companies:
                state = portfolio_state[comp.id]
                # Get daily price
                day_price_obj = next((p for p in company_prices.get(comp.id, []) if p.date == d), None)
                if not day_price_obj:
                    # Keep same state, add cash value
                    daily_cash_val += state["cash"]
                    continue

                price = float(day_price_obj.close)
                sig = signals[comp.id].get(d, "HOLD")
                
                # Strategy logic
                if strategy_type == "signal_following":
                    if sig == "BUY" and not state["invested"] and state["cash"] > 0:
                        # BUY trade
                        shares_bought = state["cash"] / price
                        state["shares"] = shares_bought
                        state["cash"] = 0.0
                        state["invested"] = True
                        state["last_buy_price"] = price
                        
                        trade_logs.append({
                            "date": d.strftime("%Y-%m-%d"),
                            "ticker": comp.ticker,
                            "action": "BUY",
                            "price": price,
                            "shares": round(shares_bought, 4),
                            "value": round(shares_bought * price, 2),
                            "profit": 0.0
                        })
                    elif sig == "SELL" and state["invested"] and state["shares"] > 0:
                        # SELL trade
                        cash_received = state["shares"] * price
                        shares_sold = state["shares"]
                        profit = cash_received - (shares_sold * state["last_buy_price"])
                        
                        state["cash"] = cash_received
                        state["shares"] = 0.0
                        state["invested"] = False
                        
                        trade_logs.append({
                            "date": d.strftime("%Y-%m-%d"),
                            "ticker": comp.ticker,
                            "action": "SELL",
                            "price": price,
                            "shares": round(shares_sold, 4),
                            "value": round(cash_received, 2),
                            "profit": round(profit, 2)
                        })
                else: # Buy & Hold benchmark strategy
                    if not state["invested"] and state["cash"] > 0:
                        shares_bought = state["cash"] / price
                        state["shares"] = shares_bought
                        state["cash"] = 0.0
                        state["invested"] = True
                        state["last_buy_price"] = price
                
                # Add to total daily valuation
                daily_stock_val += state["shares"] * price
                daily_cash_val += state["cash"]

            total_value = daily_stock_val + daily_cash_val
            
            # Simple Benchmark Simulation: equally weighted index of the selected stocks holding starting capital
            benchmark_val = starting_balance
            bench_stock_val = 0.0
            for comp in companies:
                first_price = float(company_prices[comp.id][0].close)
                day_price_obj = next((p for p in company_prices.get(comp.id, []) if p.date == d), None)
                curr_price = float(day_price_obj.close) if day_price_obj else first_price
                bench_stock_val += (allocation / first_price) * curr_price
                
            equity_curve.append({
                "date": d.strftime("%Y-%m-%d"),
                "portfolio": round(total_value, 2),
                "benchmark": round(bench_stock_val, 2)
            })

        # 4. Compute Performance Metrics (KPIs)
        final_value = equity_curve[-1]["portfolio"]
        bench_final_value = equity_curve[-1]["benchmark"]
        
        total_return = ((final_value - starting_balance) / starting_balance) * 100.0
        bench_return = ((bench_final_value - starting_balance) / starting_balance) * 100.0
        
        # CAGR calculation
        days = (sorted_dates[-1] - sorted_dates[0]).days
        years = max(days / 365.25, 0.01)
        cagr = ((final_value / starting_balance) ** (1.0 / years) - 1.0) * 100.0
        bench_cagr = ((bench_final_value / starting_balance) ** (1.0 / years) - 1.0) * 100.0
        
        # Volatility & Sharpe Ratio
        daily_returns = []
        for i in range(1, len(equity_curve)):
            ret = (equity_curve[i]["portfolio"] - equity_curve[i-1]["portfolio"]) / equity_curve[i-1]["portfolio"]
            daily_returns.append(ret)
            
        sharpe = 0.0
        ann_vol = 0.0
        if daily_returns:
            n = len(daily_returns)
            mean_ret = sum(daily_returns) / n
            variance = sum((r - mean_ret) ** 2 for r in daily_returns) / max(n - 1, 1)
            ann_vol = math.sqrt(variance) * math.sqrt(252)
            
            # Risk free rate assumption of 5% (0.05)
            excess_return = (cagr / 100.0) - 0.05
            if ann_vol > 0:
                sharpe = excess_return / ann_vol

        # Max Drawdown
        max_drawdown = 0.0
        peak = starting_balance
        for pt in equity_curve:
            val = pt["portfolio"]
            if val > peak:
                peak = val
            drawdown = (peak - val) / peak * 100.0
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # Win Rate on closed trades
        sell_trades = [t for t in trade_logs if t["action"] == "SELL"]
        win_trades = [t for t in sell_trades if t["profit"] > 0]
        win_rate = (len(win_trades) / len(sell_trades) * 100.0) if sell_trades else 0.0

        summary = {
            "starting_balance": round(starting_balance, 2),
            "final_balance": round(final_value, 2),
            "total_return_pct": round(total_return, 2),
            "benchmark_return_pct": round(bench_return, 2),
            "cagr_pct": round(cagr, 2),
            "benchmark_cagr_pct": round(bench_cagr, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "total_trades": len(trade_logs),
            "win_rate_pct": round(win_rate, 2)
        }

        return {
            "summary": summary,
            "equity_curve": equity_curve,
            "trades": trade_logs
        }
